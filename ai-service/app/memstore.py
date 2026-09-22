"""LangGraph 官方 runtime store 长期记忆(PostgreSQL 存储)

记忆机制:
  长期记忆(用户偏好 / 宠物习性)由 LangGraph 官方 PostgresStore 持久化,
  与 checkpoint(会话级短期记忆)同库不同表、生命周期解耦:
  - 命名空间按「用户 / 宠物」两级隔离(见 user_ns / pet_ns);
  - 删除会话只删 checkpoint 与业务表,长期记忆跨会话保留;
  - 每轮对话结束后由 longterm.extract_and_apply 后台抽取写入,
    recall_memory 节点在图内读取注入 System Prompt。

为什么这样接(Windows 兼容):
  与 checkpoint.py 同一约束——官方 PostgresStore 是同步实现(psycopg 同步连接),
  官方 AsyncPostgresStore 的 psycopg 异步连接在 Windows ProactorEventLoop 下不可用;
  因此保留官方存储引擎与建表迁移,仅补一层线程适配:异步方法统一经
  asyncio.to_thread 代理执行,连接参数与官方 from_conn_string 保持一致
  (autocommit / prepare_threshold=0 / row_factory=dict_row)。

存储韧性(PG 故障不阻断对话,与 checkpoint 同一套降级哲学):
  - 读(注入记忆)失败降级为「无长期记忆」;
  - 写(抽取结果)失败静默仅记日志;
  - 建表(setup)失败带冷却重试,PG 恢复后无需重启服务即可重新启用长期记忆。
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

# store 连接池参数:读(每轮对话 1 次)与写(每轮结束 1 次)均为毫秒级短操作,小池足够
_POOL_MIN = 1
_POOL_MAX = 2
# 从池取连接的等待上限(秒):超过即视为 PG 不可用并立即降级
_POOL_TIMEOUT = 5.0
# setup(建表)失败后的重试冷却(秒):PG 未就绪期间不反复打建表请求
_SETUP_RETRY_COOLDOWN = 30.0

# 命名空间前缀:便于在 store 表中识别业务来源与人工排查
_ROOT_NS = "petverse"


def user_ns(user_id: int) -> tuple[str, ...]:
    """用户级记忆命名空间(用户偏好 / 喂养理念等与具体宠物无关的信息)"""
    return (_ROOT_NS, "user_mem", str(user_id))


def pet_ns(user_id: int, pet_id: int) -> tuple[str, ...]:
    """宠物级记忆命名空间(pet_id 转 str:雪花 ID 经 JSON 下发会精度丢失,统一字符串化)"""
    return (_ROOT_NS, "pet_mem", str(user_id), str(pet_id))


def user_prefix(user_id: int) -> tuple[str, ...]:
    """用户全部记忆的命名空间前缀(user 级 + 所有宠物级,管理接口遍历用)"""
    return (_ROOT_NS, "user_mem", str(user_id))


def pet_prefix(user_id: int) -> tuple[str, ...]:
    """该用户全部宠物级记忆的命名空间前缀"""
    return (_ROOT_NS, "pet_mem", str(user_id))


class _ThreadedStore(PostgresStore):
    """官方 PostgresStore 的异步线程适配 + 存储降级

    官方同步方法(建表/读写)在线程池中执行:既不阻塞事件循环,
    也兼容 Windows ProactorEventLoop;PG 故障时读返回 None / 空、写静默失败,
    任何存储异常都不向对话主流程传播(与 checkpoint / persistence 的降级策略一致)。
    """

    def __init__(self, pool: ConnectionPool) -> None:
        super().__init__(conn=pool)
        self._ready = False       # store 表是否已就绪
        self._last_setup = 0.0    # 上次建表尝试时刻(monotonic),用于冷却重试

    async def _ensure_ready(self) -> bool:
        """确保 store 表就绪;失败带冷却重试,绝不抛出"""
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

    # ---------- 异步接口:线程代理 + 异常降级 ----------

    async def aget(self, namespace: tuple, key: str, *, refresh_ttl=None):
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
        if not await self._ensure_ready():
            return
        try:
            await asyncio.to_thread(self.put, namespace, key, value)
        except Exception:
            logger.warning("写入长期记忆失败(忽略): ns=%s key=%s", namespace, key, exc_info=True)

    async def adelete(self, namespace: tuple, key: str) -> None:
        if not await self._ensure_ready():
            return
        try:
            await asyncio.to_thread(self.delete, namespace, key)
        except Exception:
            logger.warning("删除长期记忆失败(忽略): ns=%s key=%s", namespace, key, exc_info=True)


# 模块级单例(由 main.py 的 lifespan 启动 / 停机时初始化与关闭)
_store: Optional[_ThreadedStore] = None
_pool: Optional[ConnectionPool] = None


def _init_sync() -> None:
    """在线程中创建连接池与 store(psycopg 池创建为同步操作)

    open(wait=False):PG 未就绪时不阻塞启动,连接由池在首次使用时按需建立(池内建重试);
    连接参数与官方 from_conn_string 的 pool_config 分支保持一致
    (autocommit / prepare_threshold=0 / row_factory=dict_row)。
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
    """初始化长期记忆 store(服务启动时调用一次);失败不抛出,长期记忆自动降级"""
    global _store
    try:
        await asyncio.to_thread(_init_sync)
        logger.info("LangGraph 长期记忆 store 已初始化(PostgreSQL): %s@%s:%s/%s",
                    settings.PG_USER, settings.PG_HOST, settings.PG_PORT, settings.PG_DB)
    except Exception:
        logger.warning("LangGraph 长期记忆 store 初始化失败,长期记忆将降级为不可用", exc_info=True)
        _store = None


async def close() -> None:
    """关闭长期记忆连接池(服务停机时调用)"""
    global _pool, _store
    pool, _pool, _store = _pool, None, None
    if pool is not None:
        try:
            await asyncio.to_thread(pool.close)
        except Exception:
            logger.warning("关闭 LangGraph store 连接池异常(忽略)", exc_info=True)


def get_store() -> Optional[BaseStore]:
    """获取 store(供图编译时注入 runtime store);未初始化返回 None,图退化为无长期记忆"""
    return _store


def available() -> bool:
    """长期记忆是否可用(供路由层决定是否触发后台抽取)"""
    return _store is not None
