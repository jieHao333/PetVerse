"""MySQL 会话与对话消息持久化（aiomysql 连接池）

存储表：
  - petverse_ai.chat_session：会话表（user_id + pet_id 隔离，一只宠物可建多个会话）
  - petverse_ai.chat_message：消息表（session_id 关联会话）

设计要点：
  1. Redis 仍是 LLM 短窗口上下文缓存（按会话滚动 N 条 + TTL），MySQL 才是持久层，
     GET /chat/history 从 MySQL 读取，服务重启 / Redis 过期都不丢历史；
  2. 会话管理方法（create/list/delete_session、get_session）失败时抛出异常，
     由路由层转换为业务错误响应——会话是对话的前提，不允许静默降级；
  3. 消息读写方法均做异常降级：MySQL 故障时绝不让对话主流程报错，
     save_* 静默失败仅记日志，read_history 返回空列表；
  4. save_turn_if_exists 用于流式对话客户端中途断开的兜底保存——
     若用户此刻已删除该会话（表中该 session_id 无行），跳过写入避免复活历史，
     与 memory.append_if_exists 的 Redis Lua 原子语义保持一致。
"""
import logging
from typing import List, Optional

import aiomysql

from app.config import settings

logger = logging.getLogger(__name__)

# 会话标题最大长度（与 chat_session.title VARCHAR(64) 对齐）
TITLE_MAX_LEN = 30

# 连接池单例（由 main.py 的 lifespan 在启动 / 停机时初始化与关闭）
_pool: Optional[aiomysql.Pool] = None


async def init() -> None:
    """创建 MySQL 连接池（服务启动时调用一次）

    aiomysql.create_pool 是异步函数，需在事件循环中 await；
    连接失败时不抛出，仅记日志，让服务在 MySQL 不可用时仍能启动（读写自动降级）。
    """
    global _pool
    try:
        _pool = await aiomysql.create_pool(
            host=settings.MYSQL_HOST,
            port=settings.MYSQL_PORT,
            user=settings.MYSQL_USER,
            password=settings.MYSQL_PASSWORD,
            db=settings.MYSQL_DB,
            charset="utf8mb4",
            autocommit=True,                    # 单条 INSERT / DELETE 无需显式事务
            minsize=settings.MYSQL_POOL_MIN,
            maxsize=settings.MYSQL_POOL_MAX,
            pool_recycle=3600,                  # 1 小时回收连接，避免 MySQL wait_timeout 断连
        )
        logger.info("MySQL 连接池已初始化: %s@%s:%s/%s",
                    settings.MYSQL_USER, settings.MYSQL_HOST,
                    settings.MYSQL_PORT, settings.MYSQL_DB)
    except Exception:
        logger.warning("MySQL 连接池初始化失败，会话与对话持久化将不可用", exc_info=True)
        _pool = None


async def close() -> None:
    """关闭 MySQL 连接池（服务停机时调用）"""
    global _pool
    if _pool is not None:
        try:
            _pool.close()
            await _pool.wait_closed()
        except Exception:
            logger.warning("关闭 MySQL 连接池异常（忽略）", exc_info=True)
        _pool = None


# ---------- 会话管理（失败抛异常，由路由层统一转业务错误） ----------

async def create_session(user_id: int, pet_id: int, title: str = "") -> int:
    """为指定宠物创建一个新会话，返回自增会话 ID

    title 为空时使用默认标题「新会话」；首轮对话落库时会自动
    用首条用户消息刷新标题（见 save_turn 的自动命名逻辑）。
    """
    if _pool is None:
        raise RuntimeError("MySQL 未就绪，无法创建会话")
    clean_title = (title or "").strip()[:TITLE_MAX_LEN] or "新会话"
    sql = ("INSERT INTO chat_session (user_id, pet_id, title) VALUES (%s, %s, %s)")
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, (user_id, pet_id, clean_title))
            return cur.lastrowid


async def list_sessions(user_id: int) -> List[dict]:
    """查询用户的全部会话（跨宠物统一展示，最近活跃在前），每条带归属宠物 petId"""
    if _pool is None:
        raise RuntimeError("MySQL 未就绪，无法查询会话列表")
    sql = ("SELECT id, pet_id, title, UNIX_TIMESTAMP(create_time), UNIX_TIMESTAMP(update_time) "
           "FROM chat_session WHERE user_id=%s "
           "ORDER BY update_time DESC, id DESC")
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, (user_id,))
            rows = await cur.fetchall()
    # petId 转字符串下发：宠物 ID 是雪花 ID（19 位），以 JSON number 返回会在前端被截断
    return [{"id": r[0], "petId": str(r[1] or 0), "title": r[2],
             "createTime": int(r[3]), "updateTime": int(r[4])}
            for r in rows]


async def get_session(user_id: int, session_id: int) -> Optional[dict]:
    """按 ID 查询会话（仅限本人），不存在返回 None

    对话前用它做归属校验，防止跨用户 / 跨宠物串会话。
    """
    if _pool is None:
        raise RuntimeError("MySQL 未就绪，无法查询会话")
    sql = "SELECT id, pet_id, title FROM chat_session WHERE id=%s AND user_id=%s"
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, (session_id, user_id))
            row = await cur.fetchone()
    if row is None:
        return None
    return {"id": row[0], "petId": str(row[1] or 0), "title": row[2]}


async def delete_session(user_id: int, session_id: int) -> None:
    """删除会话及其全部消息（仅限本人）

    连接池是 autocommit=True，两条 DELETE 必须显式包在同一事务里：
    否则先删消息后删会话，若第二条失败就会残留一个没有任何消息的空会话
    （侧栏还能看到、点进去却是空的）。失败时回滚并向上抛，
    由路由层转为业务错误响应——删除是用户主动操作，不允许静默降级。
    """
    if _pool is None:
        raise RuntimeError("MySQL 未就绪，无法删除会话")
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await conn.begin()
            try:
                await cur.execute(
                    "DELETE FROM chat_message WHERE session_id=%s AND user_id=%s",
                    (session_id, user_id))
                await cur.execute(
                    "DELETE FROM chat_session WHERE id=%s AND user_id=%s",
                    (session_id, user_id))
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise


# ---------- 消息读写（异常降级，不影响对话主流程） ----------

async def save_turn(user_id: int, pet_id: int, session_id: int,
                    user_msg: str, reply: str) -> None:
    """持久化一轮对话（用户消息 + 助手回复）到 MySQL

    一次事务写入两条，保证顺序与原子性；若会话标题仍是默认「新会话」，
    顺带用首条用户消息自动命名会话；任何异常仅记日志，不影响对话主流程。
    """
    if _pool is None:
        return
    sql = ("INSERT INTO chat_message (user_id, pet_id, session_id, role, content, create_time) "
           "VALUES (%s, %s, %s, %s, %s, NOW())")
    title_sql = ("UPDATE chat_session SET title=%s WHERE id=%s AND user_id=%s AND title='新会话'")
    try:
        async with _pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 关闭 autocommit 后用事务写入，确保两条消息要么都成功要么都失败
                await conn.begin()
                try:
                    await cur.execute(sql, (user_id, pet_id, session_id, "user", user_msg))
                    await cur.execute(sql, (user_id, pet_id, session_id, "assistant", reply))
                    # 首轮对话自动命名会话（截断为 TITLE_MAX_LEN 字符）
                    await cur.execute(title_sql,
                                      (user_msg.strip()[:TITLE_MAX_LEN], session_id, user_id))
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    raise
    except Exception:
        logger.warning("写入 MySQL 对话历史失败（不影响本次对话）", exc_info=True)


async def save_turn_if_exists(user_id: int, pet_id: int, session_id: int,
                              user_msg: str, reply: str) -> None:
    """条件持久化一轮对话：仅当该会话已有历史消息时才写入

    语义与 memory.append_if_exists 对齐：客户端中途断开的兜底保存走这里，
    若用户此刻已删除该会话（表中该会话无消息行），跳过写入避免把刚删掉的历史复活。
    任何异常仅记日志，不影响对话主流程。
    """
    if _pool is None:
        return
    exists_sql = ("SELECT 1 FROM chat_message WHERE user_id=%s AND session_id=%s LIMIT 1")
    insert_sql = ("INSERT INTO chat_message (user_id, pet_id, session_id, role, content, create_time) "
                  "VALUES (%s, %s, %s, %s, %s, NOW())")
    try:
        async with _pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(exists_sql, (user_id, session_id))
                if await cur.fetchone() is None:
                    # 会话已被删除：跳过写入，避免复活历史
                    return
                await conn.begin()
                try:
                    await cur.execute(insert_sql, (user_id, pet_id, session_id, "user", user_msg))
                    await cur.execute(insert_sql, (user_id, pet_id, session_id, "assistant", reply))
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    raise
    except Exception:
        logger.warning("条件写入 MySQL 对话历史失败（不影响本次对话）", exc_info=True)


async def read_history(user_id: int, session_id: int, limit: Optional[int] = None) -> List[dict]:
    """读取指定会话的对话历史（时间正序：旧 → 新）

    - limit=None：返回全部历史（GET /chat/history 场景，前端展示完整对话）；
    - limit=N：仅返回最近 N 条（LLM 上下文回落场景，Redis 缺失时用 MySQL 补齐）。
    MySQL 异常或连接池未初始化时返回空列表，绝不抛错。
    """
    if _pool is None:
        return []
    # 用子查询先按时间倒序取最近 N 条，再外层按时间正序返回，避免 ORDER BY + LIMIT 组合的方向陷阱；
    # 子查询必须同时 SELECT id，否则外层无法用 id 作为同时间戳下的次级排序键
    if limit is not None and limit > 0:
        sql = ("SELECT role, content, pet_id, UNIX_TIMESTAMP(create_time) AS ts FROM ("
               "  SELECT id, role, content, pet_id, create_time FROM chat_message "
               "  WHERE user_id=%s AND session_id=%s ORDER BY create_time DESC, id DESC LIMIT %s"
               ") t ORDER BY create_time ASC, id ASC")
        params = (user_id, session_id, limit)
    else:
        sql = ("SELECT role, content, pet_id, UNIX_TIMESTAMP(create_time) AS ts FROM chat_message "
               "WHERE user_id=%s AND session_id=%s ORDER BY create_time ASC, id ASC")
        params = (user_id, session_id)
    try:
        async with _pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        # petId 转字符串下发防前端截断；pet_id 为 0 / NULL 时视为未知，返回 None
        return [{"role": r[0], "content": r[1], "petId": str(r[2]) if r[2] else None, "ts": int(r[3])}
                for r in rows]
    except Exception:
        logger.warning("读取 MySQL 对话历史失败，降级为空历史", exc_info=True)
        return []
