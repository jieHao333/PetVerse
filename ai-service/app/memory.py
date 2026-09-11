"""Redis 对话记忆（redis.asyncio，db=3）

key 结构：ai:chat:history:{userId}:{sessionId}（LIST，每项为 JSON 字符串 {"role","content","ts"}）
会话按「用户 + 宠物」隔离创建，key 再拼会话 ID 即可完成三级隔离。

所有操作均做了异常降级：Redis 故障时绝不让对话主流程报错，
read 返回空列表、append / clear 静默失败仅记日志。
"""
import json
import logging
import time
from typing import List, Optional

import redis.asyncio as aredis

from app.config import settings

logger = logging.getLogger(__name__)

# 连接池与客户端单例（由 main.py 的 lifespan 在启动 / 停机时初始化与关闭）
_pool: Optional[aredis.ConnectionPool] = None
_client: Optional[aredis.Redis] = None


def _key(user_id: int, session_id: int) -> str:
    """拼接对话历史的 Redis Key：ai:chat:history:{userId}:{sessionId}"""
    return f"ai:chat:history:{user_id}:{session_id}"


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


async def read(user_id: int, session_id: int) -> List[dict]:
    """读取指定会话的对话历史（时间正序：旧 → 新）

    Redis 异常或客户端未初始化时返回空列表，绝不抛错。
    """
    if _client is None:
        return []
    try:
        raw_list = await _client.lrange(_key(user_id, session_id), 0, -1)
        result: List[dict] = []
        for raw in raw_list:
            try:
                item = json.loads(raw)
                result.append({
                    "role": item.get("role", "user"),
                    "content": item.get("content", ""),
                    "ts": item.get("ts", 0),
                })
            except (TypeError, ValueError):
                # 单条脏数据直接跳过，不影响其余历史
                continue
        return result
    except Exception:
        logger.warning("读取 Redis 对话历史失败，降级为空历史（不影响本次对话）", exc_info=True)
        return []


async def append(user_id: int, session_id: int, user_msg: str, reply: str) -> None:
    """追加一轮对话（用户消息 + 助手回复）

    一次性 RPUSH 两条 + LTRIM 裁剪到最近 HISTORY_MAX_MESSAGES 条 + EXPIRE 续期；
    任何异常仅记日志，不影响对话主流程。
    """
    if _client is None:
        return
    now = int(time.time())
    key = _key(user_id, session_id)
    try:
        pipe = _client.pipeline()
        pipe.rpush(key, json.dumps({"role": "user", "content": user_msg, "ts": now}, ensure_ascii=False))
        pipe.rpush(key, json.dumps({"role": "assistant", "content": reply, "ts": now}, ensure_ascii=False))
        pipe.ltrim(key, -settings.HISTORY_MAX_MESSAGES, -1)   # 只保留最近 N 条
        pipe.expire(key, settings.HISTORY_TTL_SECONDS)       # 每次写入滚动续期
        await pipe.execute()
    except Exception:
        logger.warning("写入 Redis 对话历史失败（不影响本次对话）", exc_info=True)


# append_if_exists 的 Lua 脚本：exists / rpush / ltrim / expire 全部在 Redis 服务端原子执行，
# 避免「先 exists 检查、再写入」两步之间 key 被 DELETE（用户删除会话）导致已删的历史复活。
# max_messages / ttl 在模块加载时从配置格式化进脚本（均为整数，无注入风险）。
_APPEND_IF_EXISTS_LUA = """
if redis.call('exists', KEYS[1]) == 1 then
    redis.call('rpush', KEYS[1], ARGV[1], ARGV[2])
    redis.call('ltrim', KEYS[1], -{max_messages}, -1)
    redis.call('expire', KEYS[1], {ttl})
    return 1
end
return 0
""".format(max_messages=settings.HISTORY_MAX_MESSAGES, ttl=settings.HISTORY_TTL_SECONDS)


async def append_if_exists(user_id: int, session_id: int, user_msg: str, reply: str) -> None:
    """条件追加一轮对话：仅当历史 key 仍存在时才写入，key 不存在则跳过

    语义区分：正常对话结束用 append（允许创建新 key）；
    客户端断开 / 停止生成的兜底保存用本方法——若用户此刻已删除会话（key 被 DELETE），
    直接跳过写入，避免把刚删掉的历史"复活"。
    任何异常仅记日志，不影响对话主流程。
    """
    if _client is None:
        return
    now = int(time.time())
    try:
        # redis-py 5.x asyncio 的 eval 签名：eval(script, numkeys, *keys_and_args)
        await _client.eval(
            _APPEND_IF_EXISTS_LUA,
            1,
            _key(user_id, session_id),
            json.dumps({"role": "user", "content": user_msg, "ts": now}, ensure_ascii=False),
            json.dumps({"role": "assistant", "content": reply, "ts": now}, ensure_ascii=False),
        )
    except Exception:
        logger.warning("条件写入 Redis 对话历史失败（不影响本次对话）", exc_info=True)


async def clear(user_id: int, session_id: int) -> None:
    """清空指定会话的对话记忆；任何异常仅记日志"""
    if _client is None:
        return
    try:
        await _client.delete(_key(user_id, session_id))
    except Exception:
        logger.warning("清空 Redis 对话历史失败（忽略）", exc_info=True)


# ---------- 通用 JSON 缓存（评论摘要 / 个性化推荐等） ----------

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
