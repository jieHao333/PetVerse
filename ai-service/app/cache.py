"""两级缓存门面与单飞（single-flight）并发控制。

把「Redis 热缓存」与「PostgreSQL 温缓存」组合成对上层透明的两级缓存，并提供按 key
粒度的单飞锁——同一个 key 并发未命中时只让一个请求真正回源计算，其余等待并复用结果。
"""

import asyncio
import random
from contextlib import asynccontextmanager
from typing import Optional

from app import memory, pg_store

# key -> {"lock": asyncio.Lock, "waiters": int}
# 用「引用计数 + 到期摘除」而非固定字典：key 数量随用户增长无上限，不回收会持续泄漏内存。
_flights: dict = {}

_flights_lock = asyncio.Lock()

# TTL 抖动比例：同一批写入的 key 若过期时间完全一致，会在同一秒集中失效并同时回源，
# 加 0~10% 的随机偏移把失效时间打散。
_TTL_JITTER_RATIO = 0.1


@asynccontextmanager
async def single_flight(cache_key: str):
    """按 key 的异步单飞锁。

    进入时登记等待者，退出时递减；最后一个等待者离开时摘除该 key 的锁对象。

    Args:
        cache_key: 缓存键（同一 key 的并发调用会被串行化）。

    Yields:
        None：``async with`` 内即临界区。
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
        # 用「登记/注销配对」而不是 try/finally 删 key：若直接删除，仍在排队的等待者
        # 会持有已被摘除的旧锁对象，新来的请求又新建一把锁，串行化就失效了。
        async with _flights_lock:
            flight["waiters"] -= 1
            if flight["waiters"] <= 0:
                _flights.pop(cache_key, None)


async def get_hot(cache_key: str) -> Optional[dict]:
    """读热缓存（仅 Redis）。

    Returns:
        缓存值；未命中或 Redis 不可用时返回 None。
    """
    return await memory.cache_get(cache_key)


async def set_hot(cache_key: str, payload: dict, ttl: int) -> None:
    """写热缓存（仅 Redis）。

    Args:
        ttl: 过期秒数。
    """
    await memory.cache_set(cache_key, payload, ttl)


async def get_cached(cache_key: str) -> Optional[dict]:
    """读两级缓存：先 Redis，未命中再回落 PostgreSQL。

    Returns:
        缓存值；两级都未命中时返回 None。
    """
    cached = await memory.cache_get(cache_key)
    if cached is not None:
        return cached
    return await pg_store.cache_get(cache_key)


async def set_cached(cache_key: str, payload: dict, ttl: int) -> None:
    """双写两级缓存（并行写，互不阻塞）。

    Args:
        ttl: 过期秒数（实际写入值附带抖动）。
    """
    ttl_with_jitter = ttl + random.randint(0, max(1, int(ttl * _TTL_JITTER_RATIO)))
    await asyncio.gather(
        memory.cache_set(cache_key, payload, ttl_with_jitter),
        pg_store.cache_set(cache_key, payload, ttl_with_jitter),
    )
