"""MCP 服务配置的 PostgreSQL 存储。

存放管理员在「AI 扩展管理」页维护的 MCP 服务连接参数（``ai_mcp_server``）。
与 ``app.persistence``（会话消息）同款可靠性语义：配置是管理端显式写入的数据，
读写失败向上抛由路由层转业务错误，不静默降级。Skill 技能不在此处——它是磁盘上的
标准技能包（``app.skill_store``，``skills/<name>/SKILL.md``）。
"""

import asyncio
import json
import logging
import time
from typing import List, Optional

from psycopg_pool import ConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)

# 建表失败后的冷却时间（与 persistence 同款）：PG 未就绪时避免每个请求都叠加建表超时。
_TABLES_RETRY_COOLDOWN = 30.0

_pool: Optional[ConnectionPool] = None
_tables_ready = False
_last_tables_try = 0.0

# 建表语句全部幂等，可重复执行；与 db/schema_pgvector.sql 中的 DDL 保持一致。
_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS ai_mcp_server (
         id          BIGSERIAL    PRIMARY KEY,
         name        VARCHAR(64)  NOT NULL UNIQUE,
         transport   VARCHAR(16)  NOT NULL DEFAULT 'streamable_http',
         url         TEXT         NOT NULL DEFAULT '',
         command     TEXT         NOT NULL DEFAULT '',
         args        JSONB        NOT NULL DEFAULT '[]'::jsonb,
         env         JSONB        NOT NULL DEFAULT '{}'::jsonb,
         headers     JSONB        NOT NULL DEFAULT '{}'::jsonb,
         description VARCHAR(255) NOT NULL DEFAULT '',
         enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
         create_time TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
         update_time TIMESTAMPTZ  NOT NULL DEFAULT NOW()
       )""",
)


def _init_sync() -> None:
    """同步创建连接池（供 ``asyncio.to_thread`` 调用）。

    Raises:
        Exception: 连接池构造失败时抛出，由 ``init`` 捕获并降级。
    """
    global _pool
    _pool = ConnectionPool(
        conninfo=settings.pg_dsn,
        min_size=settings.PG_POOL_MIN,
        max_size=settings.PG_POOL_MAX,
        timeout=5.0,
        # 惰性连接：PG 暂时不可达也能把服务拉起来，后续由池自身重连。
        open=False,
    )
    _pool.open(wait=False)


async def init() -> None:
    """初始化连接池并确保扩展配置表存在。
    """
    global _pool
    try:
        await asyncio.to_thread(_init_sync)
        logger.info("PostgreSQL 连接池已初始化（AI 扩展配置）: %s@%s:%s/%s",
                    settings.PG_USER, settings.PG_HOST, settings.PG_PORT, settings.PG_DB)
    except Exception:
        logger.warning("PostgreSQL 连接池初始化失败，AI 扩展管理暂不可用", exc_info=True)
        _pool = None
        return
    await _ensure_tables()


async def _ensure_tables() -> bool:
    """确保扩展配置表就绪（带冷却的惰性建表）。

    Returns:
        True 表示表已就绪；False 表示当前不可用。
    """
    global _tables_ready, _last_tables_try
    if _tables_ready:
        return True
    pool = _pool
    if pool is None:
        return False
    if time.monotonic() - _last_tables_try < _TABLES_RETRY_COOLDOWN:
        return False
    _last_tables_try = time.monotonic()

    def _create() -> None:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                for ddl in _SCHEMA_STATEMENTS:
                    cur.execute(ddl)

    try:
        await asyncio.to_thread(_create)
        _tables_ready = True
        logger.info("MCP 服务配置表已就绪: ai_mcp_server")
    except Exception:
        logger.warning("AI 扩展配置表初始化失败（%ss 后自动重试）",
                       int(_TABLES_RETRY_COOLDOWN), exc_info=True)
    return _tables_ready


async def close() -> None:
    """关闭连接池。
    """
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        try:
            await asyncio.to_thread(pool.close)
        except Exception:
            logger.warning("关闭 PostgreSQL 连接池异常（忽略）", exc_info=True)


def available() -> bool:
    """连接池是否已初始化。

    Returns:
        True 表示可以尝试读写。
    """
    return _pool is not None


async def _run(sql: str, params: tuple = (), fetch: bool = False):
    """执行单条 SQL。

    Args:
        sql: SQL 语句（``%s`` 占位符）。
        fetch: True 时返回 ``fetchall()`` 结果。

    Returns:
        查询结果行列表；``fetch=False`` 时为 None。

    Raises:
        RuntimeError: 表尚未就绪时抛出，由路由层转成业务错误报文。
        Exception: 透传 psycopg 异常（如唯一键冲突 UniqueViolation）。
    """
    if not await _ensure_tables():
        raise RuntimeError("PostgreSQL 未就绪")

    def _exec():
        with _pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None

    return await asyncio.to_thread(_exec)


def _json_value(raw, default):
    """兼容驱动把 JSONB 返回成字符串的情况（未注册适配器时会出现）。"""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default
    return raw if raw is not None else default


def _mcp_to_dict(r) -> dict:
    """MCP 服务行 → 对外字典（时间转秒级时间戳，与全站接口口径一致）。"""
    return {
        "id": r[0], "name": r[1], "transport": r[2], "url": r[3],
        "command": r[4], "args": _json_value(r[5], []),
        "env": _json_value(r[6], {}), "headers": _json_value(r[7], {}),
        "description": r[8], "enabled": bool(r[9]),
        "createTime": int(r[10] or 0), "updateTime": int(r[11] or 0),
    }


_MCP_COLUMNS = ("id, name, transport, url, command, args, env, headers, description, enabled, "
                "EXTRACT(EPOCH FROM create_time)::bigint, EXTRACT(EPOCH FROM update_time)::bigint")


async def list_mcp_servers() -> List[dict]:
    """全部 MCP 服务（按创建先后排序，管理页展示用）。

    Returns:
        服务配置列表。

    Raises:
        RuntimeError / Exception: 存储不可用时向上抛，由路由层转业务错误。
    """
    rows = await _run(f"SELECT {_MCP_COLUMNS} FROM ai_mcp_server ORDER BY id", fetch=True)
    return [_mcp_to_dict(r) for r in rows or []]


async def list_enabled_mcp_servers() -> List[dict]:
    """启用中的 MCP 服务（Agent 运行时装配用）。

    Returns:
        服务配置列表；无启用项时为空列表。

    Raises:
        RuntimeError / Exception: 存储不可用时向上抛，由调用方降级。
    """
    rows = await _run(f"SELECT {_MCP_COLUMNS} FROM ai_mcp_server WHERE enabled ORDER BY id", fetch=True)
    return [_mcp_to_dict(r) for r in rows or []]


async def get_mcp_server(server_id: int) -> Optional[dict]:
    """按 ID 取单个 MCP 服务。

    Returns:
        服务配置；不存在时返回 None。

    Raises:
        RuntimeError / Exception: 存储不可用时向上抛。
    """
    rows = await _run(f"SELECT {_MCP_COLUMNS} FROM ai_mcp_server WHERE id=%s",
                      (server_id,), fetch=True)
    return _mcp_to_dict(rows[0]) if rows else None


async def create_mcp_server(data: dict) -> dict:
    """新增 MCP 服务。

    Args:
        data: 已通过接口层校验的配置（name / transport / url / command / args / env /
            headers / description / enabled）。

    Returns:
        创建后的完整配置（含 ID 与时间戳）。

    Raises:
        psycopg.errors.UniqueViolation: 名称重复，由路由层转 409。
        RuntimeError / Exception: 存储不可用时向上抛。
    """
    sql = ("INSERT INTO ai_mcp_server (name, transport, url, command, args, env, headers, "
           "description, enabled) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s) "
           "RETURNING id")
    rows = await _run(sql, (
        data["name"], data["transport"], data.get("url", ""), data.get("command", ""),
        json.dumps(data.get("args") or [], ensure_ascii=False),
        json.dumps(data.get("env") or {}, ensure_ascii=False),
        json.dumps(data.get("headers") or {}, ensure_ascii=False),
        data.get("description", ""), bool(data.get("enabled", True)),
    ), fetch=True)
    return await get_mcp_server(rows[0][0])


async def update_mcp_server(server_id: int, data: dict) -> Optional[dict]:
    """整体更新 MCP 服务配置（含启用状态）。

    Args:
        data: 与 ``create_mcp_server`` 同构的完整配置。

    Returns:
        更新后的配置；ID 不存在时返回 None。

    Raises:
        psycopg.errors.UniqueViolation: 名称与其他服务重复。
        RuntimeError / Exception: 存储不可用时向上抛。
    """
    sql = ("UPDATE ai_mcp_server SET name=%s, transport=%s, url=%s, command=%s, "
           "args=%s::jsonb, env=%s::jsonb, headers=%s::jsonb, description=%s, enabled=%s, "
           "update_time=NOW() WHERE id=%s")
    await _run(sql, (
        data["name"], data["transport"], data.get("url", ""), data.get("command", ""),
        json.dumps(data.get("args") or [], ensure_ascii=False),
        json.dumps(data.get("env") or {}, ensure_ascii=False),
        json.dumps(data.get("headers") or {}, ensure_ascii=False),
        data.get("description", ""), bool(data.get("enabled", True)), server_id,
    ))
    return await get_mcp_server(server_id)


async def delete_mcp_server(server_id: int) -> bool:
    """删除 MCP 服务。

    Returns:
        True 表示删掉了；False 表示 ID 不存在。

    Raises:
        RuntimeError / Exception: 存储不可用时向上抛。
    """
    rows = await _run("DELETE FROM ai_mcp_server WHERE id=%s RETURNING id",
                      (server_id,), fetch=True)
    return bool(rows)