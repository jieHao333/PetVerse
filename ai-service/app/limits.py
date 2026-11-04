"""单用户并发闸（Redis 计数 + 进程内兜底）。

职责：限制同一用户同时进行的流式对话路数，防止单账号（或脚本）占满全局并发名额、把其他
用户挤出服务。计数优先放在 Redis（多实例共享），Redis 不可用时退化为进程内计数。

主要导出：
    - ``try_acquire``  尝试占用一个名额（返回是否成功、是否走了 Redis）
    - ``release``      归还名额
    - ``is_busy``      只读探测当前是否已达上限（用于快速拒绝，不占名额）

外部依赖：``app.memory``（Redis Lua 原子操作）。

使用示例：
    from app import limits

    acquired, via_redis = await limits.try_acquire(user_id, 2)
    if not acquired:
        return "您有正在生成的咨询回复"
    try:
        ...
    finally:
        await limits.release(user_id, via_redis)   # 必须回传 via_redis 保持计数来源一致
"""

import asyncio
import logging

from app import memory

logger = logging.getLogger(__name__)

# 计数键的兜底 TTL：进程崩溃/重启时不会有代码去 DECR，靠过期自动回收，
# 否则该用户会被永久判定为「忙」。5 分钟为经验值，需大于单轮生成的最长耗时。
_COUNTER_TTL_MILLIS = 300_000

# 自增并首次设置过期时间：只在 current == 1（首次创建）时设置 PEXPIRE，
# 避免每次自增都续期导致键永不过期。
_ACQUIRE_SCRIPT = (
    "local current = redis.call('INCR', KEYS[1]) "
    "if current == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end "
    "return current"
)

# 自减到 0 及以下时直接删键：残留的 0 值键既占内存，也会让下次 acquire 的「首次」判断失效。
_RELEASE_SCRIPT = (
    "local current = redis.call('DECR', KEYS[1]) "
    "if current <= 0 then redis.call('DEL', KEYS[1]) return 0 end "
    "return current"
)

# 进程内兜底计数：仅在 Redis 不可用时使用，天然只在单实例内有效。
_local_counts: dict = {}

_local_lock = asyncio.Lock()


def _key(user_id: int) -> str:
    """拼出计数键。

    Args:
        user_id: 用户 ID。

    Returns:
        Redis 键名，形如 ``ai:conc:user:{id}``。

    Raises:
        无。
    """
    return f"ai:conc:user:{user_id}"


async def try_acquire(user_id: int, limit: int) -> tuple:
    """尝试占用一个并发名额。

    Args:
        user_id: 用户 ID。
        limit: 该用户允许的最大并发数。

    Returns:
        ``(acquired, via_redis)`` 二元组：
            - ``acquired``：是否成功占到名额；
            - ``via_redis``：本次计数是否发生在 Redis 上。归还时必须原样回传给 ``release``，
              否则会在错误的计数体系上做递减，导致名额泄漏。

    Raises:
        无——Redis 异常在内部降级为进程内计数。
    """
    key = _key(user_id)
    try:
        current = await memory.eval_script(_ACQUIRE_SCRIPT, key, str(_COUNTER_TTL_MILLIS))
        if current is not None:
            if current > limit:
                # 超限时要立刻回滚刚做的自增，否则每次被拒都会把计数越堆越高，
                # 用户即使后续空闲也永远无法再进来（只能等 TTL）。
                await memory.eval_script(_RELEASE_SCRIPT, key)
                return False, True
            return True, True
    except Exception:
        logger.warning("Redis 并发计数不可用，降级为进程内计数: user_id=%s", user_id, exc_info=True)
    async with _local_lock:
        current = _local_counts.get(key, 0)
        if current >= limit:
            return False, False
        _local_counts[key] = current + 1
    return True, False


async def release(user_id: int, via_redis: bool) -> None:
    """归还并发名额。

    Args:
        user_id: 用户 ID。
        via_redis: ``try_acquire`` 返回的计数来源；决定在 Redis 还是进程内字典上递减。

    Returns:
        None。

    Raises:
        无——Redis 递减失败时依赖键 TTL 自动回收，不做进程内补偿。
    """
    key = _key(user_id)
    if via_redis:
        try:
            await memory.eval_script(_RELEASE_SCRIPT, key)
            return
        except Exception:
            logger.warning("Redis 并发计数归还失败（依赖 TTL 回收）: user_id=%s", user_id, exc_info=True)
        # 刻意不回退到进程内递减：本轮的计数从未记在本地字典里，扣减会误伤其他请求。
        return
    async with _local_lock:
        current = _local_counts.get(key, 0) - 1
        if current > 0:
            _local_counts[key] = current
        else:
            _local_counts.pop(key, None)


async def is_busy(user_id: int, limit: int) -> bool:
    """只读判断用户当前是否已达并发上限（不占名额）。

    用于在建立 SSE 流之前快速拒绝，省掉一次流对象创建与信号量排队。

    Args:
        user_id: 用户 ID。
        limit: 该用户允许的最大并发数。

    Returns:
        True 表示已达上限。

    Raises:
        无——Redis 读取失败时改用进程内计数判断。
    """
    key = _key(user_id)
    try:
        current = await memory.get_int(key)
        if current is not None:
            return current >= limit
    except Exception:
        logger.warning("读取 Redis 并发计数失败，改用进程内计数判断: user_id=%s", user_id, exc_info=True)
    async with _local_lock:
        return _local_counts.get(key, 0) >= limit
