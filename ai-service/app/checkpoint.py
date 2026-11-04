"""LangGraph 对话记忆（checkpoint）适配层。

职责：把 LangGraph 官方的 ``PostgresSaver`` 包装成「Windows 可用的异步 saver」。官方实现走
psycopg 异步连接，而本服务运行在 Windows 默认的 ProactorEventLoop 上（psycopg 异步连接不可用），
因此这里把所有异步方法改为**经线程池调用同步实现**，对外仍是标准的 ``BaseCheckpointSaver``。

主要导出：
    - ``init`` / ``close``        连接池与 saver 生命周期
    - ``get_saver``               取 saver（未就绪时返回 None，图编译时允许 None）
    - ``available``               saver 是否可用
    - ``thread_config``           生成 ``thread_id`` 配置（用户 + 会话）
    - ``adelete_thread``          删除某会话的全部记忆

外部依赖：``langgraph-checkpoint-postgres``、``psycopg`` / ``psycopg_pool``、
``app.config.settings``。

使用示例：
    from app import checkpoint

    config = checkpoint.thread_config(user_id=42, session_id=7)
    graph = get_chat_graph()                      # 编译时已注入 checkpoint.get_saver()
    state = await graph.aget_state(config)        # 取回上一轮上下文

设计取舍：所有读失败一律降级为「无历史」、写失败仅记日志。记忆是增强项，
不能因为 PG 抖动就让用户这一轮问不出话。
"""

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Optional, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)

# 记忆链路独立于业务库连接池：池小、单独限额，避免大量对话把业务查询的连接抢光。
_POOL_MIN = 1
_POOL_MAX = 4
_POOL_TIMEOUT = 5.0
# 建表失败冷却，避免 PG 未就绪时每个超级步都去重试建表。
_SETUP_RETRY_COOLDOWN = 30.0
# thread_id 前缀：同一张表里可能还有别的图，用前缀区分用途，删除时也不会误删。
_THREAD_PREFIX = "petverse-chat"


class _ThreadedMemorySaver(PostgresSaver):
    """把 ``PostgresSaver`` 的同步实现桥接成异步接口。

    每个异步方法都是「确保就绪 → ``asyncio.to_thread`` 调同步实现 → 异常降级」三段式，
    这样调用方（LangGraph 运行时）感知不到线程边界。
    """

    def __init__(self, pool: ConnectionPool) -> None:
        super().__init__(pool)
        self._ready = False
        self._last_setup = 0.0

    async def _ensure_ready(self) -> bool:
        """确保 checkpoint 表已建好（带冷却的惰性建表）。

        Returns:
            True 表示可以读写；False 表示当前不可用，调用方应走降级分支。

        Raises:
            无。
        """
        if self._ready:
            return True
        if time.monotonic() - self._last_setup < _SETUP_RETRY_COOLDOWN:
            return False
        self._last_setup = time.monotonic()
        try:
            # setup() 是同步建表方法，必须放线程池，否则阻塞事件循环。
            await asyncio.to_thread(self.setup)
            self._ready = True
            logger.info("LangGraph checkpoint 表已就绪")
        except Exception:
            logger.warning("LangGraph checkpoint 表初始化失败(记忆暂不可用, %ss 后自动重试)",
                           int(_SETUP_RETRY_COOLDOWN), exc_info=True)
        return self._ready


    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """读取某 thread 的最新 checkpoint。

        Args:
            config: LangGraph 运行配置（含 ``thread_id``）。

        Returns:
            checkpoint 元组；不可用或读取失败时返回 None（等价于「没有历史」）。

        Raises:
            无。
        """
        if not await self._ensure_ready():
            return None
        try:
            return await asyncio.to_thread(self.get_tuple, config)
        except Exception:
            logger.warning("读取对话 checkpoint 失败(本轮按无历史处理)", exc_info=True)
            return None

    async def alist(self, config: Optional[RunnableConfig], *,
                    filter: Optional[dict] = None,
                    before: Optional[RunnableConfig] = None,
                    limit: Optional[int] = None) -> AsyncIterator[CheckpointTuple]:
        """按条件列举 checkpoint。

        同步实现返回生成器，无法直接跨线程迭代，因此先 ``list`` 物化再逐条 yield。

        Args:
            config: 运行配置；None 表示不限 thread。
            filter: 元数据过滤条件。
            before: 只取该 checkpoint 之前的记录。
            limit: 条数上限。

        Yields:
            CheckpointTuple。

        Raises:
            无。
        """
        if not await self._ensure_ready():
            return
        try:
            items = await asyncio.to_thread(
                lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        except Exception:
            logger.warning("列举对话 checkpoint 失败(降级为空)", exc_info=True)
            return
        for item in items:
            yield item

    async def aput(self, config: RunnableConfig, checkpoint: Checkpoint,
                   metadata: CheckpointMetadata,
                   new_versions: ChannelVersions) -> RunnableConfig:
        """写入一个 checkpoint（每个超级步调用一次）。

        Args:
            config: 运行配置。
            checkpoint: 状态快照。
            metadata: 元数据（来源、步号等）。
            new_versions: 本步各 channel 的版本号。

        Returns:
            写入后的配置；失败时原样返回入参，图执行继续（记忆缺一步好过对话中断）。

        Raises:
            无。
        """
        if not await self._ensure_ready():
            return config
        try:
            return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)
        except Exception:
            logger.warning("写入对话 checkpoint 失败(本轮记忆不落库,对话不受影响)", exc_info=True)
            return config

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]],
                          task_id: str, task_path: str = "") -> None:
        """写入节点的中间结果（LangGraph 用于并行分支的暂存与恢复）。

        Args:
            config: 运行配置。
            writes: ``(channel, value)`` 列表。
            task_id: 产生这些写入的任务 ID。
            task_path: 任务在图中的路径。

        Returns:
            None。

        Raises:
            无。
        """
        if not await self._ensure_ready():
            return
        try:
            await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)
        except Exception:
            logger.warning("写入对话 checkpoint 中间结果失败(忽略)", exc_info=True)

    async def adelete_thread(self, thread_id: str) -> None:
        """删除一个 thread 的全部记忆（删会话时调用）。

        Args:
            thread_id: 线程标识（由 ``thread_config`` 生成）。

        Returns:
            None。

        Raises:
            无。
        """
        if not await self._ensure_ready():
            return
        try:
            await asyncio.to_thread(self.delete_thread, thread_id)
        except Exception:
            logger.warning("删除对话 checkpoint 失败(忽略): thread=%s", thread_id, exc_info=True)


_saver: Optional[_ThreadedMemorySaver] = None
_pool: Optional[ConnectionPool] = None


def _init_sync() -> None:
    """同步创建连接池与 saver（供 ``asyncio.to_thread`` 调用）。

    Returns:
        None。

    Raises:
        Exception: 连接池构造失败时抛出。
    """
    global _pool, _saver
    pool = ConnectionPool(
        conninfo=settings.pg_dsn,
        min_size=_POOL_MIN,
        max_size=_POOL_MAX,
        timeout=_POOL_TIMEOUT,
        # autocommit + prepare_threshold=0：LangGraph 的建表语句与动态 SQL 不适合
        # 服务端预编译；dict_row 让同步实现直接拿到字典形式的结果行。
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    pool.open(wait=False)
    _pool = pool
    _saver = _ThreadedMemorySaver(pool)


async def init() -> None:
    """初始化 checkpoint saver；失败降级为「无记忆」但服务继续启动。

    Returns:
        None。

    Raises:
        无。
    """
    global _saver
    try:
        await asyncio.to_thread(_init_sync)
        logger.info("LangGraph checkpoint 已初始化(PostgreSQL): %s@%s:%s/%s",
                    settings.PG_USER, settings.PG_HOST, settings.PG_PORT, settings.PG_DB)
    except Exception:
        logger.warning("LangGraph checkpoint 初始化失败,对话记忆将降级为无记忆", exc_info=True)
        _saver = None


async def close() -> None:
    """关闭连接池并清空 saver 引用。

    Returns:
        None。

    Raises:
        无。
    """
    global _pool, _saver
    pool, _pool, _saver = _pool, None, None
    if pool is not None:
        try:
            await asyncio.to_thread(pool.close)
        except Exception:
            logger.warning("关闭 LangGraph checkpoint 连接池异常(忽略)", exc_info=True)


def get_saver() -> Optional[BaseCheckpointSaver]:
    """获取 saver 实例，供图编译时注入。

    Returns:
        saver；未初始化成功时返回 None——``StateGraph.compile(checkpointer=None)`` 是合法的，
        此时图退化为无记忆执行。

    Raises:
        无。
    """
    return _saver


def available() -> bool:
    """saver 是否可用（仅代表对象已创建，不代表此刻 PG 可达）。

    Returns:
        True 表示可以尝试读写记忆。

    Raises:
        无。
    """
    return _saver is not None


def thread_config(user_id: int, session_id: int) -> dict:
    """构造 LangGraph 运行配置。

    thread_id 用「用户 + 会话」两个维度拼成：会话级隔离保证不同会话互不串味，
    带上 user_id 则顺手形成归属校验，跨用户猜 session_id 也读不到别人的记忆。

    Args:
        user_id: 用户 ID。
        session_id: 会话 ID。

    Returns:
        可直接传给 ``graph.ainvoke`` / ``aget_state`` 的 config 字典。

    Raises:
        无。
    """
    return {"configurable": {"thread_id": f"{_THREAD_PREFIX}:{user_id}:{session_id}"}}


async def adelete_thread(user_id: int, session_id: int) -> None:
    """删除指定会话的对话记忆。

    Args:
        user_id: 用户 ID。
        session_id: 会话 ID。

    Returns:
        None。

    Raises:
        无（saver 不可用时直接返回）。
    """
    saver = _saver
    if saver is None:
        return
    await saver.adelete_thread(f"{_THREAD_PREFIX}:{user_id}:{session_id}")
