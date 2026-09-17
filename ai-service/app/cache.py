"""结果缓存门面：Redis（热）→ PostgreSQL ai_cache（温）两级降级

评论摘要 / 个性化推荐等 AI 结果的两级缓存，由本模块统一读写：
  - 读：优先 Redis（快）；未命中回落 ai_cache（温层，Redis 故障 / 重启后仍可用）；
    都未命中返回 None，由调用方重新计算；
  - 写：Redis 与 ai_cache 并行双写（TTL 一致），均为 best-effort
    （两个模块内部各自异常降级，绝不让缓存故障影响主流程）。

为什么需要第二级：Redis 是易失的热缓存——实例故障 / 重启会让全部结果缓存
失效、请求透传重算（LLM 调用 + 外部服务查询）。ai_cache 落 PostgreSQL，
与业务数据同库持久，Redis 缺失期间缓存能力不受单点影响。

说明：ai_cache 命中时不回填 Redis——避免「回填续期」让 TTL 语义失真
（如推荐缓存「10 分钟自然过期重建」的意图会被持续访问无限延长）；
温层的定位是「Redis 缺失时仍可用」，不是保温。
"""
import asyncio
from typing import Optional

from app import memory, pg_store


async def get_cached(cache_key: str) -> Optional[dict]:
    """两级读取：Redis 未命中回落 ai_cache；都未命中返回 None"""
    cached = await memory.cache_get(cache_key)
    if cached is not None:
        return cached
    return await pg_store.cache_get(cache_key)


async def set_cached(cache_key: str, payload: dict, ttl: int) -> None:
    """两级写入：Redis 与 ai_cache 并行双写（TTL 一致）；单边失败不影响另一边"""
    await asyncio.gather(
        memory.cache_set(cache_key, payload, ttl),
        pg_store.cache_set(cache_key, payload, ttl),
    )
