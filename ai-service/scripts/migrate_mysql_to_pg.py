"""MySQL → PostgreSQL 一次性数据迁移脚本。

职责：把旧版存在 MySQL 里的 ``chat_session`` / ``chat_message`` 搬到 PostgreSQL，
保留原会话 ID（前端历史链接与 checkpoint 的 thread_id 都依赖它），并修正自增序列。

主要导出：无（作为脚本执行）。

外部依赖：``aiomysql``（仅迁移需要，完成后可卸载）、``psycopg``、``app.persistence``。

使用示例：
    pip install aiomysql          # 仅迁移时需要
    python -m scripts.migrate_mysql_to_pg

幂等性：插入使用 ``ON CONFLICT (id) DO NOTHING``，重复执行不会产生重复数据，
只会报告「已存在跳过 N 条」。迁移完成后无需额外处理 checkpoint 记忆——
它按会话 ID 直接沿用。
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

# 使脚本可直接执行时也能 import app 包。
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402

from app import persistence  # noqa: E402
from app.config import settings  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402

# 源库时间戳通常是「本地时间但无时区」，用当前进程的时区补上 tzinfo，
# 否则写入 TIMESTAMPTZ 时会被当成 UTC，导致历史消息时间偏移数小时。
_LOCAL_TZ = datetime.now().astimezone().tzinfo


class _MySQLSource(BaseSettings):
    """源库连接配置（独立于主配置，便于迁移时只改 MySQL 参数）。"""

    MYSQL_HOST: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "123456"
    MYSQL_DB: str = "petverse_ai"

    model_config = SettingsConfigDict(
        env_file=str(_PACKAGE_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


def _localize(dt: datetime) -> datetime:
    """给无时区的时间戳补上本地时区。

    Args:
        dt: 源库读出的时间（可能为 None）。

    Returns:
        带时区的时间；已有时区或为 None 时原样返回。

    Raises:
        无。
    """
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=_LOCAL_TZ)


async def _read_mysql() -> Tuple[List[tuple], List[tuple]]:
    """读取源库的会话与消息。

    Returns:
        ``(sessions, messages)`` 两个元组列表。按 id 升序读取，保证迁移顺序稳定。

    Raises:
        SystemExit: 未安装 aiomysql 或连接源库失败（迁移是可选操作，
            源库不可用时直接退出并说明即可）。
    """
    try:
        import aiomysql
    except ImportError:
        raise SystemExit("缺少 MySQL 驱动：请先执行 pip install aiomysql（仅迁移用，完成后可卸载）")

    src = _MySQLSource()
    print(f"源库(MySQL): {src.MYSQL_USER}@{src.MYSQL_HOST}:{src.MYSQL_PORT}/{src.MYSQL_DB}")
    print(f"目标库(PostgreSQL): {settings.PG_USER}@{settings.PG_HOST}:{settings.PG_PORT}/{settings.PG_DB}")
    try:
        conn = await aiomysql.connect(
            host=src.MYSQL_HOST, port=src.MYSQL_PORT, user=src.MYSQL_USER,
            password=src.MYSQL_PASSWORD, db=src.MYSQL_DB, charset="utf8mb4")
    except Exception as exc:
        raise SystemExit(f"连接 MySQL 失败（源库不可用则无需迁移）：{exc}")

    try:
        cur = await conn.cursor()
        await cur.execute(
            "SELECT id, user_id, pet_id, title, create_time, update_time "
            "FROM chat_session ORDER BY id")
        sessions = list(await cur.fetchall())
        await cur.execute(
            "SELECT id, user_id, pet_id, session_id, role, content, interrupted, create_time "
            "FROM chat_message ORDER BY id")
        messages = list(await cur.fetchall())
        await cur.close()
    finally:
        conn.close()
    return sessions, messages


def _write_pg(sessions: List[tuple], messages: List[tuple]) -> Tuple[int, int]:
    """把数据写入 PostgreSQL。

    显式带上主键 id 插入（而不是让 PG 重新生成），这样会话 ID、消息归属、
    checkpoint 的 thread_id 都能原样沿用。

    Args:
        sessions: 会话元组列表。
        messages: 消息元组列表。

    Returns:
        ``(新增会话数, 新增消息数)``；已存在的记录不计入。

    Raises:
        Exception: 透传 psycopg 异常（整个事务回滚，不会留下半截数据）。
    """
    import psycopg

    ins_session = ("INSERT INTO chat_session "
                   "(id, user_id, pet_id, title, create_time, update_time) "
                   "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING")
    ins_message = ("INSERT INTO chat_message "
                   "(id, user_id, pet_id, session_id, role, content, interrupted, create_time) "
                   "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING")
    # 手工写入 id 不会推进 BIGSERIAL 的序列，必须把序列重设到 MAX(id) 之后，
    # 否则应用后续插入会撞上已存在的主键。
    seq_sql = ("SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
               "COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")

    with psycopg.connect(settings.pg_dsn) as pg:
        with pg.cursor() as cur:
            s_ok = 0
            for sid, uid, pid, title, ct, ut in sessions:
                cur.execute(ins_session, (sid, uid, pid, title, _localize(ct), _localize(ut)))
                # rowcount 为 1 表示确实插入，0 表示命中 DO NOTHING（已存在）。
                s_ok += cur.rowcount
            m_ok = 0
            for mid, uid, pid, sid, role, content, interrupted, ct in messages:
                cur.execute(ins_message, (mid, uid, pid, sid, role, content,
                                          bool(interrupted), _localize(ct)))
                m_ok += cur.rowcount
            cur.execute(seq_sql.format(table="chat_session"))
            cur.execute(seq_sql.format(table="chat_message"))
        pg.commit()
    return s_ok, m_ok


async def main() -> None:
    """迁移主流程：读源库 → 确保目标表存在 → 写入 → 打印统计。

    Returns:
        None。

    Raises:
        无（连接类错误以 SystemExit 退出）。
    """
    setup_logging()
    sessions, messages = await _read_mysql()
    print(f"读取完成: 会话 {len(sessions)} 条, 消息 {len(messages)} 条")

    # 复用业务层的初始化逻辑：目标表结构与线上完全一致（含索引与新增列）。
    await persistence.init()
    try:
        s_ok, m_ok = _write_pg(sessions, messages)
    finally:
        await persistence.close()

    print(f"写入完成: 会话新增 {s_ok} 条（已存在跳过 {len(sessions) - s_ok} 条）, "
          f"消息新增 {m_ok} 条（已存在跳过 {len(messages) - m_ok} 条）")
    print("迁移结束。checkpoint 记忆按会话 ID 直接沿用，无需其他处理。")


if __name__ == "__main__":
    asyncio.run(main())
