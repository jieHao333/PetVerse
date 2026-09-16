"""存量数据迁移脚本（MySQL chat_session / chat_message -> PostgreSQL）

背景：ai-service 的会话与消息表已从 MySQL 整体迁移到 PostgreSQL 库
petverse_ai（与 LangGraph checkpoint 同库）；本脚本把历史遗留 MySQL 库中的
存量数据搬运过来。

用法（在 ai-service 目录下执行）：
    .venv\\Scripts\\python -m scripts.migrate_mysql_to_pg
    （需要一次性安装 MySQL 驱动：pip install aiomysql；迁移完成后可卸载）

特性：
  - 只读源库：绝不写 MySQL，连不上时直接提示退出；
  - 幂等：目标表同 ID 行已存在时跳过（ON CONFLICT DO NOTHING），可反复执行；
  - 保留原 ID：前端 currentSessionId 与 LangGraph checkpoint 的 thread_id 都
    内嵌了会话 ID，换 ID 会造成记忆断链；写完后对齐 PG 序列避免新会话撞主键；
  - 时间戳按本地时区补全后写入 TIMESTAMPTZ，与原库的展示时间保持一致。

MySQL 连接参数从 .env 的 MYSQL_* 键读取（若已删除配置则用默认值）。
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

# 支持 `python scripts/migrate_mysql_to_pg.py` 直接运行：把包根目录补进 sys.path
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from pydantic_settings import BaseSettings, SettingsConfigDict  # noqa: E402

from app import persistence  # noqa: E402
from app.config import settings  # noqa: E402
from app.logging_setup import setup_logging  # noqa: E402

# 本地时区（用于把 MySQL 的 naive DATETIME 补全为带时区时间写入 TIMESTAMPTZ）
_LOCAL_TZ = datetime.now().astimezone().tzinfo


class _MySQLSource(BaseSettings):
    """仅迁移脚本使用的 MySQL 连接配置（主配置已不再声明 MYSQL_*，此处独立读取 .env）"""

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
    """naive DATETIME 按本地时区补全（MySQL DATETIME 无时区，存的是本地时间）"""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=_LOCAL_TZ)


async def _read_mysql() -> Tuple[List[tuple], List[tuple]]:
    """读取 MySQL 全部会话与消息（只读）"""
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
    """写入 PostgreSQL：保留原 ID + 幂等跳过 + 末尾对齐序列"""
    import psycopg

    ins_session = ("INSERT INTO chat_session "
                   "(id, user_id, pet_id, title, create_time, update_time) "
                   "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING")
    ins_message = ("INSERT INTO chat_message "
                   "(id, user_id, pet_id, session_id, role, content, interrupted, create_time) "
                   "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING")
    # 显式写入 id 不会推进 BIGSERIAL 序列，必须手动 setval；
    # 第三参 false 表示「下次 nextval 返回该值」，空表时从 1 开始
    seq_sql = ("SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
               "COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")

    with psycopg.connect(settings.pg_dsn) as pg:
        with pg.cursor() as cur:
            s_ok = 0
            for sid, uid, pid, title, ct, ut in sessions:
                cur.execute(ins_session, (sid, uid, pid, title, _localize(ct), _localize(ut)))
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
    setup_logging()
    sessions, messages = await _read_mysql()
    print(f"读取完成: 会话 {len(sessions)} 条, 消息 {len(messages)} 条")

    # 确保目标表存在（幂等；PG 不可用时 init 内部降级，写入阶段会明确报错）
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
