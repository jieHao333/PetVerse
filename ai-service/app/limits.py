"""用户级并发闸门：Redis 共享计数 + 进程内计数兜底

AI 服务多实例部署时，单用户并发上限需要跨实例生效，因此额度计数放在 Redis：
占用时 INCR，超过上限立即归还；计数首次建立时设置 TTL，
进程异常退出留下的计数由 TTL 自动回收，不会把用户永久挡在门外。

Redis 不可用时退化为进程内计数：单实例内上限仍然生效，对话主流程不中断。
"""
import asyncio
import logging

from app import memory

logger = logging.getLogger(__name__)

# 计数在 Redis 中的存活时间（毫秒）：兜底回收进程异常退出后未归还的额度
_COUNTER_TTL_MILLIS = 300_000

# 占用额度：计数从 0 变 1 的请求负责设置过期时间
_ACQUIRE_SCRIPT = (
    "local current = redis.call('INCR', KEYS[1]) "
    "if current == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end "
    "return current"
)

# 归还额度：计数归零即删除键，避免无用键堆积
_RELEASE_SCRIPT = (
    "local current = redis.call('DECR', KEYS[1]) "
    "if current <= 0 then redis.call('DEL', KEYS[1]) return 0 end "
    "return current"
)

# 进程内兜底计数（user_id -> 计数）
_local_counts: dict = {}

_local_lock = asyncio.Lock()


def _key(user_id: int) -> str:
    return f"ai:conc:user:{user_id}"


async def try_acquire(user_id: int, limit: int) -> tuple:
    """尝试占用一个并发额度

    :return: (是否占用成功, 是否使用 Redis 计数)
             占用成功时调用方必须在结束时按同一标志调用 release
    """
    key = _key(user_id)
    try:
        current = await memory.eval_script(_ACQUIRE_SCRIPT, key, str(_COUNTER_TTL_MILLIS))
        if current is not None:
            if current > limit:
                # 超限：立即归还本次占用，避免多占一个额度
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
    """归还并发额度，按占用时使用的计数方式归还，保证两个计数不会互相错位"""
    key = _key(user_id)
    if via_redis:
        try:
            await memory.eval_script(_RELEASE_SCRIPT, key)
            return
        except Exception:
            # 归还失败由计数 TTL 兜底回收
            logger.warning("Redis 并发计数归还失败（依赖 TTL 回收）: user_id=%s", user_id, exc_info=True)
        return
    async with _local_lock:
        current = _local_counts.get(key, 0) - 1
        if current > 0:
            _local_counts[key] = current
        else:
            _local_counts.pop(key, None)


async def is_busy(user_id: int, limit: int) -> bool:
    """只读判断是否已达并发上限（供建会话前的预检查使用，不占用额度）"""
    key = _key(user_id)
    try:
        current = await memory.get_int(key)
        if current is not None:
            return current >= limit
    except Exception:
        logger.warning("读取 Redis 并发计数失败，改用进程内计数判断: user_id=%s", user_id, exc_info=True)
    async with _local_lock:
        return _local_counts.get(key, 0) >= limit