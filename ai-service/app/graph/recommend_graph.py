"""个性化推荐工作流（recommend graph）

LangGraph 编排：
    gather_profile → recall_candidates → rerank → persist_cache
  - gather_profile：并发拉取用户画像数据（宠物 / 订单 / 购物车 / 评价 / 我的动态）；
  - recall_candidates：按画像物种召回商品与同物种动态、热门内容兜底，再做物种相关度排序；
  - rerank：真实模式用 LLM 结合画像重排并生成推荐理由；mock / 失败时按相关度排序；
  - persist_cache：按用户 + 场景写两级缓存（Redis 热 + ai_cache 温，TTL 可配）。

为什么相关度要先在 Python 侧算一遍：商品只有「用品/食品/活体」三分类、动态只有自由话题，
两侧都没有物种字段，物种信号只存在于标题与详情的自由文本里。只靠 LLM 重排兜不住——候选池
里大半是猫狗内容时模型照样会选错，所以先按物种归组打分、把别家物种专属的候选挡在召回门外，
再交给模型排序并写理由。
"""
import asyncio
import logging
from typing import Any, Dict, List, Set, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph

from app import cache
from app.clients import biz
from app.config import settings
from app.llm import astructured_invoke
from app.schemas import RecommendResult

logger = logging.getLogger(__name__)

# 召回规模
_MAX_KEYWORDS = 5               # 画像物种关键词上限（每个关键词一次商品检索）
_MAX_POST_KEYWORDS = 2          # 动态按物种检索的关键词上限
_PRODUCT_PER_KEYWORD = 6        # 单词召回的商品条数
_POST_PER_KEYWORD = 4           # 单词召回的动态条数
_PRODUCT_BACKUP_LIMIT = 8       # 商品泛化兜底条数（无画像时放宽到 _BLANK_PRODUCT_LIMIT）
_BLANK_PRODUCT_LIMIT = 12       # 无画像时泛化召回的商品条数
_HOT_POST_LIMIT = 8             # 热门动态兜底条数
_CANDIDATE_POOL = 24            # 送入重排层的候选总数
_TOP_N = 8                      # 最终返回条数
_HOME_SPACE_MAX = _TOP_N // 2   # 首页动态条数上限，避免整屏都是动态

# 养宠人群分组 -> 标题/详情里可能出现的别名词（与前端 petOptions.js 的物种表对齐，
# 并补上用户自定义填写的花枝鼠、蜜袋鼯等）。按词长倒序匹配并整词消费，
# 所以「龙猫」「花枝鼠」归小宠，不会被拆成「猫」「鼠」错判成猫用品。
_SPECIES_TOKENS: Dict[str, List[str]] = {
    "猫": ["猫", "喵"],
    "狗": ["狗", "犬"],
    "兔": ["兔"],
    "小宠": ["花枝鼠", "金丝熊", "蜜袋鼯", "龙猫", "仓鼠", "豚鼠", "刺猬", "雪貂", "小宠", "貂", "鼠"],
    "鸟": ["鹦鹉", "八哥", "文鸟", "鸟"],
    "龟": ["乌龟", "陆龟", "草龟", "龟"],
    "爬宠": ["守宫", "蜥蜴", "角蛙", "蛇", "蜘蛛", "寄居蟹"],
    "鱼": ["观赏鱼", "金鱼", "锦鲤", "斗鱼", "鱼"],
}
_TOKEN_INDEX = tuple(sorted(
    ((token, group) for group, tokens in _SPECIES_TOKENS.items() for token in tokens),
    key=lambda x: (-len(x[0]), x[0])))

# 组内常见商品检索词：只拿宠物档案里的物种名去搜会漏掉同属「小宠」的仓鼠笼、龙猫浴沙等
# 商品，按组补几个代表词进召回关键词（单词太泛，如「鼠」会命中猫玩具「猫抓老鼠」，不收）
_GROUP_SEARCH_LEADS: Dict[str, List[str]] = {
    group: [token for token in tokens if len(token) > 1]
    for group, tokens in _SPECIES_TOKENS.items()
}


class RecommendState(TypedDict, total=False):
    """推荐图状态"""

    user_id: int
    scene: str                   # home / shop
    profile: Dict[str, Any]      # 画像原始数据
    keywords: List[str]          # 画像物种关键词
    own_groups: List[str]        # 用户饲养物种所属人群分组
    own_words: List[str]         # 用户宠物物种展示词（生成理由用）
    candidates: List[Dict[str, Any]]  # 候选（商品 + 动态），已按相关度降序
    items: List[Dict[str, Any]]  # 最终推荐项
    summary: str


def _species_order(text: str) -> List[str]:
    """按出现先后返回文本涉及的养宠人群分组（最长匹配优先，命中即消费掉该词）"""
    ordered: List[str] = []
    lowered = (text or "").lower()
    pos = 0
    while pos < len(lowered):
        for token, group in _TOKEN_INDEX:
            if lowered.startswith(token, pos):
                if group not in ordered:
                    ordered.append(group)
                pos += len(token)
                break
        else:
            pos += 1
    return ordered


def _species_of(text: str) -> Set[str]:
    """文本涉及哪些养宠人群分组"""
    return set(_species_order(text))


def _build_intent(profile: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    """从画像提炼 (召回关键词, 所属人群分组, 物种展示词)

    以宠物档案为准；没登记宠物时退化为消费/互动行为（购物车、订单、评价、自己发的动态）
    里体现出的物种，避免新用户一上来就被全库新品灌满猫狗内容。
    """
    keywords: List[str] = []
    own_words: List[str] = []
    groups: Set[str] = set()

    for pet in profile.get("pets") or []:
        for field in ("species", "breed"):
            value = (pet.get(field) or "").strip()
            if not value:
                continue
            groups |= _species_of(value)
            if value not in keywords:
                keywords.append(value)
            if field == "species" and value not in own_words:
                own_words.append(value)

    if not groups:
        touched: List[str] = []
        for item in (profile.get("cart") or []) + (profile.get("reviews") or []):
            if item.get("productName"):
                touched.append(item["productName"])
        for order in profile.get("orders") or []:
            for item in order.get("items") or []:
                if item.get("productName"):
                    touched.append(item["productName"])
        for post in profile.get("my_posts") or []:
            if post.get("title"):
                touched.append(post["title"])
        groups = _species_of(" ".join(touched))

    # 分组名本身也是电商常用搜词（如「小宠」），连同组内代表词补进关键词，
    # 兜住标题里不直接写宠物物种名的商品
    for group in sorted(groups):
        for term in [group] + _GROUP_SEARCH_LEADS.get(group, []):
            if term not in keywords:
                keywords.append(term)
    return keywords[:_MAX_KEYWORDS], sorted(groups), own_words[:5]


def _is_off_species(cand: Dict[str, Any], own_groups: Set[str]) -> bool:
    """物种门槛：这条内容是别家物种专属

    两种出局情形——标题只写了别家物种（猫爬架之于养鼠用户），或标题里最先出现的物种词
    是别家物种（「猫抓老鼠玩具」是给猫玩的，不是给鼠玩的）。后者必须靠语序判，
    因为商品没有物种字段，只看"沾没沾上自家物种"会把猫玩具放进小宠用户的推荐里。
    """
    if not own_groups:
        return False
    order = _species_order(cand.get("title") or "")
    foreign = [group for group in order if group not in own_groups]
    if not foreign:
        return False
    owned = [group for group in order if group in own_groups]
    return not owned or order[0] in foreign


def _as_number(value: Any) -> int:
    """把 Java 可能序列化成字符串的数值字段安全转成 int，异常值按 0 处理"""
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _relevance(cand: Dict[str, Any], own_groups: Set[str]) -> float:
    """相关度打分：命中自家物种加分（标题命中更高于详情命中），标题写明别家物种减分，
    画像召回优于热门兜底，动态再按点赞微调。"""
    in_title = _species_of(cand.get("title") or "")
    in_text = in_title | _species_of(cand.get("_desc") or "")
    score = 0.0
    if own_groups:
        if in_title & own_groups:
            score += 3.0
        elif in_text & own_groups:
            score += 1.5
        foreign = in_title - own_groups
        if foreign:
            # 「猫抓老鼠玩具」这类同时沾了自家物种的标题不判死，只降权：
            # 商品没有物种字段可依据，硬删会把真好物一起删掉
            score -= 2.0 + len(foreign)
    if cand.get("_targeted"):
        score += 1.0
    if cand.get("type") == "space":
        score += min(0.5, (cand.get("likeCount") or 0) / 100.0)
    return score


def _item_reason(cand: Dict[str, Any], own_groups: Set[str], own_words: List[str]) -> str:
    """降级推荐理由：命中用户物种时点出具体物种，否则退回通用话术"""
    touched = _species_of(cand.get("title") or "") | _species_of(cand.get("_desc") or "")
    if own_groups and (touched & own_groups):
        word = next((w for w in own_words if _species_of(w) & touched), "") or own_words[0]
        return f"适合你家{word}的实用好物" if cand["type"] == "product" else f"养{word}的铲屎官都在看"
    return "在售好物，值得看看" if cand["type"] == "product" else "社区热门动态"


def _to_item(cand: Dict[str, Any], reason: str, score: float) -> Dict[str, Any]:
    """输出项裁剪：下划线开头的是内部标记，不下发给前端"""
    item = {k: v for k, v in cand.items() if not k.startswith("_")}
    item["reason"] = reason
    item["score"] = score
    return item


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
    """召回候选：按画像物种检索商品与动态，无画像时用热门兜底，再做物种门槛与排序"""
    user_id = state.get("user_id", 0)
    scene = state.get("scene", "home")
    profile = state.get("profile") or {}
    keywords, own_groups_list, own_words = _build_intent(profile)
    own_groups = set(own_groups_list)

    # 商品：逐物种关键词召回 + 一路泛化兜底。兜底不会被猫狗内容污染——泛化取到的是全库
    # 新品，其中别家物种专属的会在后面被物种门槛挡掉，留下的正是标题没写物种名的同物种
    # 商品与通用品；无画像（新用户）时才放大兜底额度当作热门推荐。
    product_coros: List[Any] = []
    product_targeted: List[bool] = []
    for kw in keywords:
        product_coros.append(biz.search_products(kw, limit=_PRODUCT_PER_KEYWORD, user_id=user_id))
        product_targeted.append(True)
    product_coros.append(biz.search_products("", limit=_BLANK_PRODUCT_LIMIT if not keywords
                                             else _PRODUCT_BACKUP_LIMIT, user_id=user_id))
    product_targeted.append(False)

    # 动态：同物种检索 + 一路热门兜底；商城页「猜你喜欢」只要商品，不占动态额度
    post_coros: List[Any] = []
    post_targeted: List[bool] = []
    if scene != "shop":
        for kw in keywords[:_MAX_POST_KEYWORDS]:
            post_coros.append(biz.search_posts(kw, limit=_POST_PER_KEYWORD, user_id=user_id))
            post_targeted.append(True)
        post_coros.append(biz.get_hot_posts(limit=_HOT_POST_LIMIT, user_id=user_id))
        post_targeted.append(False)

    results = await asyncio.gather(*product_coros, *post_coros, return_exceptions=True)
    product_results = results[:len(product_coros)]
    post_results = results[len(product_coros):]

    candidates: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, Any]] = set()

    def _add(kind: str, raw: dict, targeted: bool) -> None:
        rid = raw.get("id")
        if rid is None or (kind, rid) in seen:
            return
        seen.add((kind, rid))
        if kind == "product":
            cand = {
                "id": rid, "type": "product",
                "title": raw.get("name") or "", "image": raw.get("imageUrl") or "",
                "price": raw.get("price"), "category": raw.get("categoryName") or "",
                "shopName": raw.get("shopName") or "",
            }
        else:
            cand = {
                "id": rid, "type": "space",
                "title": raw.get("title") or (raw.get("content") or "")[:30],
                # Java 侧 Long 统一序列化为字符串，相关度计算要用数值，先归一化
                "image": "", "likeCount": _as_number(raw.get("likeCount")),
                "category": raw.get("category") or "",
            }
        cand["_targeted"] = targeted
        # 详情文本只参与相关度判定，不展示：商品没有物种字段，标题没写到的物种只能从详情认
        cand["_desc"] = (raw.get("description") or "") if kind == "product" else (raw.get("content") or "")
        candidates.append(cand)

    for targeted, res in zip(product_targeted, product_results):
        if isinstance(res, list):
            for raw in res:
                _add("product", raw, targeted)
    for targeted, res in zip(post_targeted, post_results):
        if isinstance(res, list):
            for raw in res:
                _add("space", raw, targeted)

    # 别家物种专属的候选直接出局，再按相关度降序截断送进重排层
    candidates = [c for c in candidates if not _is_off_species(c, own_groups)]
    for cand in candidates:
        cand["_score"] = _relevance(cand, own_groups)
    candidates.sort(key=lambda c: c["_score"], reverse=True)
    return {
        "keywords": keywords,
        "own_groups": own_groups_list,
        "own_words": own_words,
        "candidates": candidates[:_CANDIDATE_POOL],
    }


async def rerank(state: RecommendState) -> Dict[str, Any]:
    """LLM 重排 + 推荐理由；mock / 失败时按相关度启发式排序"""
    candidates = state.get("candidates") or []
    if not candidates:
        return {"items": [], "summary": "暂时没有可推荐的内容，先去逛逛圈子和商城吧～"}

    if settings.is_mock:
        return _heuristic_rank(state)

    profile = state.get("profile") or {}
    own_words = state.get("own_words") or []
    profile_text = _profile_digest(profile)
    cand_text = "\n".join(
        f"- [{c['type']}] id={c['id']} 标题：{c['title']} 分类：{c.get('category','')}"
        for c in candidates)
    scene_hint = ("当前是商城页「猜你喜欢」，候选只含商品。" if state.get("scene") == "shop"
                  else "当前是首页推荐流，商品与社区动态可以混排。")
    species_hint = (f"用户饲养的物种：{'、'.join(own_words)}。" if own_words else "")
    system_prompt = (
        "你是 PetVerse 宠物社区的推荐助手。请结合用户画像，从候选内容中挑选最相关的"
        f"至多 {_TOP_N} 条，并为每条写一句不超过 30 字的推荐理由——理由必须落到用户画像里的"
        "具体信息（宠物名字、物种、最近买过什么），不要写成放到谁身上都适用的万能话术。"
        "硬性要求：只推与用户所养物种相关的内容，其他物种的专属内容一律不选（例如只养花枝鼠"
        "的用户，不要出现猫爬架、猫砂、逗猫棒）；候选里确实没有合适内容时就少推几条，宁缺毋滥。"
        "只能使用候选列表中出现过的 id，不要编造。"
    )
    user_prompt = f"{scene_hint}{species_hint}\n\n用户画像：\n{profile_text}\n\n候选内容（已按相关度从高到低排列）：\n{cand_text}"
    try:
        result: RecommendResult = await astructured_invoke(RecommendResult, system_prompt, user_prompt)
        items = _merge_items(result.items, candidates,
                             set(state.get("own_groups") or []), state.get("own_words") or [])
        if not items:
            return _heuristic_rank(state)
        return {"items": items, "summary": result.summary or ""}
    except Exception:
        logger.warning("推荐重排失败，降级为启发式排序", exc_info=True)
        return _heuristic_rank(state)


async def persist_cache(state: RecommendState) -> Dict[str, Any]:
    """写入推荐缓存（按用户 + 场景；Redis 热 + ai_cache 温两层）"""
    user_id = state.get("user_id", 0)
    scene = state.get("scene", "home")
    if state.get("items"):
        await cache.set_cached(
            f"ai:recommend:{scene}:{user_id}",
            {"items": state.get("items") or [], "summary": state.get("summary") or ""},
            settings.RECOMMEND_CACHE_TTL)
    return {}


def _profile_digest(profile: Dict[str, Any]) -> str:
    """把画像压缩为 LLM 可读的简短文本"""
    lines = []
    pets = profile.get("pets") or []
    if pets:
        species = list(dict.fromkeys((p.get("species") or "").strip() for p in pets if p.get("species")))
        if species:
            lines.append("饲养物种：" + "、".join(species))
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


def _identity(cand_type: str, raw_id: Any) -> Tuple[str, str]:
    """推荐项去重键：Java 侧雪花 ID 序列化为字符串，LLM 回传的 id 是数字，
    统一归一化成字符串再比较，否则同一项会被当成两条重复下发"""
    return cand_type, str(raw_id)


def _merge_items(model_items, candidates: List[Dict[str, Any]], own_groups: Set[str],
                 own_words: List[str]) -> List[Dict[str, Any]]:
    """把 LLM 输出与候选详情合并（过滤不存在的 id 与越出门槛的物种），保序

    模型为「宁缺毋滥」少选时，用同样过了物种门槛、按相关度排好的剩余候选补齐，
    避免首页出现没必要的空位。
    """
    index = {_identity(c["type"], c["id"]): c for c in candidates if c.get("id") is not None}
    merged = []
    picked: Set[Tuple[str, str]] = set()
    for mi in model_items:
        key = _identity(mi.type, mi.id)
        cand = index.get(key)
        if cand is None or key in picked or _is_off_species(cand, own_groups):
            continue
        picked.add(key)
        merged.append(_to_item(cand, mi.reason, float(mi.score)))
    for cand in candidates:
        if len(merged) >= _TOP_N:
            break
        key = _identity(cand["type"], cand["id"])
        if key in picked or _is_off_species(cand, own_groups):
            continue
        picked.add(key)
        merged.append(_to_item(cand, _item_reason(cand, own_groups, own_words),
                               round(max(0.1, min(1.0, 0.5 + cand.get("_score", 0) / 6)), 2)))
    return merged[:_TOP_N]


def _heuristic_rank(state: RecommendState) -> Dict[str, Any]:
    """启发式排序（mock / 降级）：按召回阶段的相关度次序取数，理由点出命中的物种"""
    scene = state.get("scene", "home")
    own_groups = set(state.get("own_groups") or [])
    own_words = state.get("own_words") or []
    candidates = [c for c in (state.get("candidates") or []) if not _is_off_species(c, own_groups)]
    products = [c for c in candidates if c["type"] == "product"]
    spaces = [c for c in candidates if c["type"] == "space"]

    if scene == "shop":
        chosen = products[:_TOP_N]
    else:
        picked_spaces = spaces[:_HOME_SPACE_MAX]
        chosen = products[:_TOP_N - len(picked_spaces)] + picked_spaces
        if len(chosen) < _TOP_N:
            taken = {(c["type"], c["id"]) for c in chosen}
            chosen += [c for c in spaces if (c["type"], c["id"]) not in taken][:_TOP_N - len(chosen)]

    items = [_to_item(c, _item_reason(c, own_groups, own_words),
                      round(max(0.1, min(1.0, 0.5 + c.get("_score", 0) / 6)), 2))
             for c in chosen[:_TOP_N]]
    if own_words:
        summary = f"根据你家{'、'.join(own_words)}的养育需求，从社区与商城挑了这些相关内容"
    else:
        summary = "根据社区热门与在售好物为你推荐"
    return {"items": items, "summary": summary}


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
    """生成个性化推荐（优先读两级缓存），返回 {items, summary}

    未命中时进入单飞：同一用户同一场景的并发请求只放行一次真实计算
    （五路画像拉取 + 召回 + LLM 重排），其余请求等锁后直接取缓存结果。
    """
    cache_key = f"ai:recommend:{scene}:{user_id}"
    if use_cache:
        cached = await cache.get_cached(cache_key)
        if cached is not None:
            return cached
    async with cache.single_flight(cache_key):
        # 等锁期间可能已由其他请求计算并写入缓存
        if use_cache:
            cached = await cache.get_cached(cache_key)
            if cached is not None:
                return cached
        state: RecommendState = {"user_id": user_id, "scene": scene}
        result = await get_recommend_graph().ainvoke(state)
        return {"items": result.get("items") or [], "summary": result.get("summary") or ""}
