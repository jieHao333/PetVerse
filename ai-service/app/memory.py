"""Redis 接入层（热缓存 + 并发计数）。

职责：为「结果热缓存」与「单用户并发计数」提供最小可用的 Redis 封装。所有方法都遵守
**故障即降级**：连接异常时读返回 None / 写静默失败，让上层逻辑退化为「缓存未命中」或
「改用进程内计数」，绝不把 Redis 故障放大成用户可见错误。

主要导出：
    - ``init`` / ``check_connection`` / ``close``  生命周期三件套
    - ``cache_get`` / ``cache_set`` / ``cache_delete``  JSON 缓存的读写删
    - ``eval_script``  原子执行 Lua 脚本（并发计数的 INCR/DECR 依赖它）
    - ``get_int``  读取整数计数
    - ``available``  Redis 是否已初始化

外部依赖：``redis.asyncio``、``app.config.settings``。

使用示例：
    from app import memory

    memory.init()
    await memory.check_connection()
    await memory.cache_set("ai:key", {"a": 1}, ttl=300)
    value = await memory.cache_get("ai:key")

注意区分：本模块是 **Redis**；``app.memstore`` 才是 LangGraph 的长期记忆
（PostgreSQL 向量存储），两者都叫 memory 相关但互不相干。
"""

import json
import logging
from typing import Optional

import redis.asyncio as aredis
from redis.exceptions import AuthenticationError

from app.config import settings

logger = logging.getLogger(__name__)

_pool: Optional[aredis.ConnectionPool] = None
_client: Optional[aredis.Redis] = None


def init() -> None:
    """创建 Redis 连接池与客户端（惰性连接，不会在此时发起网络请求）。

    同步函数：``redis.asyncio`` 的池构造不涉及 IO，放在启动钩子里直接调用即可。

    Returns:
        None。

    Raises:
        无（连接失败要等到首次命令才暴露，由 ``check_connection`` 统一探测）。
    """
    global _pool, _client
    _pool = aredis.ConnectionPool(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        # 空密码要转成 None：传空串会被 redis-py 当作「需要 AUTH」而触发无谓的鉴权失败。
        password=settings.REDIS_PASSWORD or None,
        db=settings.REDIS_DB,
        decode_responses=True,
        max_connections=50,
        socket_timeout=3,
        socket_connect_timeout=2,
        # 显式指定 RESP2 协议，避免不同 redis-py 版本的默认协议差异影响连接行为。
        protocol=2,
    )
    _client = aredis.Redis(connection_pool=_pool)


async def check_connection() -> bool:
    """启动期连通性自检。

    只做探测、不阻断启动——Redis 不可用时缓存与并发计数降级，对话主链路仍然可用。

    Returns:
        True 表示 PING 成功；False 表示未初始化或连接/认证失败。

    Raises:
        无（异常在内部转成 False 并记录原因）。
    """
    if _client is None:
        logger.error("Redis 未初始化，结果缓存与并发计数将降级")
        return False
    try:
        await _client.ping()
        logger.info("Redis 连接正常: %s:%s db=%s",
                    settings.REDIS_HOST, settings.REDIS_PORT, settings.REDIS_DB)
        return True
    except AuthenticationError:
        # 认证失败最常见的原因是「服务端没设密码，客户端却配了空串以外的值」，
        # 这里把排查方向直接写进日志，省掉翻文档的时间。
        logger.error(
            "Redis 认证失败：Python 侧配置了 REDIS_PASSWORD，但 Redis 未启用密码或密码不一致。"
            "服务器 Redis 没有密码时请将 ai-service/.env 的 REDIS_PASSWORD 留空；"
            "已启用密码时请与 requirepass / ACL 配置保持一致。地址: %s:%s db=%s",
            settings.REDIS_HOST, settings.REDIS_PORT, settings.REDIS_DB,
            exc_info=True)
        return False
    except Exception:
        logger.error("Redis 连接失败（结果缓存 / 并发计数将降级）: %s:%s db=%s",
                     settings.REDIS_HOST, settings.REDIS_PORT, settings.REDIS_DB,
                     exc_info=True)
        return False


async def close() -> None:
    """关闭客户端与连接池；异常仅记日志。

    Returns:
        None。

    Raises:
        无。
    """
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


async def cache_get(key: str) -> Optional[dict]:
    """读取 JSON 缓存。

    Args:
        key: 缓存键。

    Returns:
        反序列化后的字典；键不存在、未初始化或解析失败时返回 None（对上层等价于「未命中」）。

    Raises:
        无。
    """
    if _client is None:
        return None
    try:
        raw = await _client.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        logger.warning("读取 Redis 缓存失败（忽略）: %s", key, exc_info=True)
        return None


async def cache_set(key: str, value: dict, ttl: int) -> None:
    """写入 JSON 缓存。

    Args:
        key: 缓存键。
        value: 可 JSON 序列化的字典。
        ttl: 过期秒数；``<= 0`` 视为不缓存（避免写入永不失效的脏数据）。

    Returns:
        None。

    Raises:
        无。
    """
    if _client is None or ttl <= 0:
        return
    try:
        await _client.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
    except Exception:
        logger.warning("写入 Redis 缓存失败（忽略）: %s", key, exc_info=True)


async def cache_delete(key: str) -> None:
    """删除缓存键（不存在时静默通过）。

    Args:
        key: 缓存键。

    Returns:
        None。

    Raises:
        无。
    """
    if _client is None:
        return
    try:
        await _client.delete(key)
    except Exception:
        logger.warning("删除 Redis 缓存失败（忽略）: %s", key, exc_info=True)


def available() -> bool:
    """Redis 客户端是否已初始化（仅代表「配置了就绪」，不代表当前网络可达）。

    Returns:
        True 表示可以尝试使用 Redis。

    Raises:
        无。
    """
    return _client is not None


async def eval_script(script: str, key: str, *args) -> Optional[int]:
    """原子执行一段 Lua 脚本。

    并发计数的「读-改-写」必须原子化，否则高并发下会超卖名额；用 Lua 而非事务，
    是因为这里只需单键原子操作，Lua 一次往返即可完成。

    Args:
        script: Lua 脚本源码。
        key: 作为 ``KEYS[1]`` 传入的键。
        *args: 依次作为 ``ARGV[1..n]`` 传入（统一转成字符串给 Redis）。

    Returns:
        脚本返回值的整数形式；未初始化时返回 None。

    Raises:
        Exception: 透传 Redis 异常，由调用方决定是否降级（``limits`` 会捕获）。
    """
    if _client is None:
        return None
    result = await _client.eval(script, 1, key, *args)
    return int(result) if result is not None else None


async def get_int(key: str) -> Optional[int]:
    """读取整数计数。

    Args:
        key: 计数键。

    Returns:
        计数值；键不存在或未初始化时返回 None（调用方需区分「0」与「不知道」）。

    Raises:
        Exception: 透传 Redis 异常（``limits.is_busy`` 会捕获并降级到进程内计数）。
    """
    if _client is None:
        return None
    raw = await _client.get(key)
    return int(raw) if raw is not None else None
