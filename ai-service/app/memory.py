"""Redis 通用缓存（redis.asyncio，db=3）

承载评论摘要 / 个性化推荐等 AI 结果缓存；对话上下文记忆已迁移至
LangGraph 官方 checkpoint（PostgreSQL，见 app/checkpoint.py），本模块不再
读写聊天历史。

所有操作均做异常降级：Redis 故障时绝不让主流程报错，读返回 None、写静默失败仅记日志。
"""
import json
import logging
from typing import Optional

import redis.asyncio as aredis

from app.config import settings

logger = logging.getLogger(__name__)

# 连接池与客户端单例（由 main.py 的 lifespan 在启动 / 停机时初始化与关闭）
_pool: Optional[aredis.ConnectionPool] = None
_client: Optional[aredis.Redis] = None


def init() -> None:
    """初始化 Redis 连接池（服务启动时调用一次，创建连接池本身不发起网络请求）"""
    global _pool, _client
    _pool = aredis.ConnectionPool(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        password=settings.REDIS_PASSWORD or None,  # 密码为空时不传，兼容无密码 Redis
        db=settings.REDIS_DB,
        decode_responses=True,                     # 直接返回 str，免去手动 decode
        max_connections=50,
        # 读写与建连超时：Redis 无响应时快速失败并进入降级分支，请求线程不被挂住
        socket_timeout=3,
        socket_connect_timeout=2,
    )
    _client = aredis.Redis(connection_pool=_pool)


async def close() -> None:
    """关闭 Redis 连接池（服务停机时调用）"""
    global _pool, _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            logger.warning("关闭 Redis 客户端异常（忽略）", exc_info=True)
        _client = None
    if _pool is not None:
        try:
            await _pool.aclose()
        except Exception:
            logger.warning("关闭 Redis 连接池异常（忽略）", exc_info=True)
        _pool = None


# ---------- 通用 JSON 缓存（评论摘要 / 个性化推荐等 AI 结果；由 app/cache.py 两级门面统一调用） ----------

async def cache_get(key: str) -> Optional[dict]:
    """读取 JSON 缓存，未命中或异常返回 None"""
    if _client is None:
        return None
    try:
        raw = await _client.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        logger.warning("读取 Redis 缓存失败（忽略）: %s", key, exc_info=True)
        return None


async def cache_set(key: str, value: dict, ttl: int) -> None:
    """写入 JSON 缓存并设置 TTL；异常仅记日志"""
    if _client is None or ttl <= 0:
        return
    try:
        await _client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
    except Exception:
        logger.warning("写入 Redis 缓存失败（忽略）: %s", key, exc_info=True)


async def cache_delete(key: str) -> None:
    """删除缓存键；异常仅记日志"""
    if _client is None:
        return
    try:
        await _client.delete(key)
    except Exception:
        logger.warning("删除 Redis 缓存失败（忽略）: %s", key, exc_info=True)


# ---------- 原子计数（用户级并发额度等跨实例共享的计数） ----------

def available() -> bool:
    """Redis 是否已初始化并可用"""
    return _client is not None


async def eval_script(script: str, key: str, *args) -> Optional[int]:
    """执行 Lua 脚本并返回整数结果

    计数类操作需要「读 + 写 + 设置过期」在多实例间保持原子，故以脚本形式下发；
    未初始化返回 None，执行异常向上抛出，由调用方决定降级方式。
    """
    if _client is None:
        return None
    result = await _client.eval(script, 1, key, *args)
    return int(result) if result is not None else None


async def get_int(key: str) -> Optional[int]:
    """读取整数计数键；键不存在返回 None，异常向上抛出"""
    if _client is None:
        return None
    raw = await _client.get(key)
    return int(raw) if raw is not None else None
