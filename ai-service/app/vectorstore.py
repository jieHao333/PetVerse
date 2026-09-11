"""pgvector 向量库访问层（RAG 检索）

两条检索通路：
  1. 语义检索（首选）：langchain_postgres.PGVector + OpenAI 兼容 Embedding，
     写入 / 查询都走 pgvector 的余弦距离，能理解同义表达；
  2. 关键词降级：未配置 Embedding 或 pgvector 不可用时，直接从内置知识文件做
     字符 bigram 覆盖度打分，保证 RAG 功能在无外部依赖时依然可演示。

集合（collection）由 PGVector 自动管理（langchain_pg_collection / langchain_pg_embedding），
无需手工建表；只需数据库提前执行 `CREATE EXTENSION vector`（见 db/schema_pgvector.sql）。

为什么用同步 PGVector + asyncio.to_thread：psycopg 的异步连接在 Windows 默认的
ProactorEventLoop 下不可用，改用同步 engine 并丢到线程池，跨平台一致且不阻塞事件循环。
所有方法均做异常降级：向量库不可用时返回空列表 / 静默失败，绝不让主流程报错。
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import List, Optional

from app.config import settings
from app.llm import get_embeddings

logger = logging.getLogger(__name__)

# 知识库 Markdown 目录（app/knowledge/*.md），同时作为关键词降级的语料来源
KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"

# 向量库不可用时的冷却时间：避免 PG 宕机期间每个请求都尝试连接并打印堆栈
_FAIL_COOLDOWN = 60.0

_store = None            # PGVector 实例（惰性创建）
_store_failed_at = 0.0   # 最近一次失败时间戳（冷却期内直接走关键词降级）
_fallback_chunks: Optional[List[dict]] = None


# ---------- 语义检索（pgvector） ----------

def _build_store():
    """创建同步 PGVector 实例（SQLAlchemy 同步 engine + psycopg）"""
    from langchain_postgres import PGVector
    from sqlalchemy import create_engine

    engine = create_engine(settings.pg_conn_str, pool_pre_ping=True)
    return PGVector(
        embeddings=get_embeddings(),
        connection=engine,
        collection_name=settings.RAG_COLLECTION,
        embedding_length=settings.EMBEDDING_DIM,
        use_jsonb=True,
        async_mode=False,
        create_extension=True,   # 自动 CREATE EXTENSION IF NOT EXISTS vector
    )


def get_store():
    """获取 PGVector 单例；不可用（未配置 RAG / 处于失败冷却期）时返回 None"""
    global _store, _store_failed_at
    if _store is not None:
        return _store
    if not settings.rag_enabled:
        return None
    if _store_failed_at and (time.monotonic() - _store_failed_at) < _FAIL_COOLDOWN:
        return None
    try:
        _store = _build_store()
        _store_failed_at = 0.0
        return _store
    except Exception:
        logger.warning("pgvector 向量库初始化失败，RAG 降级为关键词检索")
        _store_failed_at = time.monotonic()
        return None


async def retrieve(query: str, k: Optional[int] = None) -> List[dict]:
    """语义检索：返回 [{content, source, category, score}]，score 为 0-1 相似度（越大越相关）

    向量库不可用或检索异常时，自动降级为关键词检索。
    """
    global _store_failed_at
    top_k = k or settings.RAG_TOP_K
    store = get_store()
    if store is not None:
        try:
            # PGVector 返回 (Document, distance)，cosine distance 越小越相关
            pairs = await asyncio.to_thread(
                store.similarity_search_with_score, query, top_k)
            results = []
            for doc, distance in pairs:
                score = _distance_to_score(distance)
                if score < settings.RAG_SCORE_THRESHOLD:
                    continue
                meta = doc.metadata or {}
                results.append({
                    "content": doc.page_content,
                    "source": meta.get("source", ""),
                    "category": meta.get("category", ""),
                    "score": round(score, 4),
                })
            return results
        except Exception:
            # 连接类失败进入冷却期，后续请求直接走关键词降级，避免反复打印堆栈
            logger.warning("pgvector 检索失败，降级为关键词检索")
            _store_failed_at = time.monotonic()
    return _keyword_retrieve(query, top_k)


def _distance_to_score(distance: float) -> float:
    """余弦距离 -> 相似度（0-1）：PGVector cosine 距离为 1 - cos，故相似度 = 1 - distance"""
    try:
        return max(0.0, min(1.0, 1.0 - float(distance)))
    except (TypeError, ValueError):
        return 0.0


# ---------- 关键词降级检索 ----------

def _load_fallback_chunks() -> List[dict]:
    """加载内置知识文件并按段落切块（首次调用时缓存）

    先剔除 Markdown 标题行，再按空行切段：避免「## 标题 + 紧跟正文」的块
    因整块以 # 开头而被误丢弃（标题与正文间常无空行）。
    """
    global _fallback_chunks
    if _fallback_chunks is not None:
        return _fallback_chunks
    chunks: List[dict] = []
    try:
        for md in sorted(KNOWLEDGE_DIR.glob("*.md")):
            text = md.read_text(encoding="utf-8")
            # 去掉标题行，保留正文（标题信息由 category / source 承载）
            body = "\n".join(line for line in text.splitlines()
                             if not line.lstrip().startswith("#"))
            for para in body.split("\n\n"):
                para = " ".join(para.split())   # 折叠换行与多余空白
                if len(para) < 10:
                    continue
                chunks.append({"content": para, "source": md.name,
                               "category": md.stem, "score": 0.0})
    except Exception:
        logger.warning("读取内置知识文件失败，关键词检索语料为空", exc_info=True)
    _fallback_chunks = chunks
    return chunks


def _bigrams(text: str) -> set:
    """中文友好的字符 bigram 集合（无需分词依赖）"""
    clean = "".join(ch for ch in text if ch.strip())
    return {clean[i:i + 2] for i in range(max(0, len(clean) - 1))}


def _keyword_retrieve(query: str, top_k: int) -> List[dict]:
    """关键词降级：按「查询 bigram 被段落覆盖的比例」打分排序

    用覆盖率（overlap / len(query_bigrams)）而非 Jaccard：长段落不会因分母过大
    被稀释，只要命中查询中的关键词即可获得较高分，更贴近「关键词检索」的直觉。
    """
    q = _bigrams(query)
    if not q:
        return []
    floor = min(settings.RAG_SCORE_THRESHOLD, 0.12)
    scored = []
    for chunk in _load_fallback_chunks():
        c = _bigrams(chunk["content"])
        if not c:
            continue
        overlap = len(q & c)
        if overlap == 0:
            continue
        score = min(1.0, overlap / len(q))
        if score >= floor:
            item = dict(chunk)
            item["score"] = round(score, 4)
            scored.append(item)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def format_context(results: List[dict]) -> str:
    """把检索结果拼成注入 Prompt 的知识上下文块（含来源标注，便于回答引用）"""
    if not results:
        return ""
    lines = []
    for i, r in enumerate(results, 1):
        source = r.get("source") or r.get("category") or "知识库"
        lines.append(f"[{i}] 来源：{source}\n{r['content']}")
    return "\n\n".join(lines)


# ---------- 写入 / 清空（知识库导入脚本使用） ----------

async def aadd_texts(texts: List[str], metadatas: List[dict]) -> int:
    """批量写入知识片段到 pgvector，返回写入条数（失败返回 0）"""
    store = get_store()
    if store is None:
        logger.warning("向量库不可用，跳过知识写入")
        return 0
    try:
        from langchain_core.documents import Document
        docs = [Document(page_content=t, metadata=m) for t, m in zip(texts, metadatas)]
        ids = await asyncio.to_thread(store.add_documents, docs)
        return len(ids)
    except Exception:
        logger.warning("知识写入 pgvector 失败", exc_info=True)
        return 0


async def aclear() -> bool:
    """清空向量集合（导入前幂等重建），成功返回 True

    delete_collection 会连同集合元数据一起删除，PGVector 之后不会再自动建集合
    （add_documents 会抛 "Collection not found"），因此删除后立即重建空集合。
    """
    store = get_store()
    if store is None:
        return False
    try:
        def _reset():
            try:
                store.delete_collection()
            except Exception:
                pass   # 首次导入无集合，忽略
            store.create_collection()   # 重建空集合，供后续写入
        await asyncio.to_thread(_reset)
        return True
    except Exception:
        logger.warning("清空 pgvector 集合失败（首次导入可忽略）", exc_info=True)
        return False
