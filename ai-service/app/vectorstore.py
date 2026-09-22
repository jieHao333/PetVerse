"""
pgvector 向量库访问层（RAG 检索）

"""
import asyncio
import logging
from typing import List, Optional

from app.config import settings
from app.llm import get_embeddings

logger = logging.getLogger(__name__)


class RagUnavailable(Exception):
    """RAG 检索链路不可用（Embedding 未配置 / pgvector 初始化或查询失败）"""


_store = None   # PGVector 实例（惰性创建）


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
    """获取 PGVector 单例；不可用时抛 RagUnavailable"""
    global _store
    if _store is not None:
        return _store
    if not settings.embedding_enabled:
        raise RagUnavailable(
            "知识库检索不可用：请在 ai-service/.env 配置 EMBEDDING_API_KEY / EMBEDDING_MODEL")
    try:
        _store = _build_store()
    except Exception as exc:
        logger.warning("pgvector 向量库初始化失败", exc_info=True)
        raise RagUnavailable(
            "知识库检索不可用：请确认 PostgreSQL 可访问且已安装 pgvector 扩展") from exc
    return _store


async def retrieve(query: str, k: Optional[int] = None) -> List[dict]:
    """语义检索：返回 [{content, source, category, score}]，score 为 0-1 相似度（越大越相关）

    向量库不可用或检索异常时抛 RagUnavailable；相似度低于 RAG_SCORE_THRESHOLD 的
    片段直接丢弃，只保留达到阈值的召回结果。
    """
    top_k = k or settings.RAG_TOP_K
    store = get_store()
    try:
        # PGVector 返回 (Document, distance)，cosine distance 越小越相关
        pairs = await asyncio.to_thread(
            store.similarity_search_with_score, query, top_k)
    except Exception as exc:
        logger.warning("pgvector 检索失败", exc_info=True)
        raise RagUnavailable("知识库检索失败：向量库查询异常") from exc

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


def _distance_to_score(distance: float) -> float:
    """余弦距离 -> 相似度（0-1）：PGVector cosine 距离为 1 - cos，故相似度 = 1 - distance"""
    try:
        return max(0.0, min(1.0, 1.0 - float(distance)))
    except (TypeError, ValueError):
        return 0.0


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
    """批量写入知识片段到 pgvector，返回写入条数；失败抛异常（导入脚本据此报错退出）"""
    store = get_store()
    from langchain_core.documents import Document

    docs = [Document(page_content=t, metadata=m) for t, m in zip(texts, metadatas)]
    ids = await asyncio.to_thread(store.add_documents, docs)
    return len(ids)


async def aclear() -> None:
    """清空向量集合（导入前幂等重建）

    delete_collection 会连同集合元数据一起删除，PGVector 之后不会再自动建集合
    （add_documents 会抛 "Collection not found"），因此删除后立即重建空集合。
    """
    store = get_store()

    def _reset():
        try:
            store.delete_collection()
        except Exception:
            pass   # 首次导入无集合，忽略
        store.create_collection()   # 重建空集合，供后续写入

    await asyncio.to_thread(_reset)
