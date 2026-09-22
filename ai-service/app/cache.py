"""结果缓存门面：Redis（热）→ PostgreSQL ai_cache（温）两级降级

评论摘要 / 个性化推荐等 AI 结果的两级缓存，由本模块统一读写：
  - 读：优先 Redis（快）；未命中回落 ai_cache（温层，Redis 故障 / 重启后仍可用）；
    都未命中返回 None，由调用方重新计算；
  - 写：Redis 与 ai_cache 并行双写（TTL 一致），均为 best-effort
    （两个模块内部各自异常降级，绝不让缓存故障影响主流程）；
  - 单飞：同一 key 的并发未命中只放行一次真实计算，其余请求等待后直接取结果
    （防止热点商品 / 热门场景在缓存过期瞬间被并发打穿）；
  - 雪崩：写入 TTL 附加随机抖动，避免同一批缓存同时过期。

为什么需要第二级：Redis 是易失的热缓存——实例故障 / 重启会让全部结果缓存
失效、请求透传重算（LLM 调用 + 外部服务查询）。ai_cache 落 PostgreSQL，
与业务数据同库持久，Redis 缺失期间缓存能力不受单点影响。

说明：ai_cache 命中时不回填 Redis——避免「回填续期」让 TTL 语义失真
（如推荐缓存「10 分钟自然过期重建」的意图会被持续访问无限延长）；
温层的定位是「Redis 缺失时仍可用」，不是保温。
"""
import asyncio
import random
from contextlib import asynccontextmanager
from typing import Optional

from app import memory, pg_store

# 进行中的同 key 计算（cache_key -> 等待者计数与锁）
_flights: dict = {}

_flights_lock = asyncio.Lock()

# TTL 抖动上限比例：实际 TTL = 配置值 + 配置值的 0~10%
_TTL_JITTER_RATIO = 0.1


@asynccontextmanager
async def single_flight(cache_key: str):
    """同 key 串行：并发未命中时只有一个请求进入计算，其余在此等待

    等待者计数归零后从表中移除锁，避免锁对象随 key 无限堆积。
    """
    async with _flights_lock:
        flight = _flights.get(cache_key)
        if flight is None:
            flight = {"lock": asyncio.Lock(), "waiters": 0}
            _flights[cache_key] = flight
        flight["waiters"] += 1
    try:
        async with flight["lock"]:
            yield
    finally:
        async with _flights_lock:
            flight["waiters"] -= 1
            if flight["waiters"] <= 0:
                _flights.pop(cache_key, None)


async def get_cached(cache_key: str) -> Optional[dict]:
    """两级读取：Redis 未命中回落 ai_cache；都未命中返回 None"""
    cached = await memory.cache_get(cache_key)
    if cached is not None:
        return cached
    return await pg_store.cache_get(cache_key)


async def set_cached(cache_key: str, payload: dict, ttl: int) -> None:
    """两级写入：Redis 与 ai_cache 并行双写（TTL 一致）；单边失败不影响另一边

    写入 TTL 加入随机抖动，避免同一时间写入的一批缓存同时到期引发重算高峰。
    """
    ttl_with_jitter = ttl + random.randint(0, max(1, int(ttl * _TTL_JITTER_RATIO)))
    await asyncio.gather(
        memory.cache_set(cache_key, payload, ttl_with_jitter),
        pg_store.cache_set(cache_key, payload, ttl_with_jitter),
    )
