"""商品评论摘要工作流（review graph）

LangGraph 编排：
    fetch_reviews → summarize → persist_cache
  - fetch_reviews：通过 clients 拉取商品评论（真实数据）；
  - summarize：真实模式用 LLM 结构化输出 ReviewSummary；mock / 少评论时用规则摘要；
  - persist_cache：两级缓存写入（Redis 热 + ai_cache 温，TTL 可配），命中缓存直接返回。

对外由 app/review.py 的 POST /ai/shop/review/summary 调用。
"""
import logging
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from app import cache
from app.clients import biz
from app.config import settings
from app.llm import astructured_invoke
from app.schemas import ReviewSummary

logger = logging.getLogger(__name__)

# 参与摘要的最大评论条数与单条截断长度（控制上下文规模）
_MAX_REVIEWS = 40
_MAX_REVIEW_CHARS = 300


class ReviewState(TypedDict, total=False):
    """评论摘要图状态"""

    product_id: int
    reviews: List[Dict[str, Any]]
    summary: Dict[str, Any]


async def fetch_reviews(state: ReviewState) -> Dict[str, Any]:
    """拉取商品评论（失败返回空列表）"""
    product_id = state.get("product_id", 0)
    reviews = await biz.get_product_reviews(product_id, limit=50)
    return {"reviews": reviews or []}


def _review_lines(reviews: List[Dict[str, Any]]) -> List[str]:
    """把评论整理为「评分 + 内容」文本行"""
    lines = []
    for r in reviews[:_MAX_REVIEWS]:
        content = (r.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        rating = r.get("rating")
        lines.append(f"[{rating}星] {content[:_MAX_REVIEW_CHARS]}")
    return lines


async def summarize(state: ReviewState) -> Dict[str, Any]:
    """生成摘要：真实模式走 LLM，其余走规则"""
    reviews = state.get("reviews") or []
    valid = [r for r in reviews if (r.get("content") or "").strip()]
    if not valid:
        return {"summary": {
            "sentiment": "neutral", "one_line": "该商品暂无足够评论可供总结",
            "pros": [], "cons": [], "keywords": [], "count": 0,
        }}

    if settings.is_mock:
        return {"summary": _rule_summary(valid)}

    lines = _review_lines(valid)
    system_prompt = (
        "你是电商评论分析助手。请根据真实用户评论，客观总结商品口碑，"
        "严格按结构化字段输出。优点与缺点必须来自评论内容，不要编造；"
        "关键词提取 3-6 个高频词。"
    )
    user_prompt = "用户评论：\n" + "\n".join(lines)
    try:
        result: ReviewSummary = await astructured_invoke(ReviewSummary, system_prompt, user_prompt)
        data = result.model_dump()
        data["count"] = len(valid)
        return {"summary": data}
    except Exception:
        logger.warning("LLM 评论摘要失败，降级为规则摘要", exc_info=True)
        return {"summary": _rule_summary(valid)}


async def persist_cache(state: ReviewState) -> Dict[str, Any]:
    """写入两级缓存：Redis（热）+ PostgreSQL ai_cache（温兜底），TTL 可配"""
    product_id = state.get("product_id", 0)
    summary = state.get("summary") or {}
    if summary.get("count", 0) > 0:
        await cache.set_cached(f"ai:review:summary:{product_id}", summary,
                               settings.REVIEW_SUMMARY_TTL)
    return {}


def _rule_summary(reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    """规则摘要（mock / LLM 失败降级）：按评分均值定情感，抽取短句作优缺点"""
    ratings = [r.get("rating") for r in reviews if isinstance(r.get("rating"), (int, float))]
    avg = sum(ratings) / len(ratings) if ratings else 0
    sentiment = "positive" if avg >= 4 else "negative" if avg < 3 else "neutral"

    # 简单按长度挑选代表性短句（正/负各取若干）
    contents = [(r.get("content") or "").strip() for r in reviews]
    contents = [c for c in contents if c]
    positives = [c[:40] for c in contents if _is_positive(c)][:3]
    negatives = [c[:40] for c in contents if _is_negative(c)][:3]

    return {
        "sentiment": sentiment,
        "one_line": f"共 {len(reviews)} 条评论，平均评分 {avg:.1f} 分，整体评价"
                    f"{'偏正面' if sentiment == 'positive' else '偏负面' if sentiment == 'negative' else '中性'}。",
        "pros": positives or ["多数用户反馈使用体验尚可"],
        "cons": negatives or ["暂无明显负面反馈"],
        "keywords": _keywords(contents),
        "count": len(reviews),
    }


_POS_WORDS = ["好", "不错", "满意", "喜欢", "推荐", "值得", "快", "香", "可爱", "划算"]
_NEG_WORDS = ["差", "不好", "失望", "退", "贵", "慢", "破", "问题", "异味", "一般"]


def _is_positive(text: str) -> bool:
    return any(w in text for w in _POS_WORDS)


def _is_negative(text: str) -> bool:
    return any(w in text for w in _NEG_WORDS)


def _keywords(contents: List[str]) -> List[str]:
    """极简关键词提取：按 2-4 字 n-gram 词频（无分词依赖），供前端展示"""
    from collections import Counter
    counter: Counter = Counter()
    for text in contents:
        clean = "".join(ch for ch in text if ch.strip())
        for size in (2, 3, 4):
            for i in range(len(clean) - size + 1):
                gram = clean[i:i + size]
                if gram.isascii():
                    continue
                counter[gram] += 1
    return [w for w, _ in counter.most_common(6)]


_graph = None


def get_review_graph():
    """构建并缓存评论摘要图"""
    global _graph
    if _graph is not None:
        return _graph
    builder = StateGraph(ReviewState)
    builder.add_node("fetch_reviews", fetch_reviews)
    builder.add_node("summarize", summarize)
    builder.add_node("persist_cache", persist_cache)
    builder.add_edge(START, "fetch_reviews")
    builder.add_edge("fetch_reviews", "summarize")
    builder.add_edge("summarize", "persist_cache")
    builder.add_edge("persist_cache", END)
    _graph = builder.compile()
    return _graph


async def summarize_product(product_id: int, use_cache: bool = True) -> Dict[str, Any]:
    """生成商品评论摘要（优先读两级缓存），返回摘要 dict"""
    cache_key = f"ai:review:summary:{product_id}"
    if use_cache:
        cached = await cache.get_cached(cache_key)
        if cached is not None:
            return cached
    state: ReviewState = {"product_id": product_id}
    result = await get_review_graph().ainvoke(state)
    return result.get("summary") or {}
