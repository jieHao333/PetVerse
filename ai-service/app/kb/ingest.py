"""知识库导入编排：解析 → 切分 → 向量化 → 入库

导入脚本（scripts/ingest_knowledge.py）与上传接口（POST /ai/kb/documents）共用本模块，
保证「仓库目录里的文档」和「用户在页面上上传的文档」走同一套切分与向量化逻辑，
检索行为不会因来源不同而不一致。

幂等策略：以文件内容 sha256 为版本判据——内容没变就跳过（省 Embedding 调用），
变了就按 doc_id 覆盖重建（先删旧片段再写新片段，单事务）。
"""
import asyncio
import logging
from pathlib import Path
from typing import List, Optional, Sequence

from app.config import settings
from app.kb import lexicon
from app.kb.chunking import split_document
from app.kb.documents import DocParseError, ParsedDocument, parse_bytes, parse_file
from app.kb.store import document_exists, upsert_document
from app.llm import get_embeddings

logger = logging.getLogger(__name__)

# 单批 Embedding 请求的片段数：多数 OpenAI 兼容服务对一次请求的条数/长度有限制，
# 阿里云系常见上限是 10 条，保守取小值，超限由服务商报错而非静默丢片段
_DEFAULT_BATCH = 10

# 瞬时网络失败的补试次数（批量导入跑几十次调用，一次抖动就整轮重来代价太高）
_EMBED_RETRIES = 2
_EMBED_RETRY_DELAY = 1.0


class IngestError(Exception):
    """导入失败（解析失败 / Embedding 不可用 / 向量库写入失败）"""


async def ingest_path(path: Path, origin: str = "repo", *, force: bool = False,
                      doc_id: Optional[str] = None, title: Optional[str] = None,
                      category: Optional[str] = None,
                      species: Optional[Sequence[str]] = None) -> dict:
    """导入磁盘上的一个文档文件，返回 {docId, source, chunks, skipped, sha256}

    仓库文档的原文本来就在磁盘上，直接把绝对路径记进 kb_document.file_path，
    在线预览（尤其是 PDF 原样渲染）按这个路径回读文件。
    """
    path = Path(path)
    doc = _parse(lambda: parse_file(path, doc_id=doc_id, title=title, category=category,
                                    species=list(species or [])))
    return await _ingest_document(doc, origin=origin, force=force,
                                  file_path=str(path.resolve()))


async def ingest_content(filename: str, data: bytes, origin: str = "upload", *,
                         doc_id: Optional[str] = None, title: Optional[str] = None,
                         category: Optional[str] = None,
                         species: Optional[Sequence[str]] = None) -> dict:
    """导入内存中的文件字节（上传接口用），并把原文落盘留存

    上传的文档原本只留下切分后的片段，PDF / DOCX 这类带排版的格式就没法原样预览，
    因此按 doc_id 把原文写到 KB_STORAGE_DIR（默认 data/kb）；同名文档重复上传时
    覆盖同一个文件，与「按 doc_id 覆盖重建」的库内语义保持一致。
    """
    doc = _parse(lambda: parse_bytes(filename, data, doc_id=doc_id, title=title,
                                     category=category, species=list(species or [])))
    file_path = _persist_original(doc.doc_id, filename, data)
    return await _ingest_document(doc, origin=origin, force=True, file_path=file_path)


def _persist_original(doc_id: str, filename: str, data: bytes) -> str:
    """把上传的原文写到留存目录，返回绝对路径；写失败只记日志（不阻断入库）

    留存是预览的增强项而非前提：磁盘写不进去时文档内容照样进了向量库，
    预览会自动回退到「按片段拼接」，不该让整个上传失败。
    """
    from app.config import settings

    try:
        directory = settings.kb_storage_dir
        directory.mkdir(parents=True, exist_ok=True)
        # 扩展名取自原文件名并截断，避免超长/怪异后缀污染文件名；doc_id 已由
        # documents._safe_doc_id 过滤过路径分隔符，不会跳出留存目录
        suffix = Path(filename).suffix.lower()[:8]
        target = directory / f"{doc_id}{suffix}"
        target.write_bytes(data)
        return str(target.resolve())
    except OSError:
        logger.warning("知识库原文留存失败（预览将回退到片段拼接）: doc_id=%s", doc_id,
                       exc_info=True)
        return ""


def remove_original(doc: dict) -> None:
    """删除上传文档的留存原文（仓库文档的源文件不动，由代码仓库管理）

    只清理 KB_STORAGE_DIR 目录内的文件：路径来自数据库，但仍做一次归属校验，
    避免历史脏数据或人工改库把删除操作指向目录外的文件。
    """
    from app.config import settings

    path = (doc.get("filePath") or "").strip()
    if not path or doc.get("origin") != "upload":
        return
    try:
        target = Path(path).resolve()
        storage = settings.kb_storage_dir.resolve()
        if storage not in target.parents:
            logger.warning("跳过删除知识库原文（不在留存目录内）: %s", target)
            return
        target.unlink(missing_ok=True)
    except OSError:
        logger.warning("删除知识库原文失败（忽略）: %s", path, exc_info=True)


async def ingest_directory(directory: Path, *, force: bool = False,
                           limit: Optional[int] = None) -> dict:
    """批量导入目录下所有受支持格式的文件，返回汇总统计

    单个文件解析失败不中断整轮导入（一篇坏文档不该让知识库停在半路），
    失败清单在返回值里返回，由脚本打印并以非零码退出——静默跳过才是隐患。
    """
    from app.kb import store

    files = sorted(p for p in Path(directory).rglob("*")
                   if p.is_file() and p.suffix.lower() in _supported_suffixes())
    if limit:
        files = files[:limit]

    results, failures = [], []
    for path in files:
        try:
            results.append(await ingest_path(path, origin="repo", force=force))
        except (DocParseError, IngestError) as exc:
            failures.append({"file": path.name, "reason": str(exc)})
        except Exception as exc:
            logger.warning("导入失败：%s", path, exc_info=True)
            failures.append({"file": path.name, "reason": f"{type(exc).__name__}: {exc}"})

    stats = await store.stats()
    return {
        "scanned": len(files),
        "ingested": sum(1 for r in results if not r["skipped"]),
        "skipped": sum(1 for r in results if r["skipped"]),
        "chunks": sum(r["chunks"] for r in results),
        "failures": failures,
        "totalDocuments": stats["documents"],
        "totalChunks": stats["chunks"],
    }


# ---------- 内部 ----------

def _supported_suffixes():
    from app.kb.documents import SUPPORTED_SUFFIXES

    return SUPPORTED_SUFFIXES


def _parse(reader) -> ParsedDocument:
    try:
        return reader()
    except DocParseError:
        raise
    except Exception as exc:
        raise IngestError(f"文档解析失败：{exc}") from exc


async def _ingest_document(doc: ParsedDocument, origin: str, force: bool,
                           file_path: str = "") -> dict:
    from app.kb import store

    if not force and await document_exists(doc.doc_id, doc.sha256):
        return {"docId": doc.doc_id, "source": doc.source, "chunks": 0, "skipped": True,
                "sha256": doc.sha256}

    chunks = split_document(doc, chunk_size=settings.KB_CHUNK_SIZE,
                            chunk_min=settings.KB_CHUNK_MIN, chunk_max=settings.KB_CHUNK_MAX)
    if not chunks:
        raise IngestError(f"文档切分后没有可用片段：{doc.source}")

    await store.ensure_ready()
    texts = [c.content for c in chunks]
    # BM25 词表：正文分词 + 文档标题/小节/分类/标签（标签只在元数据里，正文没写也要能搜到）
    token_sources = [
        lexicon.index_terms(c.content, extra_terms=(
            [doc.title, doc.category] + c.tags +
            ([c.section] if c.section else [])))
        for c in chunks
    ]
    vectors = await embed_texts(texts)

    written = await upsert_document(
        {"doc_id": doc.doc_id, "name": doc.title, "source": doc.source, "kind": doc.kind,
         "category": doc.category, "species": doc.species, "tags": doc.tags,
         "sha256": doc.sha256, "origin": origin, "char_count": doc.char_count,
         "file_path": file_path or None},
        [{"doc_id": c.doc_id, "chunk_index": c.chunk_index, "section": c.section,
          "category": c.category, "species": c.species, "content": c.content,
          "tokens": t} for c, t in zip(chunks, token_sources)],
        vectors)
    lexicon.invalidate()
    return {"docId": doc.doc_id, "source": doc.source, "chunks": written,
            "skipped": False, "sha256": doc.sha256}


async def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    """批量向量化（分批调用，批间让出事件循环；单批失败补试后再抛）

    Embedding 不可用时报 IngestError：导入是显式运维动作，必须明确失败原因，
    不能像对话链路那样静默降级。
    """
    if not settings.embedding_enabled:
        raise IngestError(
            "未配置 Embedding（EMBEDDING_API_KEY / EMBEDDING_MODEL），无法导入知识库")
    try:
        embeddings = get_embeddings()
    except Exception as exc:
        raise IngestError(f"Embedding 客户端初始化失败：{exc}") from exc

    batch = max(1, settings.EMBED_BATCH_SIZE or _DEFAULT_BATCH)
    vectors: List[List[float]] = []
    for start in range(0, len(texts), batch):
        chunk_texts = list(texts[start:start + batch])
        vectors.extend(await _embed_batch(embeddings, chunk_texts))
        await asyncio.sleep(0)   # 让出事件循环：导入在服务能力内跑时不该独占线程
    if len(vectors) != len(texts):
        raise IngestError(
            f"Embedding 返回条数与请求不一致（{len(vectors)} vs {len(texts)}），已中止导入")
    return vectors


async def _embed_batch(embeddings, texts: List[str]) -> List[List[float]]:
    last_error: Optional[Exception] = None
    for attempt in range(_EMBED_RETRIES + 1):
        try:
            return await asyncio.to_thread(embeddings.embed_documents, texts)
        except Exception as exc:
            last_error = exc
            if attempt < _EMBED_RETRIES:
                await asyncio.sleep(_EMBED_RETRY_DELAY * (attempt + 1))
    raise IngestError(f"Embedding 调用失败（第 {len(texts)} 批量，已补试 "
                      f"{_EMBED_RETRIES} 次）：{last_error}")
