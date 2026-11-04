"""健康评估报告与温层缓存的 PostgreSQL 存储。

职责：存放两类「可丢但有用」的数据——宠物健康评估历史（``pet_health_report``）与
两级缓存中的**温层**（``ai_cache``，Redis 未命中时回落读取）。与 ``app.persistence``
（会话与消息）分开，是因为两者的可靠性要求不同：会话历史要尽量保住，这里全部可降级。

主要导出：
    - ``init`` / ``close`` / ``available``  生命周期
    - ``save_health_report`` / ``list_health_reports``  健康报告读写
    - ``cache_get`` / ``cache_set``  温层缓存读写

外部依赖：``psycopg`` / ``psycopg_pool``、``app.config.settings``。

使用示例：
    from app import pg_store

    await pg_store.save_health_report(user_id=42, pet_id=7, score=88, level="excellent", payload=report)
    history = await pg_store.list_health_reports(42, 7, limit=10)
"""

import asyncio
import json
import logging
from typing import List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_pool = None


def _init_pool_sync():
    """同步创建连接池（供 ``asyncio.to_thread`` 调用）。

    与其它存储层的差别：这里 ``open(wait=True, timeout=5)``，启动时确认一次连通性，
    使表结构缺失 / 认证错误能在启动阶段就暴露出来。

    Returns:
        已打开的连接池。

    Raises:
        Exception: 5 秒内连不上时抛出。
    """
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        conninfo=settings.pg_dsn,
        min_size=settings.PG_POOL_MIN,
        max_size=settings.PG_POOL_MAX,
        open=False,
    )
    pool.open(wait=True, timeout=5)
    return pool


async def init() -> None:
    """初始化连接池；失败时健康报告与温层缓存降级为不可用。

    Returns:
        None。

    Raises:
        无。
    """
    global _pool
    try:
        _pool = await asyncio.to_thread(_init_pool_sync)
        logger.info("PostgreSQL 连接池已初始化: %s@%s:%s/%s",
                    settings.PG_USER, settings.PG_HOST, settings.PG_PORT, settings.PG_DB)
    except Exception:
        logger.warning("PostgreSQL 连接池初始化失败，健康报告持久化/缓存将降级", exc_info=True)
        _pool = None


async def close() -> None:
    """关闭连接池。

    Returns:
        None。

    Raises:
        无。
    """
    global _pool
    if _pool is not None:
        try:
            await asyncio.to_thread(_pool.close)
        except Exception:
            logger.warning("关闭 PostgreSQL 连接池异常（忽略）", exc_info=True)
        _pool = None


def available() -> bool:
    """连接池是否已初始化。

    Returns:
        True 表示可以尝试读写。

    Raises:
        无。
    """
    return _pool is not None


async def _run(sql: str, params: tuple, fetch: bool = False):
    """执行单条 SQL。

    Args:
        sql: SQL 语句（``%s`` 占位符）。
        params: 参数元组。
        fetch: True 时返回 ``fetchall()`` 结果。

    Returns:
        查询结果行列表；连接池未初始化或 ``fetch=False`` 时返回 None。

    Raises:
        Exception: 透传 psycopg 异常，由各业务函数捕获降级。
    """
    if _pool is None:
        return None

    def _exec():
        with _pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None

    return await asyncio.to_thread(_exec)


async def save_health_report(user_id: int, pet_id: int, score: int,
                             level: str, payload: dict) -> None:
    """保存一次健康评估结果。

    只追加不更新：历史报告的价值在于可对比趋势，覆盖写会丢掉变化过程。

    Args:
        user_id: 用户 ID。
        pet_id: 宠物 ID。
        score: 综合评分（0-100）。
        level: 评级（excellent / good / fair / warning）。
        payload: 完整报告（落 ``payload`` JSONB，供回放）。

    Returns:
        None。

    Raises:
        无——保存失败仅记日志，报告已返回给用户。
    """
    sql = ("INSERT INTO pet_health_report (user_id, pet_id, score, level, payload) "
           "VALUES (%s, %s, %s, %s, %s::jsonb)")
    try:
        await _run(sql, (user_id, pet_id, score, level,
                         json.dumps(payload, ensure_ascii=False)))
    except Exception:
        logger.warning("保存健康评估报告失败（忽略）", exc_info=True)


async def list_health_reports(user_id: int, pet_id: int, limit: int = 10) -> List[dict]:
    """查询宠物的历史健康报告（最近在前）。

    Args:
        user_id: 用户 ID（同时作为归属校验，防止越权查他人宠物）。
        pet_id: 宠物 ID。
        limit: 返回条数上限。

    Returns:
        报告列表（id / score / level / report / createTime）；失败时为空列表。

    Raises:
        无。
    """
    sql = ("SELECT id, score, level, payload, "
           "EXTRACT(EPOCH FROM create_time)::bigint AS ts "
           "FROM pet_health_report WHERE user_id=%s AND pet_id=%s "
           "ORDER BY create_time DESC, id DESC LIMIT %s")
    try:
        rows = await _run(sql, (user_id, pet_id, limit), fetch=True)
    except Exception:
        logger.warning("查询健康评估历史失败（降级为空）", exc_info=True)
        return []
    reports = []
    for r in rows or []:
        payload = r[3]
        # 兼容驱动返回字符串的情况（未注册 JSONB 适配器时会出现）。
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        reports.append({"id": r[0], "score": r[1], "level": r[2],
                        "report": payload, "createTime": int(r[4] or 0)})
    return reports


async def cache_get(cache_key: str) -> Optional[dict]:
    """读取温层缓存（仅返回未过期的行）。

    Args:
        cache_key: 缓存键。

    Returns:
        缓存值；不存在、已过期或读取失败时返回 None。

    Raises:
        无。
    """
    # 过期判断放在 SQL 里而不是取回后在 Python 里比时间：避免把过期数据带出数据库。
    sql = "SELECT payload FROM ai_cache WHERE cache_key=%s AND expire_time > NOW()"
    try:
        rows = await _run(sql, (cache_key,), fetch=True)
    except Exception:
        logger.warning("读取 AI 缓存失败（忽略）", exc_info=True)
        return None
    if not rows:
        return None
    payload = rows[0][0]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    return payload


async def cache_set(cache_key: str, payload: dict, ttl_seconds: int) -> None:
    """写入温层缓存（同键覆盖并刷新过期时间）。

    Args:
        cache_key: 缓存键。
        payload: 可序列化的结果体。
        ttl_seconds: 过期秒数；``<= 0`` 视为不缓存。

    Returns:
        None。

    Raises:
        无。
    """
    if ttl_seconds <= 0:
        return
    sql = ("INSERT INTO ai_cache (cache_key, payload, expire_time) "
           "VALUES (%s, %s::jsonb, NOW() + (%s || ' seconds')::interval) "
           "ON CONFLICT (cache_key) DO UPDATE SET payload=EXCLUDED.payload, "
           "expire_time=EXCLUDED.expire_time")
    try:
        await _run(sql, (cache_key, json.dumps(payload, ensure_ascii=False), str(ttl_seconds)))
    except Exception:
        logger.warning("写入 AI 缓存失败（忽略）", exc_info=True)
