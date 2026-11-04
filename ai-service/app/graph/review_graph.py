"""商品评论摘要编排图。

职责：拉取某商品的全部评论 → 让 LLM 归纳情感倾向 / 一句话总结 / 优缺点 / 关键词 →
结果写入二级缓存。评论数不足或 LLM 失败时退回规则摘要。

主要导出：
    - ``ReviewState``          图状态定义
    - ``summarize_product``    对外入口（带缓存与单飞）
    - ``get_review_graph``     取得（并编译一次）图实例

外部依赖：``app.clients.biz``（评论数据）、``app.llm``、``app.cache``、``app.schemas``。

使用示例：
    from app.graph.review_graph import summarize_product

    summary = await summarize_product(product_id=128)
    # {"sentiment": "positive", "one_line": "...", "pros": [...], "count": 32, ...}

设计取舍：「优点 / 缺点必须来自真实评论」是硬要求，因此提示词禁止编造，
且 ``count`` 由服务端按有效评论数回填，不采信模型的计数。
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

# 参与摘要的评论条数上限：再多对结论没有增益，只会推高 token 成本与延迟。
_MAX_REVIEWS = 40
# 单条评论截断长度：长评论往往夹带大量无关叙述，截断保留观点部分即可。
_MAX_REVIEW_CHARS = 300


class ReviewState(TypedDict, total=False):
    """评论摘要图状态。"""

    product_id: int
    reviews: List[Dict[str, Any]]
    summary: Dict[str, Any]


async def fetch_reviews(state: ReviewState) -> Dict[str, Any]:
    """拉取评论节点。

    Args:
        state: 图状态（读 ``product_id``）。

    Returns:
        ``{"reviews": [...]}``；拉取失败时为空列表。

    Raises:
        无。
    """
    product_id = state.get("product_id", 0)
    # 取 50 条而摘要只用前 40 条：留一点余量，让「无内容评论」被过滤后仍有足够样本。
    reviews = await biz.get_product_reviews(product_id, limit=50)
    return {"reviews": reviews or []}


def _review_lines(reviews: List[Dict[str, Any]]) -> List[str]:
    """把评论整理成给模型的列表文本。

    Args:
        reviews: 评论列表。

    Returns:
        形如 ``[5星] 评论内容`` 的行列表（已压平换行、截断长度）。

    Raises:
        无。
    """
    lines = []
    for r in reviews[:_MAX_REVIEWS]:
        # 换行会破坏「一行一条」的结构，压成空格；纯空白评论直接跳过。
        content = (r.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        rating = r.get("rating")
        lines.append(f"[{rating}星] {content[:_MAX_REVIEW_CHARS]}")
    return lines


async def summarize(state: ReviewState) -> Dict[str, Any]:
    """摘要节点：LLM 归纳，失败降级为规则摘要。

    Args:
        state: 图状态（读 ``reviews``）。

    Returns:
        ``{"summary": {...}}``，字段与 ``ReviewSummary`` 对齐；无有效评论时返回中性占位。

    Raises:
        无。
    """
    reviews = state.get("reviews") or []
    valid = [r for r in reviews if (r.get("content") or "").strip()]
    if not valid:
        # 不调模型：没有评论时让模型「总结」必然产出编造内容。
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
        # count 用服务端统计值覆盖模型输出：模型对条数的估计不可靠，而前端会展示这个数字。
        data["count"] = len(valid)
        return {"summary": data}
    except Exception:
        logger.warning("LLM 评论摘要失败，降级为规则摘要", exc_info=True)
        return {"summary": _rule_summary(valid)}


async def persist_cache(state: ReviewState) -> Dict[str, Any]:
    """缓存节点：只缓存「有评论」的摘要。

    空摘要（无评论）不缓存：用户看到「暂无评论」后可能过一会儿再来，
    若缓存命中就会一直看到旧结论。

    Args:
        state: 图状态。

    Returns:
        空字典（该节点不修改状态）。

    Raises:
        无。
    """
    product_id = state.get("product_id", 0)
    summary = state.get("summary") or {}
    if summary.get("count", 0) > 0:
        await cache.set_cached(f"ai:review:summary:{product_id}", summary,
                               settings.REVIEW_SUMMARY_TTL)
    return {}


def _rule_summary(reviews: List[Dict[str, Any]]) -> Dict[str, Any]:
    """基于评分与情感词的规则摘要（LLM 不可用时的兜底）。

    Args:
        reviews: 有效评论列表。

    Returns:
        与 ``ReviewSummary`` 同构的字典。

    Raises:
        无。
    """
    ratings = [r.get("rating") for r in reviews if isinstance(r.get("rating"), (int, float))]
    avg = sum(ratings) / len(ratings) if ratings else 0
    # 阈值与电商常用五档评分对齐：4 分以上算正面，3 分以下算负面，中间为中性。
    sentiment = "positive" if avg >= 4 else "negative" if avg < 3 else "neutral"

    contents = [(r.get("content") or "").strip() for r in reviews]
    contents = [c for c in contents if c]
    positives = [c[:40] for c in contents if _is_positive(c)][:3]
    negatives = [c[:40] for c in contents if _is_negative(c)][:3]

    return {
        "sentiment": sentiment,
        "one_line": f"共 {len(reviews)} 条评论，平均评分 {avg:.1f} 分，整体评价"
                    f"{'偏正面' if sentiment == 'positive' else '偏负面' if sentiment == 'negative' else '中性'}。",
        # 没有命中任何情感词的极端情况给一句中性描述，避免前端出现空列表。
        "pros": positives or ["多数用户反馈使用体验尚可"],
        "cons": negatives or ["暂无明显负面反馈"],
        "keywords": _keywords(contents),
        "count": len(reviews),
    }


# 规则摘要用的情感词典：只需覆盖常见短评措辞，宁可漏判也不要误判
# （误判会让「优点」里出现差评原句，比留空更糟糕）。
_POS_WORDS = ["好", "不错", "满意", "喜欢", "推荐", "值得", "快", "香", "可爱", "划算"]
_NEG_WORDS = ["差", "不好", "失望", "退", "贵", "慢", "破", "问题", "异味", "一般"]


def _is_positive(text: str) -> bool:
    """判断评论是否含正面情感词。

    Args:
        text: 评论内容。

    Returns:
        True 表示命中任一正面词。

    Raises:
        无。
    """
    return any(w in text for w in _POS_WORDS)


def _is_negative(text: str) -> bool:
    """判断评论是否含负面情感词。

    Args:
        text: 评论内容。

    Returns:
        True 表示命中任一负面词。

    Raises:
        无。
    """
    return any(w in text for w in _NEG_WORDS)


def _keywords(contents: List[str]) -> List[str]:
    """用 n-gram 频次提取关键词（无分词器依赖的简化方案）。

    只统计 2~4 字的非 ASCII 片段：中文短评的关键词多是双字词或三字短语，
    而 ASCII 片段（型号、英文品牌）用 n-gram 会切出大量无意义碎片。

    Args:
        contents: 评论内容列表。

    Returns:
        频次最高的 6 个候选词（可能包含重叠子串，仅作展示用）。

    Raises:
        无。
    """
    from collections import Counter
    counter: Counter = Counter()
    for text in contents:
        # 先剔除空白，避免词被空格截断。
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
    """取得（首次调用时编译）评论摘要图。

    Returns:
        已编译的图（无状态，不需要 checkpointer）。

    Raises:
        无。
    """
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
    """生成商品评论摘要。

    Args:
        product_id: 商品 ID。
        use_cache: 是否使用缓存。

    Returns:
        摘要字典；无评论时 ``count`` 为 0。

    Raises:
        Exception: 图执行异常向上抛，由路由层转成统一错误报文。
    """
    cache_key = f"ai:review:summary:{product_id}"
    if use_cache:
        cached = await cache.get_cached(cache_key)
        if cached is not None:
            return cached
    # 单飞：商品详情页可能被大量用户同时打开，未命中时不应每个请求都去拉评论 + 调模型。
    async with cache.single_flight(cache_key):
        if use_cache:
            # 等锁期间可能已被前一个请求回填，复查一次避免重复计算。
            cached = await cache.get_cached(cache_key)
            if cached is not None:
                return cached
        state: ReviewState = {"product_id": product_id}
        result = await get_review_graph().ainvoke(state)
        return result.get("summary") or {}
