"""LangGraph 官方 checkpoint 会话记忆(PostgreSQL 存储)

记忆机制:
  对话图状态(messages 通道)由 LangGraph 官方 PostgresSaver 在每个超级步自动
  持久化到 PostgreSQL,thread_id = 「用户 + 会话」;下一轮对话自动从 checkpoint
  恢复历史,不再手工读写上下文缓存。用户中止生成时,路由层把用户问题与已产出的
  部分回复补写回图状态(见 chat._persist_interrupted),被中断的轮次同样进入
  后续对话的记忆。

为什么这样接(Windows 兼容):
  官方 PostgresSaver 是同步实现(psycopg 同步连接);本服务在 Windows 上运行,
  psycopg 的异步连接在默认 ProactorEventLoop 下不可用(与 pg_store.py 同一约束),
  因此保留官方存储引擎与建表迁移,仅补一层线程适配——异步方法统一经
  asyncio.to_thread 代理执行,与项目既有 PG 用法(同步池 + to_thread)一致。

存储韧性(PG 故障不阻断对话):
  - 读(恢复记忆)失败降级为「无历史」;写(保存状态)失败静默仅记日志;
  - 建表(setup)失败带冷却重试,PG 恢复后无需重启服务即可重新启用记忆。
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

# checkpoint 连接池参数:读写均为毫秒级短操作,小池足够(全局对话并发闸 20 路)
_POOL_MIN = 1
_POOL_MAX = 4
# 从池取连接的等待上限(秒):超过即视为 PG 不可用并立即降级
_POOL_TIMEOUT = 5.0
# setup(建表)失败后的重试冷却(秒):PG 未就绪期间不反复打建表请求
_SETUP_RETRY_COOLDOWN = 30.0
# thread_id 前缀:便于在 checkpoint 表中识别业务来源与人工排查
_THREAD_PREFIX = "petverse-chat"


class _ThreadedMemorySaver(PostgresSaver):
    """官方 PostgresSaver 的异步线程适配 + 存储降级

    官方同步方法(建表/读写)在线程池中执行:既不阻塞事件循环,
    也兼容 Windows ProactorEventLoop;PG 故障时读返回 None / 空、写静默失败,
    任何存储异常都不向对话主流程传播(与 memory / persistence 的降级策略一致)。
    """

    def __init__(self, pool: ConnectionPool) -> None:
        super().__init__(pool)
        self._ready = False       # checkpoint 表是否已就绪
        self._last_setup = 0.0    # 上次建表尝试时刻(monotonic),用于冷却重试

    async def _ensure_ready(self) -> bool:
        """确保 checkpoint 表就绪;失败带冷却重试,绝不抛出"""
        if self._ready:
            return True
        if time.monotonic() - self._last_setup < _SETUP_RETRY_COOLDOWN:
            return False
        self._last_setup = time.monotonic()
        try:
            await asyncio.to_thread(self.setup)
            self._ready = True
            logger.info("LangGraph checkpoint 表已就绪")
        except Exception:
            logger.warning("LangGraph checkpoint 表初始化失败(记忆暂不可用, %ss 后自动重试)",
                           int(_SETUP_RETRY_COOLDOWN), exc_info=True)
        return self._ready

    # ---------- 异步接口:线程代理 + 异常降级 ----------

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
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
        if not await self._ensure_ready():
            return config
        try:
            return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)
        except Exception:
            logger.warning("写入对话 checkpoint 失败(本轮记忆不落库,对话不受影响)", exc_info=True)
            return config

    async def aput_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]],
                          task_id: str, task_path: str = "") -> None:
        if not await self._ensure_ready():
            return
        try:
            await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)
        except Exception:
            logger.warning("写入对话 checkpoint 中间结果失败(忽略)", exc_info=True)

    async def adelete_thread(self, thread_id: str) -> None:
        if not await self._ensure_ready():
            return
        try:
            await asyncio.to_thread(self.delete_thread, thread_id)
        except Exception:
            logger.warning("删除对话 checkpoint 失败(忽略): thread=%s", thread_id, exc_info=True)


# 模块级单例(由 main.py 的 lifespan 启动 / 停机时初始化与关闭)
_saver: Optional[_ThreadedMemorySaver] = None
_pool: Optional[ConnectionPool] = None


def _init_sync() -> None:
    """在线程中创建连接池与 saver(psycopg 池创建为同步操作)

    open(wait=False):PG 未就绪时不阻塞启动,连接由池在首次使用时按需建立(池内建重试);
    连接参数与官方 from_conn_string 保持一致(autocommit / prepare_threshold=0 /
    row_factory=dict_row),保证官方 SQL 的行为与行访问方式正确。
    """
    global _pool, _saver
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
    _saver = _ThreadedMemorySaver(pool)


async def init() -> None:
    """初始化 checkpoint 存储(服务启动时调用一次);失败不抛出,记忆功能自动降级"""
    global _saver
    try:
        await asyncio.to_thread(_init_sync)
        logger.info("LangGraph checkpoint 已初始化(PostgreSQL): %s@%s:%s/%s",
                    settings.PG_USER, settings.PG_HOST, settings.PG_PORT, settings.PG_DB)
    except Exception:
        logger.warning("LangGraph checkpoint 初始化失败,对话记忆将降级为无记忆", exc_info=True)
        _saver = None


async def close() -> None:
    """关闭 checkpoint 连接池(服务停机时调用)"""
    global _pool, _saver
    pool, _pool, _saver = _pool, None, None
    if pool is not None:
        try:
            await asyncio.to_thread(pool.close)
        except Exception:
            logger.warning("关闭 LangGraph checkpoint 连接池异常(忽略)", exc_info=True)


def get_saver() -> Optional[BaseCheckpointSaver]:
    """获取 saver(供图编译时注入 checkpoint);未初始化返回 None,图退化为无记忆"""
    return _saver


def available() -> bool:
    """checkpoint 是否可用(供路由层决定是否读写图状态)"""
    return _saver is not None


def thread_config(user_id: int, session_id: int) -> dict:
    """构造 checkpoint 线程配置:thread_id = 「用户 + 会话」(会话级记忆隔离)"""
    return {"configurable": {"thread_id": f"{_THREAD_PREFIX}:{user_id}:{session_id}"}}


async def adelete_thread(user_id: int, session_id: int) -> None:
    """删除会话对应的 checkpoint 记忆(与 PostgreSQL 会话消息删除联动;失败仅记日志)"""
    saver = _saver
    if saver is None:
        return
    await saver.adelete_thread(f"{_THREAD_PREFIX}:{user_id}:{session_id}")
