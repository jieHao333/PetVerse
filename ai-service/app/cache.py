"""结果缓存门面：推荐走 Redis 单层，其他 AI 结果走两级降级

读取策略：
  - 个性化推荐（get_hot / set_hot）：只读写 Redis，TTL 到期后 Redis 未命中
    必须由调用方重新查询业务数据，不回落 PostgreSQL 温层；
  - 评论摘要等（get_cached / set_cached）：Redis 热层未命中回落 ai_cache 温层，
    Redis 故障 / 重启后仍可用；
  - 两级写入为 best-effort，缓存异常绝不影响主流程；
  - 单飞：同一 key 的并发未命中只放行一次真实计算，其余请求等待后直接取结果
    （防止热点商品 / 热门场景在缓存过期瞬间被并发打穿）；
  - 雪崩：两级缓存写入附加 TTL 随机抖动，避免同一批缓存同时过期。

为什么评论摘要仍需要第二级：Redis 是易失的热缓存——实例故障 / 重启会让全部结果缓存
失效、请求透传重算（LLM 调用 + 外部服务查询）。ai_cache 落 PostgreSQL，
与业务数据同库持久，Redis 缺失期间缓存能力不受单点影响。

推荐缓存不使用温层：推荐结果强调「五分钟内复用、过期即重新查业务」，
若回落 ai_cache 会让 Redis 过期后仍返回旧推荐，弱化刷新语义。

说明：ai_cache 命中时不回填 Redis——避免「回填续期」让 TTL 语义失真
（如评论摘要自然过期重建的意图会被持续访问无限延长）；
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


async def get_hot(cache_key: str) -> Optional[dict]:
    """只读 Redis 热层；未命中返回 None，由调用方直接重新查询业务数据

    推荐缓存使用此方法：Redis 过期/缺失时不能回落到 ai_cache，
    否则“Redis 五分钟过期后重新计算”的语义会被温层破坏。
    """
    return await memory.cache_get(cache_key)


async def set_hot(cache_key: str, payload: dict, ttl: int) -> None:
    """只写 Redis 热层（不做 PostgreSQL 温层双写），异常由 Redis 层内部降级"""
    await memory.cache_set(cache_key, payload, ttl)


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
