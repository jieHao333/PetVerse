"""会话与消息持久化（PostgreSQL）。

职责：为前端提供可回放的对话历史——会话表 ``chat_session`` 与消息表 ``chat_message``。
与 LangGraph checkpoint 的分工：checkpoint 存「给模型看的上下文」，本模块存「给人看的历史」，
两者独立写入，任一方故障都不影响另一方。

主要导出：
    - ``init`` / ``close``           连接池生命周期
    - ``create_session``             新建会话
    - ``list_sessions`` / ``get_session``  会话查询（后者同时用于归属校验）
    - ``delete_session``             删除会话及其全部消息（单事务）
    - ``save_turn``                  写入一轮问答（问题 + 回答 + 会话标题维护）
    - ``save_turn_if_exists``        会话仍存在时才写入（用于中断收尾，避免复活已删会话）
    - ``read_history``               按时间正序读取历史

外部依赖：``psycopg`` / ``psycopg_pool``、``app.config.settings``。

使用示例：
    from app import persistence

    await persistence.init()
    session_id = await persistence.create_session(user_id=42, pet_id=7)
    await persistence.save_turn(42, 7, session_id, "猫咪打疫苗要注意什么", "……")
    rows = await persistence.read_history(42, session_id, limit=40)
"""

import asyncio
import json
import logging
import time
from typing import List, Optional

from psycopg_pool import ConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)

# 会话标题取首条用户消息的前 N 个字；列宽为 64，这里提前收窄以适配列表展示。
TITLE_MAX_LEN = 30

# 建表失败后的冷却时间：PG 未就绪时每次请求都去重试建表会拖慢主链路，
# 冷却期内直接判定「不可用」，到期后自动再试（PG 恢复无需重启服务）。
_TABLES_RETRY_COOLDOWN = 30.0

_pool: Optional[ConnectionPool] = None
_tables_ready = False
_last_tables_try = 0.0

# 建表语句全部幂等（IF NOT EXISTS / ADD COLUMN IF NOT EXISTS），可在任意时刻重复执行，
# 因此既能用于首次部署，也能用于老库平滑升级。
_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS chat_session (
         id          BIGSERIAL   PRIMARY KEY,
         user_id     BIGINT      NOT NULL,
         pet_id      BIGINT      NOT NULL DEFAULT 0,
         title       VARCHAR(64) NOT NULL DEFAULT '新会话',
         create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
       )""",
    # 会话列表按「用户 + 宠物 + 最近活跃」排序，索引列顺序与查询完全对齐。
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
    # 后加的两列用 ALTER 补：老库已有数据，重建表会丢历史。
    """ALTER TABLE chat_message ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT '[]'::jsonb""",
    """ALTER TABLE chat_message ADD COLUMN IF NOT EXISTS refs JSONB NOT NULL DEFAULT '[]'::jsonb""",
    """CREATE INDEX IF NOT EXISTS idx_chat_message_user_session_time
         ON chat_message (user_id, session_id, create_time)""",
    """CREATE INDEX IF NOT EXISTS idx_chat_message_user_pet_time
         ON chat_message (user_id, pet_id, create_time)""",
)


def _init_sync() -> None:
    """同步创建连接池（供 ``asyncio.to_thread`` 调用）。

    Returns:
        None。

    Raises:
        Exception: 连接池构造失败时抛出，由 ``init`` 捕获并降级。
    """
    global _pool
    _pool = ConnectionPool(
        conninfo=settings.pg_dsn,
        min_size=settings.PG_POOL_MIN,
        max_size=settings.PG_POOL_MAX,
        timeout=5.0,
        # open=False + open(wait=False)：不在启动时阻塞等待连接，
        # PG 暂时不可达也能把服务拉起来（后续由池自身重连）。
        open=False,
    )
    _pool.open(wait=False)


async def init() -> None:
    """初始化连接池并确保业务表存在。

    失败不抛出：持久化不可用时服务仍应启动（历史为空、写入静默失败）。

    Returns:
        None。

    Raises:
        无。
    """
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
    """确保业务表就绪（带冷却的惰性建表）。

    Returns:
        True 表示表已就绪，可以执行 SQL；False 表示当前不可用。

    Raises:
        无——建表失败只记日志，等冷却结束后重试。
    """
    global _tables_ready, _last_tables_try
    if _tables_ready:
        return True
    pool = _pool
    if pool is None:
        return False
    # 冷却判断放在建表之前：PG 宕机期间每个请求都尝试连接会让超时时间叠加到响应耗时上。
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
    """关闭连接池。

    Returns:
        None。

    Raises:
        无。
    """
    global _pool
    # 先摘引用再关闭：即使 close 卡住，后续请求也不会再拿到已失效的池。
    pool, _pool = _pool, None
    if pool is not None:
        try:
            await asyncio.to_thread(pool.close)
        except Exception:
            logger.warning("关闭 PostgreSQL 连接池异常（忽略）", exc_info=True)


async def _run(sql: str, params: tuple = (), fetch: bool = False):
    """在连接池上执行单条 SQL。

    Args:
        sql: SQL 语句（使用 ``%s`` 占位符）。
        params: 参数元组。
        fetch: True 时返回 ``fetchall()`` 结果，否则返回 None。

    Returns:
        查询结果行列表；``fetch=False`` 时为 None。

    Raises:
        RuntimeError: 表尚未就绪时抛出，让调用方走各自的降级分支。
        Exception: 透传 psycopg 异常。
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
    """在单个事务中顺序执行多条 SQL（全成或全败）。

    一轮问答要写「用户消息 + 助手消息 + 更新会话时间」，中途失败会留下半截历史，
    因此必须包在事务里。

    Args:
        statements: ``(sql, params)`` 列表。

    Returns:
        None。

    Raises:
        RuntimeError: 表尚未就绪时抛出。
        Exception: 透传 psycopg 异常（事务自动回滚）。
    """
    if not await _ensure_tables():
        raise RuntimeError("PostgreSQL 未就绪")

    def _exec():
        with _pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    for sql, params in statements:
                        cur.execute(sql, params)

    await asyncio.to_thread(_exec)


async def create_session(user_id: int, pet_id: int, title: str = "") -> int:
    """新建会话。

    Args:
        user_id: 用户 ID。
        pet_id: 关联宠物 ID；0 表示未指定宠物。
        title: 初始标题；为空时用「新会话」，等首条消息写入时再替换。

    Returns:
        新会话自增 ID。

    Raises:
        RuntimeError: 持久化不可用时抛出（会话是后续所有操作的前提，不能静默失败）。
    """
    # 按列宽截断，避免超长标题触发数据库长度错误。
    clean_title = (title or "").strip()[:TITLE_MAX_LEN] or "新会话"
    rows = await _run(
        "INSERT INTO chat_session (user_id, pet_id, title) VALUES (%s, %s, %s) RETURNING id",
        (user_id, pet_id, clean_title), fetch=True)
    return rows[0][0]


async def list_sessions(user_id: int) -> List[dict]:
    """列出用户的全部会话（跨宠物，最近活跃在前）。

    Args:
        user_id: 用户 ID。

    Returns:
        会话字典列表，时间字段为 Unix 秒；``petId`` 统一转字符串与历史契约保持一致。

    Raises:
        Exception: 透传数据库异常（路由层转成统一错误报文）。
    """
    # 时间在 SQL 侧直接转 epoch 秒，省掉 Python 侧的时区换算（TIMESTAMPTZ → 本地时区易错）。
    sql = ("SELECT id, pet_id, title, "
           "EXTRACT(EPOCH FROM create_time)::bigint, EXTRACT(EPOCH FROM update_time)::bigint "
           "FROM chat_session WHERE user_id=%s ORDER BY update_time DESC, id DESC")
    rows = await _run(sql, (user_id,), fetch=True)
    return [{"id": r[0], "petId": str(r[1] or 0), "title": r[2],
             "createTime": int(r[3]), "updateTime": int(r[4])}
            for r in rows]


async def get_session(user_id: int, session_id: int) -> Optional[dict]:
    """查询会话基本信息，同时充当归属校验。

    Args:
        user_id: 用户 ID。
        session_id: 会话 ID。

    Returns:
        会话字典；不存在或不属于该用户时返回 None（两种情况刻意不区分，
        避免通过错误信息探测他人会话是否存在）。

    Raises:
        Exception: 透传数据库异常。
    """
    rows = await _run(
        "SELECT id, pet_id, title FROM chat_session WHERE id=%s AND user_id=%s",
        (session_id, user_id), fetch=True)
    if not rows:
        return None
    row = rows[0]
    return {"id": row[0], "petId": str(row[1] or 0), "title": row[2]}


async def delete_session(user_id: int, session_id: int) -> None:
    """删除会话及其全部消息。

    两条 DELETE 放在同一事务里：只删会话会留下孤儿消息，只删消息会留下空会话。
    checkpoint 记忆由调用方并发清理（见 ``chat.delete_session``）。

    Args:
        user_id: 用户 ID。
        session_id: 会话 ID。

    Returns:
        None。

    Raises:
        Exception: 透传数据库异常。
    """
    await _run_tx([
        ("DELETE FROM chat_message WHERE session_id=%s AND user_id=%s", (session_id, user_id)),
        ("DELETE FROM chat_session WHERE id=%s AND user_id=%s", (session_id, user_id)),
    ])


_INSERT_SQL = ("INSERT INTO chat_message (user_id, pet_id, session_id, role, content, "
               "interrupted, attachments, refs, create_time) "
               "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, NOW())")
# 只在标题仍为默认值时替换：后续轮次不应把会话标题改成最后一句话。
_TOUCH_SQL = ("UPDATE chat_session SET title = CASE WHEN title = '新会话' THEN %s ELSE title END, "
              "update_time = NOW() WHERE id = %s AND user_id = %s")


def _turn_statements(user_id: int, pet_id: int, session_id: int,
                     user_msg: str, reply: str, interrupted: bool,
                     attachments: Optional[list] = None,
                     refs: Optional[list] = None) -> List[tuple]:
    """组装一轮问答的 SQL 列表（用户消息 + 可选助手消息 + 会话标题/时间维护）。

    Args:
        user_id: 用户 ID。
        pet_id: 宠物 ID。
        session_id: 会话 ID。
        user_msg: 用户消息文本。
        reply: 助手回复文本；为空时不写助手消息（被中断且零产出时只保留提问）。
        interrupted: 该条助手消息是否为「生成中被中止」。
        attachments: 用户消息携带的附件，落 ``attachments`` JSONB。
        refs: 助手消息引用的知识库来源，落 ``refs`` JSONB。

    Returns:
        ``(sql, params)`` 组成的语句列表。

    Raises:
        无。
    """
    att_json = json.dumps(attachments or [], ensure_ascii=False)
    refs_json = json.dumps(refs or [], ensure_ascii=False)
    # 附件只挂在用户消息上，助手消息的 attachments 恒为空数组。
    statements = [(_INSERT_SQL, (user_id, pet_id, session_id, "user", user_msg, False,
                                 att_json, "[]"))]
    if reply:
        statements.append((_INSERT_SQL, (user_id, pet_id, session_id, "assistant", reply,
                                         interrupted, "[]", refs_json)))
    statements.append((_TOUCH_SQL, (user_msg.strip()[:TITLE_MAX_LEN] or "新会话", session_id, user_id)))
    return statements


async def save_turn(user_id: int, pet_id: int, session_id: int,
                    user_msg: str, reply: str, interrupted: bool = False,
                    attachments: Optional[list] = None,
                    refs: Optional[list] = None) -> None:
    """保存一轮问答（失败仅记日志，不影响本次对话）。

    Args:
        user_id: 用户 ID。
        pet_id: 宠物 ID。
        session_id: 会话 ID。
        user_msg: 用户消息文本。
        reply: 助手回复文本。
        interrupted: 回复是否被中止（前端据此渲染「已停止」标记）。
        attachments: 用户消息附件列表。
        refs: 回复引用的知识库来源列表。

    Returns:
        None。

    Raises:
        无——历史落库是「尽力而为」，不能因为写库失败就让用户看不到已经生成的答案。
    """
    try:
        await _run_tx(_turn_statements(user_id, pet_id, session_id,
                                       user_msg, reply, interrupted, attachments, refs))
    except Exception:
        logger.warning("写入 PostgreSQL 对话历史失败（不影响本次对话）", exc_info=True)


async def save_turn_if_exists(user_id: int, pet_id: int, session_id: int,
                              user_msg: str, reply: str, interrupted: bool = False,
                              attachments: Optional[list] = None,
                              refs: Optional[list] = None) -> None:
    """仅当会话仍然存在时保存一轮问答。

    用于「客户端中途断开 / 用户点停止生成」的收尾路径：此时会话可能已被用户删除，
    无条件写入会因外键缺失而失败，或在无外键的表结构下写出孤儿数据。

    Args:
        user_id: 用户 ID。
        pet_id: 宠物 ID。
        session_id: 会话 ID。
        user_msg: 用户消息文本。
        reply: 已产出的部分回复（可为空串）。
        interrupted: 固定传 True 的场景见 ``chat._persist_interrupted``。
        attachments: 用户消息附件列表。
        refs: 已产出的引用来源列表。

    Returns:
        None。

    Raises:
        无。
    """
    try:
        # 先做存在性检查而非依赖外键报错：报错会污染日志且无法区分「会话已删」与真实故障。
        rows = await _run(
            "SELECT 1 FROM chat_session WHERE id=%s AND user_id=%s LIMIT 1",
            (session_id, user_id), fetch=True)
        if not rows:
            return
        await _run_tx(_turn_statements(user_id, pet_id, session_id,
                                       user_msg, reply, interrupted, attachments, refs))
    except Exception:
        logger.warning("条件写入 PostgreSQL 对话历史失败（不影响本次对话）", exc_info=True)


async def read_history(user_id: int, session_id: int, limit: Optional[int] = None) -> List[dict]:
    """读取会话历史（时间正序）。

    Args:
        user_id: 用户 ID。
        session_id: 会话 ID。
        limit: 只取最近 N 条；None 表示全部。

    Returns:
        消息字典列表（role / content / petId / attachments / refs / ts / interrupted）。
        读取失败时返回空列表——历史缺失只影响上下文，不应让接口 500。

    Raises:
        无。
    """
    if limit is not None and limit > 0:
        # 先按倒序取最近 N 条，再在外层翻回正序：直接正序 + LIMIT 会拿到「最早的 N 条」，
        # 那是错误的窗口方向。两层排序都带 id 兜底，避免同秒消息顺序不稳。
        sql = ("SELECT role, content, pet_id, attachments, refs, EXTRACT(EPOCH FROM create_time)::bigint AS ts, interrupted FROM ("
               "  SELECT id, role, content, pet_id, attachments, refs, interrupted, create_time FROM chat_message "
               "  WHERE user_id=%s AND session_id=%s ORDER BY create_time DESC, id DESC LIMIT %s"
               ") t ORDER BY create_time ASC, id ASC")
        params = (user_id, session_id, limit)
    else:
        sql = ("SELECT role, content, pet_id, attachments, refs, EXTRACT(EPOCH FROM create_time)::bigint AS ts, interrupted "
               "FROM chat_message WHERE user_id=%s AND session_id=%s ORDER BY create_time ASC, id ASC")
        params = (user_id, session_id)
    try:
        rows = await _run(sql, params, fetch=True)
        return [{"role": r[0], "content": r[1], "petId": str(r[2]) if r[2] else None,
                 "attachments": _clean_attachments(r[3]), "refs": _clean_json_list(r[4]),
                 "ts": int(r[5]), "interrupted": bool(r[6])}
                for r in rows]
    except Exception:
        logger.warning("读取 PostgreSQL 对话历史失败，降级为空历史", exc_info=True)
        return []


def _clean_json_list(raw) -> list:
    """把 JSONB 列读出的值统一成列表。

    psycopg 通常已把 JSONB 解析为 Python 对象，但历史数据或手工改库可能留下字符串，
    因此两种形态都要兼容。

    Args:
        raw: 列值（list / str / None）。

    Returns:
        列表；无法解析时返回空列表。

    Raises:
        无。
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _clean_attachments(raw) -> list:
    """把附件列值统一成「结构合法的附件列表」。

    与 ``_clean_json_list`` 的差别：这里逐项校验 ``type`` 与 ``url`` 是否存在，
    避免脏数据传到前端后渲染出坏图 / 空附件卡片。

    Args:
        raw: 列值（list / str / None）。

    Returns:
        合法附件字典列表。

    Raises:
        无。
    """
    if isinstance(raw, list):
        return [a for a in raw if isinstance(a, dict) and a.get("type") and a.get("url")]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []
