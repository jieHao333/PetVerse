"""MySQL 对话消息持久化（aiomysql 连接池）

存储表：petverse_ai.chat_message
  id BIGINT AUTO_INCREMENT / user_id / pet_id / role / content / create_time

设计要点：
  1. Redis 仍是 LLM 短窗口上下文缓存（滚动 N 条 + TTL），MySQL 才是持久层，
     GET /chat/history 从 MySQL 读取，服务重启 / Redis 过期都不丢历史；
  2. 所有对外方法均做异常降级：MySQL 故障时绝不让对话主流程报错，
     save_* 静默失败仅记日志，read_history 返回空列表；
  3. save_turn_if_exists 用于流式对话客户端中途断开的兜底保存——
     若用户此刻已「清空对话」（表中该 user_id/pet_id 无行），跳过写入避免复活历史，
     与 memory.append_if_exists 的 Redis Lua 原子语义保持一致。
"""
import logging
from typing import List, Optional

import aiomysql

from app.config import settings

logger = logging.getLogger(__name__)

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
        logger.warning("MySQL 连接池初始化失败，对话持久化将降级（不影响对话主流程）", exc_info=True)
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


async def save_turn(user_id: int, pet_id: int, user_msg: str, reply: str) -> None:
    """持久化一轮对话（用户消息 + 助手回复）到 MySQL

    一次事务写入两条，保证顺序与原子性；任何异常仅记日志，不影响对话主流程。
    """
    if _pool is None:
        return
    sql = ("INSERT INTO chat_message (user_id, pet_id, role, content, create_time) "
           "VALUES (%s, %s, %s, %s, NOW())")
    try:
        async with _pool.acquire() as conn:
            async with conn.cursor() as cur:
                # 关闭 autocommit 后用事务写入，确保两条消息要么都成功要么都失败
                await conn.begin()
                try:
                    await cur.execute(sql, (user_id, pet_id, "user", user_msg))
                    await cur.execute(sql, (user_id, pet_id, "assistant", reply))
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    raise
    except Exception:
        logger.warning("写入 MySQL 对话历史失败（不影响本次对话）", exc_info=True)


async def save_turn_if_exists(user_id: int, pet_id: int, user_msg: str, reply: str) -> None:
    """条件持久化一轮对话：仅当 (user_id, pet_id) 已有历史行时才写入

    语义与 memory.append_if_exists 对齐：客户端中途断开的兜底保存走这里，
    若用户此刻已「清空对话」（表中该组合无行），跳过写入避免把刚删掉的历史复活。
    任何异常仅记日志，不影响对话主流程。
    """
    if _pool is None:
        return
    exists_sql = "SELECT 1 FROM chat_message WHERE user_id=%s AND pet_id=%s LIMIT 1"
    insert_sql = ("INSERT INTO chat_message (user_id, pet_id, role, content, create_time) "
                  "VALUES (%s, %s, %s, %s, NOW())")
    try:
        async with _pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(exists_sql, (user_id, pet_id))
                if await cur.fetchone() is None:
                    # 已被清空：跳过写入，避免复活历史
                    return
                await conn.begin()
                try:
                    await cur.execute(insert_sql, (user_id, pet_id, "user", user_msg))
                    await cur.execute(insert_sql, (user_id, pet_id, "assistant", reply))
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    raise
    except Exception:
        logger.warning("条件写入 MySQL 对话历史失败（不影响本次对话）", exc_info=True)


async def read_history(user_id: int, pet_id: int, limit: Optional[int] = None) -> List[dict]:
    """读取指定宠物的对话历史（时间正序：旧 → 新）

    - limit=None：返回全部历史（GET /chat/history 场景，前端展示完整对话）；
    - limit=N：仅返回最近 N 条（LLM 上下文回落场景，Redis 缺失时用 MySQL 补齐）。
    MySQL 异常或连接池未初始化时返回空列表，绝不抛错。
    """
    if _pool is None:
        return []
    # 用子查询先按时间倒序取最近 N 条，再外层按时间正序返回，避免 ORDER BY + LIMIT 组合的方向陷阱；
    # 子查询必须同时 SELECT id，否则外层无法用 id 作为同时间戳下的次级排序键
    if limit is not None and limit > 0:
        sql = ("SELECT role, content, UNIX_TIMESTAMP(create_time) AS ts FROM ("
               "  SELECT id, role, content, create_time FROM chat_message "
               "  WHERE user_id=%s AND pet_id=%s ORDER BY create_time DESC, id DESC LIMIT %s"
               ") t ORDER BY create_time ASC, id ASC")
        params = (user_id, pet_id, limit)
    else:
        sql = ("SELECT role, content, UNIX_TIMESTAMP(create_time) AS ts FROM chat_message "
               "WHERE user_id=%s AND pet_id=%s ORDER BY create_time ASC, id ASC")
        params = (user_id, pet_id)
    try:
        async with _pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        return [{"role": r[0], "content": r[1], "ts": int(r[2])} for r in rows]
    except Exception:
        logger.warning("读取 MySQL 对话历史失败，降级为空历史", exc_info=True)
        return []


async def clear_history(user_id: int, pet_id: int) -> None:
    """清空指定宠物的所有对话消息；任何异常仅记日志"""
    if _pool is None:
        return
    sql = "DELETE FROM chat_message WHERE user_id=%s AND pet_id=%s"
    try:
        async with _pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (user_id, pet_id))
    except Exception:
        logger.warning("清空 MySQL 对话历史失败（忽略）", exc_info=True)
