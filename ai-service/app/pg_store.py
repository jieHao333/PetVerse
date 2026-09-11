"""PostgreSQL 存储层（psycopg3 同步连接池 + asyncio.to_thread）

承载两类数据：
  1. pet_health_report：健康评估报告（按用户 + 宠物保留历史）；
  2. ai_cache：AI 结果缓存（Redis 不可用时的兜底）。

为什么用同步连接池：psycopg 的异步连接在 Windows 默认的 ProactorEventLoop 下无法运行
（会抛 "Psycopg cannot use the 'ProactorEventLoop'"）。为保证 Windows / Linux 一致可用，
这里使用同步 psycopg_pool.ConnectionPool，所有查询通过 asyncio.to_thread 丢到线程池执行，
既不阻塞事件循环，也规避了各平台事件循环差异。

所有方法异常降级：PG 不可用时读返回空、写静默失败仅记日志，
绝不让 AI 主流程报错（与 memory.py / persistence.py 的策略一致）。
"""
import asyncio
import json
import logging
from typing import List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_pool = None


def _init_pool_sync():
    """同步创建并打开连接池（在线程中调用）"""
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
    """创建 PG 连接池（服务启动时调用）；失败不抛出，功能自动降级"""
    global _pool
    try:
        _pool = await asyncio.to_thread(_init_pool_sync)
        logger.info("PostgreSQL 连接池已初始化: %s@%s:%s/%s",
                    settings.PG_USER, settings.PG_HOST, settings.PG_PORT, settings.PG_DB)
    except Exception:
        logger.warning("PostgreSQL 连接池初始化失败，健康报告持久化/缓存将降级", exc_info=True)
        _pool = None


async def close() -> None:
    """关闭 PG 连接池（服务停机时调用）"""
    global _pool
    if _pool is not None:
        try:
            await asyncio.to_thread(_pool.close)
        except Exception:
            logger.warning("关闭 PostgreSQL 连接池异常（忽略）", exc_info=True)
        _pool = None


def available() -> bool:
    """PG 是否可用"""
    return _pool is not None


async def _run(sql: str, params: tuple, fetch: bool = False):
    """在线程中执行单条 SQL；连接池未就绪返回 None"""
    if _pool is None:
        return None

    def _exec():
        with _pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None

    return await asyncio.to_thread(_exec)


# ---------- 健康评估报告 ----------

async def save_health_report(user_id: int, pet_id: int, score: int,
                             level: str, payload: dict) -> None:
    """保存一次健康评估报告（失败仅记日志）"""
    sql = ("INSERT INTO pet_health_report (user_id, pet_id, score, level, payload) "
           "VALUES (%s, %s, %s, %s, %s::jsonb)")
    try:
        await _run(sql, (user_id, pet_id, score, level,
                         json.dumps(payload, ensure_ascii=False)))
    except Exception:
        logger.warning("保存健康评估报告失败（忽略）", exc_info=True)


async def list_health_reports(user_id: int, pet_id: int, limit: int = 10) -> List[dict]:
    """查询某宠物的历史健康评估（时间倒序），失败返回空列表"""
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
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
        reports.append({"id": r[0], "score": r[1], "level": r[2],
                        "report": payload, "createTime": int(r[4] or 0)})
    return reports


# ---------- 通用缓存 ----------

async def cache_get(cache_key: str) -> Optional[dict]:
    """读取未过期的缓存，未命中返回 None"""
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
    """写入缓存（UPSERT，带过期时间）"""
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
