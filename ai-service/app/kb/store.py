"""知识库持久化（PostgreSQL + pgvector）。

职责：维护 ``kb_document``（文档元信息）/ ``kb_chunk``（片段 + 向量）/ ``kb_meta``（版本戳）
三张表，提供写入、查询、向量召回三类操作。

主要导出：
    - ``init`` / ``close`` / ``ensure_ready``   生命周期
    - ``upsert_document`` / ``delete_document`` / ``clear_all``  写入与删除
    - ``list_documents`` / ``get_document`` / ``document_chunks`` / ``stats``  查询
    - ``dense_search`` / ``chunks_by_ids``      向量召回与按 ID 回填
    - ``version``                               供 BM25 索引判断是否需要重建
    - ``lexicon_rows`` / ``all_contents``       供索引构建与评测使用
    - ``CHUNK_KEY_SEP``                         片段复合键分隔符（当前未被引用，用途待确认）

外部依赖：``psycopg`` / ``psycopg_pool``、pgvector 扩展（``vector`` 类型与 ``<=>`` 运算符）、
``app.config.settings``。

使用示例：
    from app.kb import store

    await store.init()
    await store.upsert_document(meta, chunks, vectors)
    rows = await store.dense_search(vector, k=20)

一致性设计：写入以 ``doc_id`` 为单位整体覆盖（单事务内「删旧片段 → 写文档行 → 写片段」），
并自增 ``kb_meta.version``；进程内的 BM25 索引据此惰性重建，避免多实例 / 多进程缓存不一致。
"""

import asyncio
import json
import logging
from typing import List, Optional, Sequence

import psycopg

from app.config import settings

logger = logging.getLogger(__name__)

# TODO(待确认)：片段复合键分隔符，目前在模块内（及全仓库）未被引用，
# 复合键的具体拼接格式与使用方缺少代码依据，待人工确认后再补充说明或清理。
CHUNK_KEY_SEP = "#"

_pool = None
_dim_checked = False

# kb_meta 的版本键：任何写操作都会 +1，读取方（lexicon）据此判断索引是否过期。
_META_VERSION = "version"


def _ddl() -> List[str]:
    """构造建表语句（幂等，可重复执行）。

    Returns:
        DDL 语句列表；每项都用 ``IF NOT EXISTS``，既用于首次部署也用于老库升级。

    Raises:
        无。
    """
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
        # file_path 是后加的列：老库需要 ALTER 补齐，否则原文预览/下载会一直为空。
        "ALTER TABLE kb_document ADD COLUMN IF NOT EXISTS file_path TEXT",
        # species 用 TEXT[] 存储，检索时按数组重叠判断是否适用（见 search._apply_species）。
        # 向量维度取自 EMBEDDING_DIM，改维度必须重建此表（见 _ensure_schema 的校验）。
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
        # HNSW + cosine 索引：近似最近邻查询，避免每次检索全表扫描。
        "CREATE INDEX IF NOT EXISTS idx_kb_chunk_embedding "
        "ON kb_chunk USING hnsw (embedding vector_cosine_ops)",
        "CREATE TABLE IF NOT EXISTS kb_meta (key TEXT PRIMARY KEY, value BIGINT NOT NULL)",
        # 版本键初始化：ON CONFLICT DO NOTHING 保证重复启动不会把已有版本重置为 0。
        f"INSERT INTO kb_meta (key, value) VALUES ('{_META_VERSION}', 0) "
        "ON CONFLICT (key) DO NOTHING",
    ]


def _init_pool():
    """同步创建连接池。

    Returns:
        已打开（非阻塞）的连接池。

    Raises:
        Exception: 参数非法或 psycopg_pool 初始化失败时抛出。
    """
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(conninfo=settings.pg_dsn, min_size=settings.PG_POOL_MIN,
                          max_size=settings.PG_POOL_MAX, timeout=5.0, open=False)
    pool.open(wait=False)
    return pool


def _get_pool():
    """获取连接池（惰性创建，供同步调用链使用）。

    Returns:
        连接池实例。

    Raises:
        Exception: 首次创建失败时抛出。
    """
    global _pool
    if _pool is None:
        _pool = _init_pool()
    return _pool


async def init() -> None:
    """初始化连接池并确保表结构就绪。

    失败时清空 ``_pool``，使后续调用走「知识库不可用」的降级路径，而不是反复重试。

    Returns:
        None。

    Raises:
        无。
    """
    global _pool
    try:
        _pool = await asyncio.to_thread(_init_pool)
        await _ensure_schema()
        logger.info("知识库表结构已就绪: kb_document / kb_chunk (dim=%s)", settings.EMBEDDING_DIM)
    except Exception:
        logger.warning("知识库存储初始化失败，知识检索将不可用", exc_info=True)
        _pool = None


async def close() -> None:
    """关闭连接池。

    Returns:
        None。

    Raises:
        无。
    """
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        try:
            await asyncio.to_thread(pool.close)
        except Exception:
            logger.warning("关闭知识库连接池异常（忽略）", exc_info=True)


def _exec(sql: str, params: Sequence = (), fetch: str = None):
    """同步执行一条 SQL。

    Args:
        sql: SQL 语句。
        params: 参数序列。
        fetch: ``"all"`` 返回全部行，``"one"`` 返回首行，None 不取结果。

    Returns:
        查询结果；不取结果时返回 None。

    Raises:
        Exception: 透传 psycopg 异常。
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
    """异步执行一条 SQL（线程池代理同步实现）。

    注意这里没有包 try/except：调用方需要区分「查询失败」与「查询为空」的语义差异。

    Args:
        sql: SQL 语句。
        params: 参数序列。
        fetch: 同上。

    Returns:
        查询结果。

    Raises:
        Exception: 透传 psycopg 异常。
    """
    if _pool is None:
        # 惰性初始化：脚本或测试可能未调用 init() 就直接查询。
        _get_pool()
    return await asyncio.to_thread(_exec, sql, params, fetch)


async def _ensure_schema() -> None:
    """确保表结构就绪，并校验向量维度与配置一致。

    Returns:
        None。

    Raises:
        RuntimeError: 已存在表的向量维度与 ``EMBEDDING_DIM`` 不一致时抛出——这是一个
            「必须人工介入」的配置错误，继续跑只会让所有写入失败，不如启动时就喊停。
    """
    global _dim_checked
    ddl = _ddl()

    def _create():
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                for stmt in ddl:
                    cur.execute(stmt)
                # atttypmod 即 vector(n) 里的 n；直接读系统表比解析建表语句更可靠。
                cur.execute(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid='kb_chunk'::regclass AND attname='embedding'")
                return cur.fetchone()[0]

    typmod = await asyncio.to_thread(_create)
    # _dim_checked 保证只在第一次发现不一致时抛错，避免每条请求都抛（日志会被刷爆）。
    if typmod is not None and typmod != settings.EMBEDDING_DIM and not _dim_checked:
        _dim_checked = True
        raise RuntimeError(
            f"kb_chunk 向量列维度为 {typmod}，与当前 EMBEDDING_DIM="
            f"{settings.EMBEDDING_DIM} 不一致：请换集合或重建知识库表")


async def ensure_ready() -> None:
    """对外暴露的「确保表就绪」入口（入库流程调用）。

    Returns:
        None。

    Raises:
        RuntimeError: 见 ``_ensure_schema``。
    """
    await _ensure_schema()


async def upsert_document(doc_meta: dict, chunks: List[dict], vectors: List[List[float]]) -> int:
    """写入 / 覆盖一篇文档及其全部片段。

    Args:
        doc_meta: 文档元信息（doc_id / name / source / kind / category / species / tags /
            sha256 / origin / file_path / char_count）。
        chunks: 片段列表（doc_id / chunk_index / section / category / species / content / tokens）。
        vectors: 与 ``chunks`` 一一对应的向量。

    Returns:
        写入的片段数。

    Raises:
        ValueError: ``chunks`` 与 ``vectors`` 数量不一致（说明上游 Embedding 出了问题，
            继续写会写出错位的向量，必须中止）。
        Exception: 透传数据库异常（事务已回滚）。
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
                    # 先删旧片段再插入：文档更新后片段数可能变少，仅 upsert 会残留旧片段
                    # （它们仍会被检索到，造成「答案引用了已删除内容」）。
                    cur.execute("DELETE FROM kb_chunk WHERE doc_id=%s", (doc_meta["doc_id"],))
                    cur.execute(insert_doc, (
                        doc_meta["doc_id"], doc_meta["name"], doc_meta["source"],
                        doc_meta["kind"], doc_meta.get("category", ""),
                        doc_meta.get("species", []), doc_meta.get("tags", []),
                        doc_meta.get("sha256", ""), doc_meta.get("origin", "repo"),
                        doc_meta.get("file_path"),
                        len(chunks), doc_meta.get("char_count", 0)))
                    for chunk, vec in zip(chunks, vectors):
                        # 向量以文本字面量传入并显式 ::vector 转换：psycopg 无 pgvector 适配器时
                        # 也能写入，省掉额外依赖。
                        cur.execute(insert_chunk, (
                            chunk["doc_id"], chunk["chunk_index"], chunk.get("section", ""),
                            chunk.get("category", ""), chunk.get("species", []),
                            chunk["content"], chunk.get("tokens", ""), _vec_text(vec)))
                    # 版本自增放在事务内：与数据写入同生共死，不会出现「数据变了但索引没失效」。
                    cur.execute(f"UPDATE kb_meta SET value = value + 1 WHERE key='{_META_VERSION}'")

    await asyncio.to_thread(_write)
    return len(chunks)


async def delete_document(doc_id: str) -> bool:
    """删除一篇文档（片段经外键级联删除）。

    Args:
        doc_id: 文档 ID。

    Returns:
        True 表示确实删掉了一行；False 表示文档不存在。

    Raises:
        Exception: 透传数据库异常。
    """
    await _ensure_schema()

    def _delete():
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kb_document WHERE doc_id=%s", (doc_id,))
                # rowcount 必须在执行下一条 SQL 前取，否则会被覆盖。
                deleted = cur.rowcount
                if deleted:
                    cur.execute(
                        f"UPDATE kb_meta SET value = value + 1 WHERE key='{_META_VERSION}'")
                return deleted

    return bool(await asyncio.to_thread(_delete))


async def clear_all() -> int:
    """清空全部文档（片段级联删除）。

    Returns:
        被删除的文档数。

    Raises:
        Exception: 透传数据库异常。
    """
    await _ensure_schema()

    def _clear():
        with _get_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM kb_document")
                deleted = cur.rowcount
                cur.execute(f"UPDATE kb_meta SET value = value + 1 WHERE key='{_META_VERSION}'")
                return deleted

    return int(await asyncio.to_thread(_clear) or 0)


async def list_documents(page: int = 1, size: int = 20,
                         keyword: str = "") -> tuple[List[dict], int]:
    """分页查询文档列表。

    Args:
        page: 页码，从 1 开始（越界值会被夹到合法范围）。
        size: 每页条数，1~100。
        keyword: 模糊过滤词，匹配文档名 / 来源 / 分类 / doc_id。

    Returns:
        ``(items, total)``。

    Raises:
        Exception: 透传数据库异常。
    """
    await _ensure_schema()
    # 服务端夹取范围：前端传 0 / 负数 / 超大值都不应导致 SQL 报错或全表拉取。
    page = max(1, int(page or 1))
    size = min(100, max(1, int(size or 20)))

    where, params = "", []
    needle = (keyword or "").strip()
    if needle:
        # 四个字段任一命中即可；统一 lower() 让大小写不敏感（doc_id 常含英文）。
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
              "origin": r[9],
              # 哈希只回传前 12 位：完整值用于内部比对，前端用不到且太长。
              "sha256": (r[10] or "")[:12], "filePath": r[11],
              "updatedAt": int(r[12] or 0)}
             for r in rows]
    return items, int(total[0]) if total else 0


async def document_chunks(doc_id: str) -> List[dict]:
    """列出某文档的全部片段（按 chunk_index 升序）。

    Args:
        doc_id: 文档 ID。

    Returns:
        片段列表（chunkIndex / section / content / chars / tokenCount）。

    Raises:
        Exception: 透传数据库异常。
    """
    await _ensure_schema()
    rows = await _run(
        "SELECT chunk_index, section, content, tokens FROM kb_chunk "
        "WHERE doc_id=%s ORDER BY chunk_index", (doc_id,), "all") or []
    return [{"chunkIndex": r[0], "section": r[1], "content": r[2],
             "chars": len(r[2] or ""), "tokenCount": len((r[3] or "").split())}
            for r in rows]


async def get_document(doc_id: str) -> Optional[dict]:
    """查询单篇文档的元信息（不含片段）。

    Args:
        doc_id: 文档 ID。

    Returns:
        文档字典；不存在时返回 None。

    Raises:
        Exception: 透传数据库异常。
    """
    rows = await _run("SELECT doc_id, name, source, kind, sha256, file_path, origin "
                      "FROM kb_document WHERE doc_id=%s", (doc_id,), "all")
    if not rows:
        return None
    r = rows[0]
    return {"docId": r[0], "name": r[1], "source": r[2], "kind": r[3], "sha256": r[4],
            "filePath": r[5], "origin": r[6]}


async def document_exists(doc_id: str, sha256: str) -> bool:
    """判断文档是否已存在且内容未变（增量导入的跳过依据）。

    Args:
        doc_id: 文档 ID。
        sha256: 本次解析出的内容哈希。

    Returns:
        True 表示库中同 doc_id 的 sha256 与本次一致，可以跳过重新向量化。

    Raises:
        Exception: 透传数据库异常。
    """
    rows = await _run("SELECT sha256 FROM kb_document WHERE doc_id=%s", (doc_id,), "all")
    return bool(rows) and rows[0][0] == sha256


async def stats() -> dict:
    """统计知识库规模。

    Returns:
        ``{"documents": 文档数, "chunks": 片段数, "chars": 正文字符数}``。

    Raises:
        Exception: 透传数据库异常。
    """
    await _ensure_schema()
    row = await _run("SELECT (SELECT count(*) FROM kb_document), "
                     "(SELECT count(*) FROM kb_chunk), "
                     "(SELECT COALESCE(sum(length(content)),0) FROM kb_chunk)", (), "one")
    return {"documents": int(row[0]), "chunks": int(row[1]), "chars": int(row[2])}


async def version() -> int:
    """读取知识库版本戳。

    Returns:
        版本号（每次写入 / 删除都会 +1）；无记录时返回 0。

    Raises:
        Exception: 透传数据库异常。
    """
    await _ensure_schema()
    row = await _run(f"SELECT value FROM kb_meta WHERE key='{_META_VERSION}'", (), "one")
    return int(row[0]) if row else 0


async def all_contents() -> List[str]:
    """取出全部片段正文（评测脚本校验期望子串时使用）。

    Returns:
        正文列表。全量加载，仅适用于知识库规模有限的分析场景。

    Raises:
        Exception: 透传数据库异常。
    """
    await _ensure_schema()
    rows = await _run("SELECT content FROM kb_chunk", (), "all") or []
    return [r[0] for r in rows]


async def lexicon_rows() -> List[tuple]:
    """取出 BM25 索引所需的 ``(chunk_id, tokens)`` 数据。

    Returns:
        ``(id, tokens)`` 列表，按 id 升序（保证索引构建结果稳定可复现）。

    Raises:
        Exception: 透传数据库异常。
    """
    await _ensure_schema()
    return await _run("SELECT id, tokens FROM kb_chunk ORDER BY id", (), "all") or []


async def dense_search(vector: Sequence[float], k: int) -> List[dict]:
    """向量召回：按余弦距离取最近的 k 个片段。

    Args:
        vector: 查询向量。
        k: 返回条数。

    Returns:
        片段列表，含 ``sim``（余弦相似度，越大越相近）。距离运算符 ``<=>`` 返回的是
        余弦**距离**（1 - 相似度），因此这里做一次换算。

    Raises:
        Exception: 透传数据库异常。
    """
    await _ensure_schema()
    sql = ("SELECT id, doc_id, chunk_index, section, category, species, content, "
           "       1 - (embedding <=> %s::vector) AS sim "
           "FROM kb_chunk ORDER BY embedding <=> %s::vector LIMIT %s")
    vec = _vec_text(vector)
    rows = await _run(sql, (vec, vec, int(k)), "all") or []
    # 注意 SELECT 里要算 sim，ORDER BY 里也要给参数：两个占位符对应同一个向量。
    return [{"chunk_id": r[0], "doc_id": r[1], "chunk_index": r[2], "section": r[3],
             "category": r[4], "species": r[5] or [], "content": r[6],
             "sim": float(r[7]) if r[7] is not None else 0.0, "tokens": ""}
            for r in rows]


async def chunks_by_ids(chunk_ids: Sequence[int]) -> List[dict]:
    """按片段 ID 批量回填完整信息（含所属文档的标题 / 来源）。

    混合检索先用两条通道拿到候选 ID，再统一回填——避免每条通道各自 JOIN 一次文档表。

    Args:
        chunk_ids: 片段 ID 列表。

    Returns:
        片段列表；``chunk_ids`` 为空时直接返回空列表（不发起查询）。

    Raises:
        Exception: 透传数据库异常。
    """
    if not chunk_ids:
        return []
    await _ensure_schema()
    rows = await _run(
        "SELECT c.id, c.doc_id, c.chunk_index, c.section, c.category, c.species, c.content, "
        "       d.name, d.source, d.kind, d.category "
        "FROM kb_chunk c JOIN kb_document d ON d.doc_id = c.doc_id "
        "WHERE c.id = ANY(%s)", (list(chunk_ids),), "all") or []
    return [{"chunk_id": r[0], "doc_id": r[1], "chunk_index": r[2], "section": r[3],
             # 片段自身 category 为空时回落到文档级分类，保证引用面板总有分类可显示。
             "category": r[4] or r[10] or "", "species": r[5] or [], "content": r[6],
             "doc_title": r[7], "source": r[8], "kind": r[9], "tokens": ""}
            for r in rows]


def _vec_text(vector: Sequence[float]) -> str:
    """把向量转成 pgvector 文本字面量。

    Args:
        vector: 浮点序列。

    Returns:
        ``[0.1234567,0.7654321,...]`` 形式的字符串，每个分量固定保留 7 位小数。
        pgvector 接受这种文本字面量，配合 SQL 里的 ``::vector`` 转型即可写入。

    Raises:
        ValueError: 元素无法转成 float 时抛出。
    """
    return "[" + ",".join(f"{float(v):.7f}" for v in vector) + "]"


def json_default(obj):
    """JSON 序列化的默认编码器（保留中文）。

    Args:
        obj: 待序列化对象。

    Returns:
        JSON 字符串。

    Raises:
        TypeError: 对象不可序列化时抛出（与 ``json.dumps`` 行为一致）。
    """
    return json.dumps(obj, ensure_ascii=False)
