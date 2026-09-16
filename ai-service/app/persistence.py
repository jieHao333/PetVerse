"""PostgreSQL 会话与对话消息持久化（psycopg3 同步连接池 + asyncio.to_thread）

存储表（与 LangGraph checkpoint、pgvector 同在 petverse_ai 库）：
  - chat_session：会话表（user_id + pet_id 隔离，一只宠物可建多个会话）
  - chat_message：消息表（session_id 关联会话）

为什么用同步连接池：与 pg_store / checkpoint / vectorstore 同一约束——psycopg 的
异步连接在 Windows 默认的 ProactorEventLoop 下不可用；同步池 + asyncio.to_thread
跨平台一致，且 psycopg_pool 自带断线重连与坏连接淘汰，无需手写池重建逻辑。

设计要点：
  1. 本层是对话历史的持久层（前端展示 / 会话管理从这里读）；LLM 上下文记忆
     由 LangGraph checkpoint 承载，两者定位不同、互不替代；
  2. 业务表由 init() 自动创建（CREATE TABLE IF NOT EXISTS，幂等）；建表失败
     带 30s 冷却重试，PG 恢复后无需重启服务；
  3. 会话管理方法（create/list/delete_session、get_session）失败时抛出异常，
     由路由层转换为业务错误响应——会话是对话的前提，不允许静默降级；
  4. 消息读写方法均做异常降级：PG 故障时绝不让对话主流程报错，
     save_* 静默失败仅记日志，read_history 返回空列表；
  5. save_turn_if_exists 用于流式对话客户端中途断开的兜底保存——
     若用户此刻已删除该会话（chat_session 无行），跳过写入避免复活历史；
     用户消息无条件落库（即使中止时尚未产出任何回复），保证记忆完整；
  6. 每轮对话显式刷新会话 update_time（列表按最近活跃排序；PG 无 MySQL 的
     ON UPDATE CURRENT_TIMESTAMP，等价语义在此显式实现）。
"""
import asyncio
import logging
import time
from typing import List, Optional

from psycopg_pool import ConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)

# 会话标题最大长度（与 chat_session.title VARCHAR(64) 对齐）
TITLE_MAX_LEN = 30

# 建表失败后的重试冷却（秒）：PG 未就绪期间不反复打建表请求
_TABLES_RETRY_COOLDOWN = 30.0

# 连接池单例（由 main.py 的 lifespan 在启动 / 停机时初始化与关闭）
_pool: Optional[ConnectionPool] = None
_tables_ready = False      # 业务表是否已建好
_last_tables_try = 0.0     # 上次建表尝试时刻（monotonic），用于冷却重试

# 业务表 DDL（幂等；服务启动时自动执行，等价定义见 db/schema_pgvector.sql）：
# - 时间戳统一 TIMESTAMPTZ；update_time 由写入轮次时显式刷新
# - interrupted 用 BOOLEAN（仅 assistant 消息置位）
_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS chat_session (
         id          BIGSERIAL   PRIMARY KEY,
         user_id     BIGINT      NOT NULL,
         pet_id      BIGINT      NOT NULL DEFAULT 0,
         title       VARCHAR(64) NOT NULL DEFAULT '新会话',
         create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
       )""",
    """CREATE INDEX IF NOT EXISTS idx_chat_session_user_pet_update
         ON chat_session (user_id, pet_id, update_time DESC)""",
    """CREATE TABLE IF NOT EXISTS chat_message (
         id          BIGSERIAL   PRIMARY KEY,
         user_id     BIGINT      NOT NULL,
         pet_id      BIGINT      NOT NULL DEFAULT 0,
         session_id  BIGINT      NOT NULL DEFAULT 0,
         role        VARCHAR(16) NOT NULL,
         content     TEXT        NOT NULL,
         interrupted BOOLEAN     NOT NULL DEFAULT FALSE,
         create_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
       )""",
    """CREATE INDEX IF NOT EXISTS idx_chat_message_user_session_time
         ON chat_message (user_id, session_id, create_time)""",
    """CREATE INDEX IF NOT EXISTS idx_chat_message_user_pet_time
         ON chat_message (user_id, pet_id, create_time)""",
)


def _init_sync() -> None:
    """在线程中创建连接池（psycopg 池创建为同步操作）

    open(wait=False)：PG 未就绪时不阻塞启动，连接由池在首次使用时按需建立；
    池自带断线重连与「坏连接淘汰」，PG 恢复后自动回到可用状态。
    """
    global _pool
    _pool = ConnectionPool(
        conninfo=settings.pg_dsn,
        min_size=settings.PG_POOL_MIN,
        max_size=settings.PG_POOL_MAX,
        timeout=5.0,        # 取连接等待上限（秒）：超过视为 PG 不可用，快速降级
        open=False,
    )
    _pool.open(wait=False)


async def init() -> None:
    """初始化连接池并确保业务表存在（服务启动时调用一次）；失败不抛出，读写自动降级"""
    global _pool
    try:
        await asyncio.to_thread(_init_sync)
        logger.info("PostgreSQL 连接池已初始化（会话与消息）: %s@%s:%s/%s",
                    settings.PG_USER, settings.PG_HOST, settings.PG_PORT, settings.PG_DB)
    except Exception:
        logger.warning("PostgreSQL 连接池初始化失败，会话与对话持久化将不可用", exc_info=True)
        _pool = None
        return
    await _ensure_tables()


async def _ensure_tables() -> bool:
    """确保业务表已建好；失败带冷却重试，绝不抛出（返回当前是否就绪）"""
    global _tables_ready, _last_tables_try
    if _tables_ready:
        return True
    pool = _pool
    if pool is None:
        return False
    if time.monotonic() - _last_tables_try < _TABLES_RETRY_COOLDOWN:
        return False
    _last_tables_try = time.monotonic()

    def _create() -> None:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                for ddl in _SCHEMA_STATEMENTS:
                    cur.execute(ddl)

    try:
        await asyncio.to_thread(_create)
        _tables_ready = True
        logger.info("对话业务表已就绪: chat_session / chat_message")
    except Exception:
        logger.warning("对话业务表初始化失败（持久化暂不可用，%ss 后自动重试）",
                       int(_TABLES_RETRY_COOLDOWN), exc_info=True)
    return _tables_ready


async def close() -> None:
    """关闭连接池（服务停机时调用）"""
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        try:
            await asyncio.to_thread(pool.close)
        except Exception:
            logger.warning("关闭 PostgreSQL 连接池异常（忽略）", exc_info=True)


async def _run(sql: str, params: tuple = (), fetch: bool = False):
    """在线程中执行单条 SQL；池 / 表未就绪时抛 RuntimeError，由调用方决定降级或上抛

    连接上下文（pool.connection()）正常退出时自动提交；连接已断时 psycopg_pool
    会丢弃该连接并重建，实现「PG 重启 / 网络闪断后无需重启服务」的自愈。
    """
    if not await _ensure_tables():
        raise RuntimeError("PostgreSQL 未就绪")

    def _exec():
        with _pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None

    return await asyncio.to_thread(_exec)


async def _run_tx(statements: List[tuple]) -> None:
    """在线程中以单事务执行多条 SQL（全成或全败）；未就绪时抛 RuntimeError"""
    if not await _ensure_tables():
        raise RuntimeError("PostgreSQL 未就绪")

    def _exec():
        with _pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    for sql, params in statements:
                        cur.execute(sql, params)

    await asyncio.to_thread(_exec)


# ---------- 会话管理（失败抛异常，由路由层统一转业务错误） ----------

async def create_session(user_id: int, pet_id: int, title: str = "") -> int:
    """为指定宠物创建一个新会话，返回自增会话 ID

    title 为空时使用默认标题「新会话」；首轮对话落库时会自动
    用首条用户消息刷新标题（见 save_turn 的自动命名逻辑）。
    """
    clean_title = (title or "").strip()[:TITLE_MAX_LEN] or "新会话"
    rows = await _run(
        "INSERT INTO chat_session (user_id, pet_id, title) VALUES (%s, %s, %s) RETURNING id",
        (user_id, pet_id, clean_title), fetch=True)
    return rows[0][0]


async def list_sessions(user_id: int) -> List[dict]:
    """查询用户的全部会话（跨宠物统一展示，最近活跃在前），每条带归属宠物 petId"""
    sql = ("SELECT id, pet_id, title, "
           "EXTRACT(EPOCH FROM create_time)::bigint, EXTRACT(EPOCH FROM update_time)::bigint "
           "FROM chat_session WHERE user_id=%s ORDER BY update_time DESC, id DESC")
    rows = await _run(sql, (user_id,), fetch=True)
    # petId 转字符串下发：宠物 ID 是雪花 ID（19 位），以 JSON number 返回会在前端被截断
    return [{"id": r[0], "petId": str(r[1] or 0), "title": r[2],
             "createTime": int(r[3]), "updateTime": int(r[4])}
            for r in rows]


async def get_session(user_id: int, session_id: int) -> Optional[dict]:
    """按 ID 查询会话（仅限本人），不存在返回 None

    对话前用它做归属校验，防止跨用户 / 跨宠物串会话。
    """
    rows = await _run(
        "SELECT id, pet_id, title FROM chat_session WHERE id=%s AND user_id=%s",
        (session_id, user_id), fetch=True)
    if not rows:
        return None
    row = rows[0]
    return {"id": row[0], "petId": str(row[1] or 0), "title": row[2]}


async def delete_session(user_id: int, session_id: int) -> None:
    """删除会话及其全部消息（仅限本人）

    两条 DELETE 显式包在同一事务里：否则先删消息后删会话，
    若第二条失败就会残留一个没有任何消息的空会话（侧栏还能看到、
    点进去却是空的）。失败时回滚并向上抛，由路由层转为业务错误响应。
    """
    await _run_tx([
        ("DELETE FROM chat_message WHERE session_id=%s AND user_id=%s", (session_id, user_id)),
        ("DELETE FROM chat_session WHERE id=%s AND user_id=%s", (session_id, user_id)),
    ])


# ---------- 消息读写（异常降级，不影响对话主流程） ----------

# 一轮对话的插入 / 会话刷新语句统一在此定义，save_turn 与 save_turn_if_exists 共用：
# - 用户消息永远落库（含中止时零产出的场景），保证这轮提问不丢；
# - reply 为空时只写用户消息，不产生空回复条目；
# - interrupted 仅标记 assistant 消息（该回复是否被用户中止生成）；
# - _TOUCH_SQL 每轮刷新会话活跃时间（列表排序依据），并在标题仍为默认
#   「新会话」时用首条用户消息自动命名（对中断兜底保存同样生效）。
_INSERT_SQL = ("INSERT INTO chat_message (user_id, pet_id, session_id, role, content, interrupted, create_time) "
               "VALUES (%s, %s, %s, %s, %s, %s, NOW())")
_TOUCH_SQL = ("UPDATE chat_session SET title = CASE WHEN title = '新会话' THEN %s ELSE title END, "
              "update_time = NOW() WHERE id = %s AND user_id = %s")


def _turn_statements(user_id: int, pet_id: int, session_id: int,
                     user_msg: str, reply: str, interrupted: bool) -> List[tuple]:
    """构造一轮对话的事务语句列表（用户消息 + 可选助手回复 + 会话刷新）"""
    statements = [(_INSERT_SQL, (user_id, pet_id, session_id, "user", user_msg, False))]
    if reply:
        statements.append(
            (_INSERT_SQL, (user_id, pet_id, session_id, "assistant", reply, interrupted)))
    statements.append((_TOUCH_SQL, (user_msg.strip()[:TITLE_MAX_LEN], session_id, user_id)))
    return statements


async def save_turn(user_id: int, pet_id: int, session_id: int,
                    user_msg: str, reply: str, interrupted: bool = False) -> None:
    """持久化一轮对话（用户消息 + 助手回复）到 PostgreSQL

    一次事务写入，保证顺序与原子性；顺带刷新会话活跃时间、
    首轮时用首条用户消息命名会话；reply 为空（模型空输出）时只写用户消息。
    任何异常仅记日志，不影响对话主流程。
    """
    try:
        await _run_tx(_turn_statements(user_id, pet_id, session_id, user_msg, reply, interrupted))
    except Exception:
        logger.warning("写入 PostgreSQL 对话历史失败（不影响本次对话）", exc_info=True)


async def save_turn_if_exists(user_id: int, pet_id: int, session_id: int,
                              user_msg: str, reply: str, interrupted: bool = False) -> None:
    """条件持久化一轮对话：仅当该会话仍然存在时才写入

    客户端中途断开 / 用户停止生成的兜底保存走这里：若用户此刻已删除该会话
    （chat_session 中无该会话行），跳过写入避免把刚删掉的历史复活。
    存在性检查按会话表（chat_session）而非消息表——首轮即被中止的会话还没有
    任何消息行，按消息表检查会把它误判为「已删除」而丢掉这轮提问；
    先查后写在「会话删除」与「写入」之间存在极小竞态窗口，兜底保存允许这一轻量代价。
    任何异常仅记日志，不影响对话主流程。
    """
    try:
        rows = await _run(
            "SELECT 1 FROM chat_session WHERE id=%s AND user_id=%s LIMIT 1",
            (session_id, user_id), fetch=True)
        if not rows:
            # 会话已被删除：跳过写入，避免复活历史
            return
        await _run_tx(_turn_statements(user_id, pet_id, session_id, user_msg, reply, interrupted))
    except Exception:
        logger.warning("条件写入 PostgreSQL 对话历史失败（不影响本次对话）", exc_info=True)


async def read_history(user_id: int, session_id: int, limit: Optional[int] = None) -> List[dict]:
    """读取指定会话的对话历史（时间正序：旧 → 新）

    - limit=None：返回全部历史（GET /chat/history 场景，前端展示完整对话）；
    - limit=N：仅返回最近 N 条（存量会话接入 checkpoint 时的历史回填上限）。
    PG 异常或连接池未就绪时返回空列表，绝不抛错。
    """
    # 用子查询先按时间倒序取最近 N 条，再外层按时间正序返回，避免 ORDER BY + LIMIT 组合的方向陷阱；
    # 子查询必须同时 SELECT id，否则外层无法用 id 作为同时间戳下的次级排序键
    if limit is not None and limit > 0:
        sql = ("SELECT role, content, pet_id, EXTRACT(EPOCH FROM create_time)::bigint AS ts, interrupted FROM ("
               "  SELECT id, role, content, pet_id, interrupted, create_time FROM chat_message "
               "  WHERE user_id=%s AND session_id=%s ORDER BY create_time DESC, id DESC LIMIT %s"
               ") t ORDER BY create_time ASC, id ASC")
        params = (user_id, session_id, limit)
    else:
        sql = ("SELECT role, content, pet_id, EXTRACT(EPOCH FROM create_time)::bigint AS ts, interrupted "
               "FROM chat_message WHERE user_id=%s AND session_id=%s ORDER BY create_time ASC, id ASC")
        params = (user_id, session_id)
    try:
        rows = await _run(sql, params, fetch=True)
        # petId 转字符串下发防前端截断；pet_id 为 0 / NULL 时视为未知，返回 None；
        # interrupted 标记助手回复是否被用户中止（前端历史回放展示「（已停止）」）
        return [{"role": r[0], "content": r[1], "petId": str(r[2]) if r[2] else None,
                 "ts": int(r[3]), "interrupted": bool(r[4])}
                for r in rows]
    except Exception:
        logger.warning("读取 PostgreSQL 对话历史失败，降级为空历史", exc_info=True)
        return []
