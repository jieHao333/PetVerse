"""长期记忆存储（LangGraph PostgresStore）。

职责：以「用户 / 宠物」两级命名空间保存跨会话的长期记忆条目（偏好、习性、事实），
并同样提供 Windows 可用的异步包装（线程池代理同步实现）。

主要导出：
    - 命名空间工具：``user_ns`` / ``pet_ns`` / ``user_prefix`` / ``pet_prefix``
    - ``init`` / ``close`` / ``get_store`` / ``available``  生命周期
    - ``_ThreadedStore``  异步适配的 store 实现

外部依赖：``langgraph-checkpoint-postgres``（PostgresStore）、``psycopg_pool``、
``app.config.settings``。

使用示例：
    from app import memstore

    store = memstore.get_store()
    if store is not None:
        await store.aput(memstore.user_ns(42), "budget", {"content": "预算每月 200 元左右"})
        items = await store.asearch(memstore.pet_ns(42, 7), limit=20)

命名空间约定（层级固定，靠下标定位，勿随意调整顺序）：
    ("petverse", "user_mem", "<user_id>")             用户级记忆
    ("petverse", "pet_mem", "<user_id>", "<pet_id>")  宠物级记忆
"""

import asyncio
import logging
import time
from typing import Any, Optional

from langgraph.store.base import BaseStore
from langgraph.store.postgres import PostgresStore
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)

_POOL_MIN = 1
_POOL_MAX = 2
_POOL_TIMEOUT = 5.0
_SETUP_RETRY_COOLDOWN = 30.0

# 命名空间根，便于同一库里与其他 LangGraph 应用隔离。
_ROOT_NS = "petverse"


def user_ns(user_id: int) -> tuple[str, ...]:
    """用户级记忆命名空间。

    Args:
        user_id: 用户 ID。

    Returns:
        三元组命名空间。

    Raises:
        无。
    """
    return (_ROOT_NS, "user_mem", str(user_id))


def pet_ns(user_id: int, pet_id: int) -> tuple[str, ...]:
    """宠物级记忆命名空间。

    Args:
        user_id: 用户 ID（放在 pet_id 之前，保证同一用户的宠物记忆聚在同一前缀下）。
        pet_id: 宠物 ID。

    Returns:
        四元组命名空间。

    Raises:
        无。
    """
    return (_ROOT_NS, "pet_mem", str(user_id), str(pet_id))


def user_prefix(user_id: int) -> tuple[str, ...]:
    """用户记忆的检索前缀（用于按前缀列出该用户全部用户级记忆）。

    Args:
        user_id: 用户 ID。

    Returns:
        命名空间前缀。

    Raises:
        无。
    """
    return (_ROOT_NS, "user_mem", str(user_id))


def pet_prefix(user_id: int) -> tuple[str, ...]:
    """宠物记忆的检索前缀（跨该用户所有宠物）。

    Args:
        user_id: 用户 ID。

    Returns:
        命名空间前缀。

    Raises:
        无。
    """
    return (_ROOT_NS, "pet_mem", str(user_id))


class _ThreadedStore(PostgresStore):
    """把 ``PostgresStore`` 的同步实现桥接成异步接口。

    与 ``checkpoint._ThreadedMemorySaver`` 同因同治：Windows 下 psycopg 异步连接不可用，
    故异步方法统一经线程池调用同步实现，并对失败做静默降级。
    """

    def __init__(self, pool: ConnectionPool) -> None:
        super().__init__(conn=pool)
        self._ready = False
        self._last_setup = 0.0

    async def _ensure_ready(self) -> bool:
        """确保 store 表已建好（带冷却的惰性建表）。

        Returns:
            True 表示可读写；False 表示当前不可用。

        Raises:
            无。
        """
        if self._ready:
            return True
        if time.monotonic() - self._last_setup < _SETUP_RETRY_COOLDOWN:
            return False
        self._last_setup = time.monotonic()
        try:
            await asyncio.to_thread(self.setup)
            self._ready = True
            logger.info("LangGraph 长期记忆 store 表已就绪")
        except Exception:
            logger.warning("LangGraph store 表初始化失败(长期记忆暂不可用, %ss 后自动重试)",
                           int(_SETUP_RETRY_COOLDOWN), exc_info=True)
        return self._ready


    async def aget(self, namespace: tuple, key: str, *, refresh_ttl=None):
        """按命名空间 + key 读取单条记忆。

        Args:
            namespace: 目标命名空间。
            key: 记忆键。
            refresh_ttl: LangGraph 接口签名保留项；本地实现不涉及 TTL。

        Returns:
            记忆条目；不存在或读取失败时返回 None。

        Raises:
            无。
        """
        if not await self._ensure_ready():
            return None
        try:
            return await asyncio.to_thread(self.get, namespace, key)
        except Exception:
            logger.warning("读取长期记忆失败(忽略): ns=%s key=%s", namespace, key, exc_info=True)
            return None

    async def asearch(self, namespace_prefix: tuple, /, *, query=None,
                      filter=None, limit: int = 10, offset: int = 0,
                      refresh_ttl=None) -> list:
        """按命名空间前缀列出记忆条目。

        实现说明：只做前缀枚举，**不使用** ``query`` 做语义检索、也**不应用** ``filter``——
        每个命名空间下的条目本来就少（受 ``MEMORY_MAX_ITEMS`` 限制），全量取出再拼进 Prompt
        比引入向量检索更简单可控。参数保留是为了满足 ``BaseStore`` 的接口签名。

        Args:
            namespace_prefix: 命名空间前缀。
            query: 语义检索语句（当前实现忽略）。
            filter: 键值过滤条件（当前实现忽略）。
            limit: 返回条数上限。
            offset: 偏移量。
            refresh_ttl: 保留项。

        Returns:
            记忆条目列表；不可用时返回空列表。

        Raises:
            无。
        """
        if not await self._ensure_ready():
            return []
        try:
            return list(await asyncio.to_thread(
                lambda: self.search(namespace_prefix, limit=limit, offset=offset)))
        except Exception:
            logger.warning("检索长期记忆失败(降级为空): ns=%s", namespace_prefix, exc_info=True)
            return []

    async def aput(self, namespace: tuple, key: str, value: dict,
                   index=None, *, ttl=None) -> None:
        """写入 / 覆盖一条记忆。

        Args:
            namespace: 目标命名空间。
            key: 记忆键（新增用 uuid，更新沿用原 key）。
            value: 记忆内容字典（content / kind / petName / createdAt / updatedAt）。
            index: 向量索引配置（当前未使用）。
            ttl: 过期时间（当前未使用，长期记忆不设 TTL）。

        Returns:
            None。

        Raises:
            无。
        """
        if not await self._ensure_ready():
            return
        try:
            await asyncio.to_thread(self.put, namespace, key, value)
        except Exception:
            logger.warning("写入长期记忆失败(忽略): ns=%s key=%s", namespace, key, exc_info=True)

    async def adelete(self, namespace: tuple, key: str) -> None:
        """删除一条记忆。

        Args:
            namespace: 目标命名空间。
            key: 记忆键。

        Returns:
            None。

        Raises:
            无。
        """
        if not await self._ensure_ready():
            return
        try:
            await asyncio.to_thread(self.delete, namespace, key)
        except Exception:
            logger.warning("删除长期记忆失败(忽略): ns=%s key=%s", namespace, key, exc_info=True)


_store: Optional[_ThreadedStore] = None
_pool: Optional[ConnectionPool] = None


def _init_sync() -> None:
    """同步创建连接池与 store（供 ``asyncio.to_thread`` 调用）。

    Returns:
        None。

    Raises:
        Exception: 连接池构造失败时抛出。
    """
    global _pool, _store
    pool = ConnectionPool(
        conninfo=settings.pg_dsn,
        min_size=_POOL_MIN,
        max_size=_POOL_MAX,
        timeout=_POOL_TIMEOUT,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    pool.open(wait=False)
    _pool = pool
    _store = _ThreadedStore(pool)


async def init() -> None:
    """初始化 store；失败时长期记忆整体降级为不可用，不影响对话。

    Returns:
        None。

    Raises:
        无。
    """
    global _store
    try:
        await asyncio.to_thread(_init_sync)
        logger.info("LangGraph 长期记忆 store 已初始化(PostgreSQL): %s@%s:%s/%s",
                    settings.PG_USER, settings.PG_HOST, settings.PG_PORT, settings.PG_DB)
    except Exception:
        logger.warning("LangGraph 长期记忆 store 初始化失败,长期记忆将降级为不可用", exc_info=True)
        _store = None


async def close() -> None:
    """关闭连接池并清空 store 引用。

    Returns:
        None。

    Raises:
        无。
    """
    global _pool, _store
    pool, _pool, _store = _pool, None, None
    if pool is not None:
        try:
            await asyncio.to_thread(pool.close)
        except Exception:
            logger.warning("关闭 LangGraph store 连接池异常(忽略)", exc_info=True)


def get_store() -> Optional[BaseStore]:
    """获取 store 实例（图编译注入与路由层直接用）。

    Returns:
        store；未初始化成功时返回 None。

    Raises:
        无。
    """
    return _store


def available() -> bool:
    """store 是否可用（仅代表对象已创建）。

    Returns:
        True 表示可以尝试读写长期记忆。

    Raises:
        无。
    """
    return _store is not None
