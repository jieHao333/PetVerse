"""个性化推荐工作流（recommend graph）

LangGraph 编排：
    gather_profile → recall_candidates → rerank → persist_cache
  - gather_profile：并发拉取用户画像数据（宠物 / 订单 / 购物车 / 评价 / 我的动态）；
  - recall_candidates：基于画像关键词召回商品候选 + 热门商品 + 热门动态；
  - rerank：真实模式用 LLM 结合画像重排并生成推荐理由；mock / 失败时按启发式排序；
  - persist_cache：按用户 + 场景写 Redis 缓存（TTL 可配）。

对外由 app/recommend.py 的 GET /ai/recommend/feed 调用。
"""
import asyncio
import logging
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from app import memory
from app.clients import biz
from app.config import settings
from app.llm import astructured_invoke
from app.schemas import RecommendResult

logger = logging.getLogger(__name__)

# 召回规模
_MAX_PRODUCT_CANDIDATES = 16
_MAX_SPACE_CANDIDATES = 8
# 最终返回条数
_TOP_N = 8


class RecommendState(TypedDict, total=False):
    """推荐图状态"""

    user_id: int
    scene: str                   # home / shop
    profile: Dict[str, Any]      # 画像原始数据
    keywords: List[str]          # 画像关键词
    candidates: List[Dict[str, Any]]  # 候选（商品 + 动态）
    items: List[Dict[str, Any]]  # 最终推荐项
    summary: str


def _first_category(items: List[dict]) -> List[int]:
    """从订单/购物车/评价中提取商品分类"""
    cats = []
    for it in items or []:
        cat = it.get("category")
        if isinstance(cat, int):
            cats.append(cat)
    return cats


async def gather_profile(state: RecommendState) -> Dict[str, Any]:
    """并发拉取用户画像（任一失败均为空，不影响其余）"""
    user_id = state.get("user_id", 0)
    pets, orders, cart, reviews, my_posts = await asyncio.gather(
        biz.get_my_pets(user_id),
        biz.get_my_orders(user_id, limit=10),
        biz.get_my_cart(user_id),
        biz.get_my_reviews(user_id, limit=10),
        biz.get_my_posts(user_id, limit=5),
        return_exceptions=True,
    )

    def _safe(v):
        return v if isinstance(v, (list, dict)) else []

    profile = {
        "pets": _safe(pets),
        "orders": _safe(orders),
        "cart": _safe(cart),
        "reviews": _safe(reviews),
        "my_posts": _safe(my_posts),
    }
    return {"profile": profile}


async def recall_candidates(state: RecommendState) -> Dict[str, Any]:
    """召回候选：画像关键词搜索商品 + 热门商品 + 热门动态"""
    user_id = state.get("user_id", 0)
    profile = state.get("profile") or {}

    # 1. 汇总关键词：宠物物种/品种 + 交互过的商品名/分类
    keywords: List[str] = []
    for pet in profile.get("pets") or []:
        for field in ("species", "breed"):
            value = (pet.get(field) or "").strip()
            if value:
                keywords.append(value)
    for it in (profile.get("cart") or []) + (profile.get("my_posts") or []):
        name = (it.get("productName") or it.get("title") or "").strip()
        if name:
            keywords.append(name[:20])
    keywords = list(dict.fromkeys(keywords))[:4]

    # 2. 关键词召回商品 + 泛化在售商品 + 热门动态
    search_tasks = [biz.search_products(kw, limit=8, user_id=user_id) for kw in keywords]
    search_tasks.append(biz.search_products("", limit=_MAX_PRODUCT_CANDIDATES, user_id=user_id))
    search_tasks.append(biz.get_hot_posts(limit=_MAX_SPACE_CANDIDATES, user_id=user_id))
    results = await asyncio.gather(*search_tasks, return_exceptions=True)

    products: List[Dict[str, Any]] = []
    for res in results[:-1]:
        if isinstance(res, list):
            products.extend(res)
    spaces = results[-1] if isinstance(results[-1], list) else []

    # 3. 去重 + 规范化候选
    candidates: List[Dict[str, Any]] = []
    seen = set()
    for p in products:
        pid = p.get("id")
        if pid is None or ("product", pid) in seen:
            continue
        seen.add(("product", pid))
        candidates.append({
            "id": pid, "type": "product",
            "title": p.get("name") or "", "image": p.get("imageUrl") or "",
            "price": p.get("price"), "category": p.get("categoryName") or "",
            "shopName": p.get("shopName") or "",
        })
    for s in spaces:
        sid = s.get("id")
        if sid is None or ("space", sid) in seen:
            continue
        seen.add(("space", sid))
        candidates.append({
            "id": sid, "type": "space",
            "title": s.get("title") or (s.get("content") or "")[:30],
            "image": "", "likeCount": s.get("likeCount") or 0,
            "category": s.get("category") or "",
        })
    return {"keywords": keywords, "candidates": candidates[:_MAX_PRODUCT_CANDIDATES + _MAX_SPACE_CANDIDATES]}


async def rerank(state: RecommendState) -> Dict[str, Any]:
    """LLM 重排 + 推荐理由；mock / 失败时启发式排序"""
    candidates = state.get("candidates") or []
    if not candidates:
        return {"items": [], "summary": "暂时没有可推荐的内容，先去逛逛圈子和商城吧～"}

    if settings.is_mock:
        return _heuristic_rank(state)

    profile = state.get("profile") or {}
    profile_text = _profile_digest(profile)
    cand_text = "\n".join(
        f"- [{c['type']}] id={c['id']} 标题：{c['title']} 分类：{c.get('category','')}"
        for c in candidates)
    system_prompt = (
        "你是 PetVerse 宠物社区的推荐助手。请根据用户画像，从候选内容中挑选最相关的"
        f"至多 {_TOP_N} 条推荐给用户，并为每条给出简短推荐理由（结合用户宠物或过往行为，不超过 30 字）。"
        "只能使用候选列表中出现过的 id，不要编造。"
    )
    user_prompt = f"用户画像：\n{profile_text}\n\n候选内容：\n{cand_text}"
    try:
        result: RecommendResult = await astructured_invoke(RecommendResult, system_prompt, user_prompt)
        items = _merge_items(result.items, candidates)
        if not items:
            return _heuristic_rank(state)
        return {"items": items, "summary": result.summary or ""}
    except Exception:
        logger.warning("推荐重排失败，降级为启发式排序", exc_info=True)
        return _heuristic_rank(state)


async def persist_cache(state: RecommendState) -> Dict[str, Any]:
    """写入推荐缓存（按用户 + 场景）"""
    user_id = state.get("user_id", 0)
    scene = state.get("scene", "home")
    if state.get("items"):
        await memory.cache_set(
            f"ai:recommend:{scene}:{user_id}",
            {"items": state.get("items") or [], "summary": state.get("summary") or ""},
            settings.RECOMMEND_CACHE_TTL)
    return {}


def _profile_digest(profile: Dict[str, Any]) -> str:
    """把画像压缩为 LLM 可读的简短文本"""
    lines = []
    pets = profile.get("pets") or []
    if pets:
        parts = []
        for p in pets[:5]:
            desc = f"{p.get('name') or ''}({p.get('species') or ''}{('·' + p['breed']) if p.get('breed') else ''},{p.get('age') or '?'}岁)"
            parts.append(desc)
        lines.append("宠物：" + "；".join(parts))
    cart = profile.get("cart") or []
    if cart:
        lines.append("购物车：" + "、".join((c.get("productName") or "")[:20] for c in cart[:5] if c.get("productName")))
    orders = profile.get("orders") or []
    names = []
    for o in orders[:5]:
        for it in (o.get("items") or []):
            if it.get("productName"):
                names.append(it["productName"][:15])
    if names:
        lines.append("近期购买：" + "、".join(names[:6]))
    reviews = profile.get("reviews") or []
    if reviews:
        lines.append("评价过的商品：" + "、".join((r.get("productName") or "")[:20] for r in reviews[:5] if r.get("productName")))
    posts = profile.get("my_posts") or []
    if posts:
        lines.append("发布动态：" + "、".join((p.get("title") or "")[:15] for p in posts[:3] if p.get("title")))
    return "\n".join(lines) or "（暂无足够画像数据，请推荐热门内容）"


def _merge_items(model_items, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把 LLM 输出与候选详情合并（过滤不存在的 id），并保序"""
    index = {(c["type"], int(c["id"])): c for c in candidates if str(c.get("id", "")).isdigit() or isinstance(c.get("id"), int)}
    merged = []
    for mi in model_items:
        key = (mi.type, int(mi.id))
        cand = index.get(key)
        if cand is None:
            continue
        item = dict(cand)
        item["reason"] = mi.reason
        item["score"] = float(mi.score)
        merged.append(item)
    return merged[:_TOP_N]


def _heuristic_rank(state: RecommendState) -> Dict[str, Any]:
    """启发式排序（mock / 降级）：商品优先热销，动态按点赞数"""
    candidates = state.get("candidates") or []
    products = [c for c in candidates if c["type"] == "product"]
    spaces = [c for c in candidates if c["type"] == "space"]
    spaces.sort(key=lambda x: x.get("likeCount", 0), reverse=True)

    items = []
    for c in products[:max(4, _TOP_N - len(spaces))]:
        item = dict(c)
        item["reason"] = "在售好物，值得看看"
        item["score"] = 0.6
        items.append(item)
    for c in spaces[:max(0, _TOP_N - len(items))]:
        item = dict(c)
        item["reason"] = "社区热门动态"
        item["score"] = 0.5
        items.append(item)
    return {"items": items[:_TOP_N], "summary": "根据社区热门与在售好物为你推荐"}


_graph = None


def get_recommend_graph():
    """构建并缓存推荐图"""
    global _graph
    if _graph is not None:
        return _graph
    builder = StateGraph(RecommendState)
    builder.add_node("gather_profile", gather_profile)
    builder.add_node("recall_candidates", recall_candidates)
    builder.add_node("rerank", rerank)
    builder.add_node("persist_cache", persist_cache)
    builder.add_edge(START, "gather_profile")
    builder.add_edge("gather_profile", "recall_candidates")
    builder.add_edge("recall_candidates", "rerank")
    builder.add_edge("rerank", "persist_cache")
    builder.add_edge("persist_cache", END)
    _graph = builder.compile()
    return _graph


async def recommend(user_id: int, scene: str = "home", use_cache: bool = True) -> Dict[str, Any]:
    """生成个性化推荐（优先读缓存），返回 {items, summary}"""
    cache_key = f"ai:recommend:{scene}:{user_id}"
    if use_cache:
        cached = await memory.cache_get(cache_key)
        if cached is not None:
            return cached
    state: RecommendState = {"user_id": user_id, "scene": scene}
    result = await get_recommend_graph().ainvoke(state)
    return {"items": result.get("items") or [], "summary": result.get("summary") or ""}
