"""知识库入库编排。

职责：把「解析 → 切分 → 生成关键词 → 向量化 → 覆盖写入 → 索引失效」串成一条流程，
支持单文件、字节流（上传）与整目录批量三种入口。

主要导出：
    - ``IngestError``        入库失败（解析之外的流程性错误）
    - ``ingest_path``        入库单个文件
    - ``ingest_content``     入库上传的字节内容（并留存原文）
    - ``ingest_directory``   批量入库目录（供重建与脚本使用）
    - ``remove_original``    清理上传文档的留存原文
    - ``embed_texts``        批量向量化（带重试与条数校验）

外部依赖：``app.kb.documents``（解析）、``app.kb.chunking``（切分）、``app.kb.lexicon``（分词）、
``app.kb.store``（存储）、``app.llm.get_embeddings``。

使用示例：
    from app.kb import ingest

    await ingest.ingest_path(Path("app/knowledge/急症识别与院前处理.md"))
    summary = await ingest.ingest_directory(settings.knowledge_dir, force=True)

幂等性：按 ``doc_id`` 覆盖写入；``force=False`` 时内容 sha256 未变的文档直接跳过，
避免无谓的 Embedding 调用（成本与耗时的主要来源）。
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

_DEFAULT_BATCH = 10

# 向量化重试次数与退避基数：Embedding 服务限流（429）很常见，
# 退避后重试通常能成功；次数再多会显著拉长导入时间。
_EMBED_RETRIES = 2
_EMBED_RETRY_DELAY = 1.0


class IngestError(Exception):
    """入库流程错误（切分后无片段、Embedding 未配置或调用失败等）。

    与 ``DocParseError`` 区分：后者是「文档本身有问题」，本类是「流程 / 依赖有问题」，
    但对调用方而言都属于可展示给管理员的 400 级错误。
    """

async def ingest_path(path: Path, origin: str = "repo", *, force: bool = False,
                      doc_id: Optional[str] = None, title: Optional[str] = None,
                      category: Optional[str] = None,
                      species: Optional[Sequence[str]] = None) -> dict:
    """入库单个磁盘文件。

    Args:
        path: 文件路径。
        origin: 来源标记（``repo`` 仓库语料 / ``upload`` 后台上传）。
        force: True 时忽略内容哈希强制重新向量化。
        doc_id: 覆盖文档 ID。
        title: 覆盖标题。
        category: 覆盖分类。
        species: 覆盖物种。

    Returns:
        ``{"docId", "source", "chunks", "skipped", "sha256"}``。

    Raises:
        DocParseError: 文档解析失败。
        IngestError: 切分或向量化失败。
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
    """入库上传的字节内容。

    与 ``ingest_path`` 的差别：先把原文落盘留存（供预览重解析与原文下载），
    且固定 ``force=True``——上传同名文件的本意就是「更新它」，不该被哈希跳过。

    Args:
        filename: 原始文件名。
        data: 文件字节。
        origin: 来源标记，默认 ``upload``。
        doc_id: 覆盖文档 ID。
        title: 覆盖标题。
        category: 覆盖分类。
        species: 覆盖物种。

    Returns:
        同 ``ingest_path``。

    Raises:
        DocParseError: 文档解析失败。
        IngestError: 切分或向量化失败。
    """
    doc = _parse(lambda: parse_bytes(filename, data, doc_id=doc_id, title=title,
                                     category=category, species=list(species or [])))
    file_path = _persist_original(doc.doc_id, filename, data)
    return await _ingest_document(doc, origin=origin, force=True, file_path=file_path)


def _persist_original(doc_id: str, filename: str, data: bytes) -> str:
    """把上传原文保存到留存目录。

    Args:
        doc_id: 文档 ID（作为文件名主干，保证同名文档覆盖而非堆积）。
        filename: 原始文件名（取后缀）。
        data: 文件字节。

    Returns:
        留存文件的绝对路径；保存失败时返回空串（预览会回退到片段拼接）。

    Raises:
        无。
    """
    from app.config import settings

    try:
        directory = settings.kb_storage_dir
        directory.mkdir(parents=True, exist_ok=True)
        # 后缀截断到 8 字符：防止构造超长后缀拼出异常的路径。
        suffix = Path(filename).suffix.lower()[:8]
        target = directory / f"{doc_id}{suffix}"
        target.write_bytes(data)
        return str(target.resolve())
    except OSError:
        # 留存失败不影响入库本身：原文只是预览的「最佳来源」，缺了还有片段可拼。
        logger.warning("知识库原文留存失败（预览将回退到片段拼接）: doc_id=%s", doc_id,
                       exc_info=True)
        return ""


def remove_original(doc: dict) -> None:
    """删除上传文档的留存原文。

    Args:
        doc: 文档元信息（需含 ``filePath`` / ``origin``）。

    Returns:
        None。

    Raises:
        无。
    """
    from app.config import settings

    path = (doc.get("filePath") or "").strip()
    # 只删「上传」来源：仓库语料是代码库的一部分，删了会影响下次全量重建。
    if not path or doc.get("origin") != "upload":
        return
    try:
        target = Path(path).resolve()
        storage = settings.kb_storage_dir.resolve()
        # 路径归属校验：数据库里的 file_path 不可信，防止误删留存目录之外的文件。
        if storage not in target.parents:
            logger.warning("跳过删除知识库原文（不在留存目录内）: %s", target)
            return
        target.unlink(missing_ok=True)
    except OSError:
        logger.warning("删除知识库原文失败（忽略）: %s", path, exc_info=True)


async def ingest_directory(directory: Path, *, force: bool = False,
                           limit: Optional[int] = None) -> dict:
    """批量入库目录下的全部受支持文档。

    单个文件失败不中断整轮：失败项收集到 ``failures`` 由调用方汇报，
    这样一次重建不会因为一篇坏文档而全盘失败。

    Args:
        directory: 语料目录（递归扫描）。
        force: True 时全部重新向量化。
        limit: 只处理前 N 个文件（调试用）。

    Returns:
        ``{scanned, ingested, skipped, chunks, failures, totalDocuments, totalChunks}``。

    Raises:
        无——单文件异常已归入 ``failures``。
    """
    from app.kb import store

    # 排序保证多次运行的导入顺序一致，便于复现问题。
    files = sorted(p for p in Path(directory).rglob("*")
                   if p.is_file() and p.suffix.lower() in _supported_suffixes())
    if limit:
        files = files[:limit]

    results, failures = [], []
    for path in files:
        try:
            results.append(await ingest_path(path, origin="repo", force=force))
        except (DocParseError, IngestError) as exc:
            # 已知错误：原因可直接展示给管理员。
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


def _supported_suffixes():
    """取受支持的扩展名集合。

    延迟 import 是为了避免模块级循环依赖（``documents`` 与本模块相互引用）。

    Returns:
        扩展名元组。

    Raises:
        无。
    """
    from app.kb.documents import SUPPORTED_SUFFIXES

    return SUPPORTED_SUFFIXES


def _parse(reader) -> ParsedDocument:
    """执行解析并统一异常类型。

    Args:
        reader: 无参可调用对象（延迟执行解析，便于集中捕获）。

    Returns:
        解析结果。

    Raises:
        DocParseError: 原样透传（已是可展示错误）。
        IngestError: 其它异常包装成入库错误。
    """
    try:
        return reader()
    except DocParseError:
        raise
    except Exception as exc:
        raise IngestError(f"文档解析失败：{exc}") from exc


async def _ingest_document(doc: ParsedDocument, origin: str, force: bool,
                           file_path: str = "") -> dict:
    """单篇文档的实际入库流程。

    Args:
        doc: 解析结果。
        origin: 来源标记。
        force: 是否忽略内容哈希。
        file_path: 原文留存路径（可为空）。

    Returns:
        ``{"docId", "source", "chunks", "skipped", "sha256"}``。

    Raises:
        IngestError: 切分后无片段，或向量化失败 / 条数不一致。
    """
    from app.kb import store

    if not force and await document_exists(doc.doc_id, doc.sha256):
        # 内容未变：跳过 Embedding（这是导入耗时与成本的大头）。
        return {"docId": doc.doc_id, "source": doc.source, "chunks": 0, "skipped": True,
                "sha256": doc.sha256}

    chunks = split_document(doc, chunk_size=settings.KB_CHUNK_SIZE,
                            chunk_min=settings.KB_CHUNK_MIN, chunk_max=settings.KB_CHUNK_MAX)
    if not chunks:
        raise IngestError(f"文档切分后没有可用片段：{doc.source}")

    await store.ensure_ready()
    texts = [c.content for c in chunks]
    # 关键词在入库时一次性算好并落库（tokens 列）：检索时直接读，避免每次查询都分词全库。
    # 额外把标题 / 分类 / 标签 / 小节并入词表——这些词常常不在正文里出现。
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
         # 空串统一转 None：数据库里用 NULL 表达「没有留存原文」，语义比空串更清晰。
         "file_path": file_path or None},
        [{"doc_id": c.doc_id, "chunk_index": c.chunk_index, "section": c.section,
          "category": c.category, "species": c.species, "content": c.content,
          "tokens": t} for c, t in zip(chunks, token_sources)],
        vectors)
    # 写库后让进程内 BM25 索引失效（多实例通过 kb_meta.version 各自感知）。
    lexicon.invalidate()
    return {"docId": doc.doc_id, "source": doc.source, "chunks": written,
            "skipped": False, "sha256": doc.sha256}


async def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    """批量向量化。

    Args:
        texts: 待向量化文本。

    Returns:
        与 ``texts`` 等长的向量列表。

    Raises:
        IngestError: 未配置 Embedding、客户端初始化失败、重试后仍失败，
            或返回条数与请求不一致（不一致意味着向量与片段错位，必须中止）。
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
        # 每批之间让出事件循环：导入是长任务，不能把事件循环一直占住
        # （服务同时在处理对话请求）。
        await asyncio.sleep(0)
    if len(vectors) != len(texts):
        raise IngestError(
            f"Embedding 返回条数与请求不一致（{len(vectors)} vs {len(texts)}），已中止导入")
    return vectors


async def _embed_batch(embeddings, texts: List[str]) -> List[List[float]]:
    """单批向量化（带线性退避重试）。

    Args:
        embeddings: Embedding 客户端。
        texts: 本批文本。

    Returns:
        向量列表。

    Raises:
        IngestError: 重试耗尽仍失败。
    """
    last_error: Optional[Exception] = None
    for attempt in range(_EMBED_RETRIES + 1):
        try:
            # SDK 是同步的，必须放线程池，否则一批 10 条的长文本会阻塞整个事件循环。
            return await asyncio.to_thread(embeddings.embed_documents, texts)
        except Exception as exc:
            last_error = exc
            # 最后一次失败后不再等待，直接抛出（省掉无意义的尾部 sleep）。
            if attempt < _EMBED_RETRIES:
                await asyncio.sleep(_EMBED_RETRY_DELAY * (attempt + 1))
    raise IngestError(f"Embedding 调用失败（第 {len(texts)} 批量，已补试 "
                      f"{_EMBED_RETRIES} 次）：{last_error}")
