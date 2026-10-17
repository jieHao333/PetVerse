"""知识库存储层（PostgreSQL + pgvector，自建表结构）

为什么不再用 LangChain 的 PGVector：它把文档与元数据封装在 langchain_pg_embedding
里，只能按 metadata 等值过滤，无法支撑本次改造需要的三件事——
  1. 文档级管理与删除（上传接口按 doc_id 级联清理全部片段）；
  2. 关键词检索通道需要片段分词结果（tokens 列）；
  3. 物种数组过滤与 HNSW 索引（0.8 支持 vector_cosine_ops）。

表结构（与业务表、checkpoint 同在 petverse_ai 库）：
  kb_document  文档登记（名称/类型/分类/物种/标签/内容哈希/片段数），doc_id 为主键，
               同名文档重复导入按覆盖处理（先删旧片段再写新片段，一个事务内完成）；
  kb_chunk     知识片段（含上下文的正文 + BM25 分词 + 向量），外键级联随文档删除；
  kb_meta      元信息表，只用于维护 BM25 索引版本戳：任何写入/删除都会自增，
               进程内 BM25 索引据此惰性重建，多进程部署也不会读到过期词表。

连接池沿用本项目既有做法：psycopg3 同步池 + asyncio.to_thread（Windows 的
ProactorEventLoop 下 psycopg 异步连接不可用）。脚本场景（导入/评测）不经过服务
lifespan，故池按需惰性创建。
"""
import asyncio
import json
import logging
from typing import List, Optional, Sequence

import psycopg

from app.config import settings

logger = logging.getLogger(__name__)

# 片段在库中的稳定标识前缀：doc_id + '#' + chunk_index，跨重建不变，供引用去重
CHUNK_KEY_SEP = "#"

_pool = None
_dim_checked = False

# 元信息键：BM25 索引版本戳
_META_VERSION = "version"


def _ddl() -> List[str]:
    """建表语句（幂等；向量列维度取配置值，见 ensure_dim 的一致性校验）"""
    return [
        """CREATE TABLE IF NOT EXISTS kb_document (
             doc_id      TEXT PRIMARY KEY,
             name        TEXT        NOT NULL,
             source      TEXT        NOT NULL,
             kind        TEXT        NOT NULL,
             category    TEXT        NOT NULL DEFAULT '',
             species     TEXT[]      NOT NULL DEFAULT '{}',
             tags        TEXT[]      NOT NULL DEFAULT '{}',
             sha256      TEXT        NOT NULL DEFAULT '',
             origin      TEXT        NOT NULL DEFAULT 'repo',
             file_path   TEXT,
             chunk_count INT         NOT NULL DEFAULT 0,
             char_count  INT         NOT NULL DEFAULT 0,
             created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
             updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
           )""",
        # 存量库补列：CREATE TABLE IF NOT EXISTS 不会给已存在的表加列
        "ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS file_path TEXT",
        f"""CREATE TABLE IF NOT EXISTS kb_chunk (
             id          BIGSERIAL PRIMARY KEY,
             doc_id      TEXT NOT NULL REFERENCES kb_document(doc_id) ON DELETE CASCADE,
             chunk_index INT  NOT NULL,
             section     TEXT NOT NULL DEFAULT '',
             category    TEXT NOT NULL DEFAULT '',
             species     TEXT[] NOT NULL DEFAULT '{{}}',
             content     TEXT NOT NULL,
             tokens      TEXT NOT NULL DEFAULT '',
             embedding   vector({settings.EMBEDDING_DIM}) NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_kb_chunk_doc ON kb_chunk (doc_id)",
        "CREATE INDEX IF NOT EXISTS idx_kb_chunk_embedding "
        "ON kb_chunk USING hnsw (embedding vector_cosine_ops)",
        "CREATE TABLE IF NOT EXISTS kb_meta (key TEXT PRIMARY KEY, value BIGINT NOT NULL)",
        f"INSERT INTO kb_meta (key, value) VALUES ('{_META_VERSION}', 0) "
        "ON CONFLICT (key) DO NOTHING",
    ]


def _init_pool():
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(conninfo=settings.pg_dsn, min_size=settings.PG_POOL_MIN,
                          max_size=settings.PG_POOL_MAX, timeout=5.0, open=False)
    pool.open(wait=False)
    return pool


def _get_pool():
    """获取连接池（脚本场景按需创建，服务场景由 init() 提前建好）"""
    global _pool
    if _pool is None:
        _pool = _init_pool()
    return _pool


async def init() -> None:
    """初始化连接池并确保表结构就绪（服务启动调用；失败只记日志，检索时明确报错）"""
    global _pool
    try:
        _pool = await asyncio.to_thread(_init_pool)
        await _ensure_schema()
        logger.info("知识库表结构已就绪: kb_document / kb_chunk (dim=%s)", settings.EMBEDDING_DIM)
    except Exception:
        logger.warning("知识库存储初始化失败，知识检索将不可用", exc_info=True)
        _pool = None


async def close() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        try:
            await asyncio.to_thread(pool.close)
        except Exception:
            logger.warning("关闭知识库连接池异常（忽略）", exc_info=True)


def _exec(sql: str, params: Sequence = (), fetch: str = None):
    """同步执行：fetch='all' 返回全部行，'one' 返回首行，None 返回 None

    向量列以 '[...]'::vector 文本形式传参，避免依赖 pgvector 的 psycopg 适配器注册。
    """
    with _get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch == "all":
                return cur.fetchall()
            if fetch == "one":
                return cur.fetchone()
            return None


async def _run(sql: str, params: Sequence = (), fetch: str = None):
    if _pool is None:
        _get_pool()
    return await asyncio.to_thread(_exec, sql, params, fetch)


async def _ensure_schema() -> None:
    """幂等建表 + 校验向量列维度与配置一致

    维度不一致时必须停下来报错：pgvector 插入不同维度会逐条报错，
    而「配置改了维度」最常见的后果是整库检索静默失败，提前校验更可解释。
    """
    global _dim_checked
    ddl = _ddl()

    def _create():
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                for stmt in ddl:
                    cur.execute(stmt)
                cur.execute(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid='kb_chunk'::regclass AND attname='embedding'")
                return cur.fetchone()[0]

    typmod = await asyncio.to_thread(_create)
    if typmod is not None and typmod != settings.EMBEDDING_DIM and not _dim_checked:
        _dim_checked = True
        raise RuntimeError(
            f"kb_chunk 向量列维度为 {typmod}，与当前 EMBEDDING_DIM="
            f"{settings.EMBEDDING_DIM} 不一致：请换集合或重建知识库表")


async def ensure_ready() -> None:
    """写/查前的兜底：确保池与表结构可用（脚本与首次请求都走这里）"""
    await _ensure_schema()


# ---------- 写入 ----------

async def upsert_document(doc_meta: dict, chunks: List[dict], vectors: List[List[float]]) -> int:
    """写入（覆盖）一篇文档及其全部片段，一个事务内完成，返回入库片段数

    顺序必须是「删旧片段 → 写文档行 → 写片段」：外键是 ON DELETE CASCADE，
    先写文档行会让旧片段短暂挂在新元数据上；重复导入同名文档要得到干净结果。
    """
    await _ensure_schema()
    if len(chunks) != len(vectors):
        raise ValueError(f"片段数与向量数不一致：{len(chunks)} vs {len(vectors)}")

    insert_doc = ("INSERT INTO kb_document (doc_id, name, source, kind, category, species, "
                  "tags, sha256, origin, file_path, chunk_count, char_count, "
                  "created_at, updated_at) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW()) "
                  "ON CONFLICT (doc_id) DO UPDATE SET name=EXCLUDED.name, "
                  "source=EXCLUDED.source, kind=EXCLUDED.kind, category=EXCLUDED.category, "
                  "species=EXCLUDED.species, tags=EXCLUDED.tags, sha256=EXCLUDED.sha256, "
                  "origin=EXCLUDED.origin, file_path=EXCLUDED.file_path, "
                  "chunk_count=EXCLUDED.chunk_count, "
                  "char_count=EXCLUDED.char_count, updated_at=NOW()")
    insert_chunk = ("INSERT INTO kb_chunk (doc_id, chunk_index, section, category, species, "
                    "content, tokens, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::vector)")

    def _write():
        with _get_pool().connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM kb_chunk WHERE doc_id=%s", (doc_meta["doc_id"],))
                    cur.execute(insert_doc, (
                        doc_meta["doc_id"], doc_meta["name"], doc_meta["source"],
                        doc_meta["kind"], doc_meta.get("category", ""),
                        doc_meta.get("species", []), doc_meta.get("tags", []),
                        doc_meta.get("sha256", ""), doc_meta.get("origin", "repo"),
                        doc_meta.get("file_path"),
                        len(chunks), doc_meta.get("char_count", 0)))
                    for chunk, vec in zip(chunks, vectors):
                        cur.execute(insert_chunk, (
                            chunk["doc_id"], chunk["chunk_index"], chunk.get("section", ""),
                            chunk.get("category", ""), chunk.get("species", []),
                            chunk["content"], chunk.get("tokens", ""), _vec_text(vec)))
                    cur.execute(f"UPDATE kb_meta SET value = value + 1 WHERE key='{_META_VERSION}'")

    await asyncio.to_thread(_write)
    return len(chunks)


async def delete_document(doc_id: str) -> bool:
    """删除一篇文档（片段随外键级联删除），返回是否真的删掉了"""
    await _ensure_schema()

    def _delete():
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kb_document WHERE doc_id=%s", (doc_id,))
                deleted = cur.rowcount
                if deleted:
                    cur.execute(
                        f"UPDATE kb_meta SET value = value + 1 WHERE key='{_META_VERSION}'")
                return deleted

    return bool(await asyncio.to_thread(_delete))


async def clear_all() -> int:
    """清空整个知识库（全量重建前调用），返回删除的文档数"""
    await _ensure_schema()

    def _clear():
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kb_document")
                deleted = cur.rowcount
                cur.execute(f"UPDATE kb_meta SET value = value + 1 WHERE key='{_META_VERSION}'")
                return deleted

    return int(await asyncio.to_thread(_clear) or 0)


# ---------- 读取 ----------

async def list_documents(page: int = 1, size: int = 20,
                         keyword: str = "") -> tuple[List[dict], int]:
    """分页查询文档列表，返回 (当前页条目, 总条数)

    排序固定按更新时间倒序（新导入/刚改过的排前面），不提供自定义排序列：
    管理页只需要「最近动的先看」，开放排序会让 SQL 拼接面变大。
    keyword 非空时按文档名 / 文件名 / 分类做不区分大小写的模糊过滤。
    """
    await _ensure_schema()
    page = max(1, int(page or 1))
    size = min(100, max(1, int(size or 20)))

    where, params = "", []
    needle = (keyword or "").strip()
    if needle:
        where = ("WHERE lower(name) LIKE lower(%s) OR lower(source) LIKE lower(%s) "
                 "OR lower(category) LIKE lower(%s) OR lower(doc_id) LIKE lower(%s)")
        like = f"%{needle}%"
        params = [like, like, like, like]

    total = await _run(f"SELECT count(*) FROM kb_document {where}",
                       tuple(params), "one")
    rows = await _run(
        f"SELECT doc_id, name, source, kind, category, species, tags, chunk_count, "
        f"       char_count, origin, sha256, file_path, EXTRACT(EPOCH FROM updated_at)::bigint "
        f"FROM kb_document {where} "
        f"ORDER BY updated_at DESC, doc_id LIMIT %s OFFSET %s",
        tuple(params) + (size, (page - 1) * size), "all") or []
    items = [{"docId": r[0], "name": r[1], "source": r[2], "kind": r[3], "category": r[4],
              "species": r[5] or [], "tags": r[6] or [], "chunkCount": r[7], "charCount": r[8],
              "origin": r[9], "sha256": (r[10] or "")[:12], "filePath": r[11],
              "updatedAt": int(r[12] or 0)}
             for r in rows]
    return items, int(total[0]) if total else 0


async def document_chunks(doc_id: str) -> List[dict]:
    """一篇文档的全部片段（按 chunk_index 升序），供预览弹窗的「入库片段」页签"""
    await _ensure_schema()
    rows = await _run(
        "SELECT chunk_index, section, content, tokens FROM kb_chunk "
        "WHERE doc_id=%s ORDER BY chunk_index", (doc_id,), "all") or []
    return [{"chunkIndex": r[0], "section": r[1], "content": r[2],
             "chars": len(r[2] or ""), "tokenCount": len((r[3] or "").split())}
            for r in rows]


async def get_document(doc_id: str) -> Optional[dict]:
    rows = await _run("SELECT doc_id, name, source, kind, sha256, file_path, origin "
                      "FROM kb_document WHERE doc_id=%s", (doc_id,), "all")
    if not rows:
        return None
    r = rows[0]
    return {"docId": r[0], "name": r[1], "source": r[2], "kind": r[3], "sha256": r[4],
            "filePath": r[5], "origin": r[6]}


async def document_exists(doc_id: str, sha256: str) -> bool:
    """该文档是否已按同一内容入库（导入脚本据此跳过未变更文件，省 Embedding 调用）"""
    rows = await _run("SELECT sha256 FROM kb_document WHERE doc_id=%s", (doc_id,), "all")
    return bool(rows) and rows[0][0] == sha256


async def stats() -> dict:
    """知识库规模统计（启动日志与评测报告用）"""
    await _ensure_schema()
    row = await _run("SELECT (SELECT count(*) FROM kb_document), "
                     "(SELECT count(*) FROM kb_chunk), "
                     "(SELECT COALESCE(sum(length(content)),0) FROM kb_chunk)", (), "one")
    return {"documents": int(row[0]), "chunks": int(row[1]), "chars": int(row[2])}


async def version() -> int:
    """BM25 索引版本戳（任何写入都会自增）"""
    await _ensure_schema()
    row = await _run(f"SELECT value FROM kb_meta WHERE key='{_META_VERSION}'", (), "one")
    return int(row[0]) if row else 0


async def all_contents() -> List[str]:
    """全部片段正文（仅供评测脚本校验黄金集，不参与线上检索）"""
    await _ensure_schema()
    rows = await _run("SELECT content FROM kb_chunk", (), "all") or []
    return [r[0] for r in rows]


async def lexicon_rows() -> List[tuple]:
    """BM25 语料：(chunk_id, tokens)，全表拉取（语料量为千级，常驻内存可接受）"""
    await _ensure_schema()
    return await _run("SELECT id, tokens FROM kb_chunk ORDER BY id", (), "all") or []


async def dense_search(vector: Sequence[float], k: int) -> List[dict]:
    """稠密召回：按余弦相似度取前 k 条，sim = 1 - cosine_distance（0~1，越大越相关）"""
    await _ensure_schema()
    sql = ("SELECT id, doc_id, chunk_index, section, category, species, content, "
           "       1 - (embedding <=> %s::vector) AS sim "
           "FROM kb_chunk ORDER BY embedding <=> %s::vector LIMIT %s")
    vec = _vec_text(vector)
    rows = await _run(sql, (vec, vec, int(k)), "all") or []
    return [{"chunk_id": r[0], "doc_id": r[1], "chunk_index": r[2], "section": r[3],
             "category": r[4], "species": r[5] or [], "content": r[6],
             "sim": float(r[7]) if r[7] is not None else 0.0, "tokens": ""}
            for r in rows]


async def chunks_by_ids(chunk_ids: Sequence[int]) -> List[dict]:
    """按 ID 取片段完整信息（混合检索融合后回表补齐文档名/标题）"""
    if not chunk_ids:
        return []
    await _ensure_schema()
    rows = await _run(
        "SELECT c.id, c.doc_id, c.chunk_index, c.section, c.category, c.species, c.content, "
        "       d.name, d.source, d.kind, d.category "
        "FROM kb_chunk c JOIN kb_document d ON d.doc_id = c.doc_id "
        "WHERE c.id = ANY(%s)", (list(chunk_ids),), "all") or []
    return [{"chunk_id": r[0], "doc_id": r[1], "chunk_index": r[2], "section": r[3],
             "category": r[4] or r[10] or "", "species": r[5] or [], "content": r[6],
             "doc_title": r[7], "source": r[8], "kind": r[9], "tokens": ""}
            for r in rows]


def _vec_text(vector: Sequence[float]) -> str:
    """向量转 pgvector 文本字面量 '[0.1,0.2,...]'"""
    return "[" + ",".join(f"{float(v):.7f}" for v in vector) + "]"


def json_default(obj):
    """psycopg 返回的额外类型统一按字符串序列化（供日志/评测输出 JSON 用）"""
    return json.dumps(obj, ensure_ascii=False)
