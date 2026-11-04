"""个性化推荐编排图。

职责：结合用户画像（宠物、订单、购物车、评价、动态）从商城商品与社区动态中挑选并排序，
产出「带推荐理由」的推荐流。排序优先用 LLM 重排，失败或 mock 模式下退回启发式排序。

主要导出：
    - ``RecommendState``      图状态定义
    - ``recommend``           对外入口（带缓存与单飞）
    - ``get_recommend_graph`` 取得（并编译一次）图实例
    - 各节点与打分函数（``gather_profile`` / ``recall_candidates`` / ``rerank`` /
      ``persist_cache`` 等）

外部依赖：``app.clients.biz``（业务数据）、``app.llm``、``app.cache``、``app.schemas``。

使用示例：
    from app.graph.recommend_graph import recommend

    feed = await recommend(user_id=42, scene="home")          # 走缓存
    feed = await recommend(user_id=42, scene="shop", use_cache=False)   # 强制重算

核心约束：**不推荐与用户饲养物种无关的内容**（只养花枝鼠的用户不该看到猫爬架）。
这条约束在候选过滤（``_is_off_species``）、提示词、以及合并阶段三处同时施加，
因为单靠提示词无法保证模型遵守。
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

# 缓存版本号：调整召回/排序逻辑后手动 +1，可让旧结构缓存立即失效（避免上线后仍吐旧数据）。
_CACHE_VERSION = "v3"

# ── 召回规模参数 ──────────────────────────────────────────────────────────
# 关键词数量直接决定并发请求数：商品每词 6 条、动态每词 4 条，5 个词 ≈ 50 条候选。
_MAX_KEYWORDS = 5
_MAX_POST_KEYWORDS = 2
_PRODUCT_PER_KEYWORD = 6
_POST_PER_KEYWORD = 4
# 无关键词时的兜底召回量（比有关键词时多，因为缺少定向性）。
_PRODUCT_BACKUP_LIMIT = 8
_BLANK_PRODUCT_LIMIT = 12
_HOT_POST_LIMIT = 8
# 送入 LLM 重排的候选上限：再多会超出上下文且显著增加成本。
_CANDIDATE_POOL = 24
_TOP_N = 8
# 首页最多几条社区动态：商品是主诉求，动态太多会让推荐流看起来像信息流。
_HOME_SPACE_MAX = _TOP_N // 2

# 物种同义词表：用于从商品名/标题里识别物种，并判断候选是否与用户饲养的物种相关。
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
# 按词长倒序排列，配合 _species_order 的「最长优先」扫描：
# 「花枝鼠」必须先于「鼠」命中，否则会被归到错误的物种组。
_TOKEN_INDEX = tuple(sorted(
    ((token, group) for group, tokens in _SPECIES_TOKENS.items() for token in tokens),
    key=lambda x: (-len(x[0]), x[0])))

# 用于发起搜索的词：只取多字词。「猫」「狗」这类单字搜出来的结果噪声太大，
# 而「花枝鼠」「金丝熊」足够精确。
_GROUP_SEARCH_LEADS: Dict[str, List[str]] = {
    group: [token for token in tokens if len(token) > 1]
    for group, tokens in _SPECIES_TOKENS.items()
}


class RecommendState(TypedDict, total=False):
    """推荐图状态。``total=False`` 让各节点只返回自己产出的字段。"""

    user_id: int
    scene: str
    profile: Dict[str, Any]
    keywords: List[str]
    own_groups: List[str]
    own_words: List[str]
    candidates: List[Dict[str, Any]]
    items: List[Dict[str, Any]]
    summary: str


def _species_order(text: str) -> List[str]:
    """按出现顺序抽取文本中的物种组（去重）。

    逐字符扫描 + 最长匹配，而不是简单 ``in`` 判断：一句话里可能同时出现「猫」和「猫砂」，
    也可能出现「花枝鼠」这种必须整体匹配的词；顺序信息在 ``_is_off_species`` 里用于
    判断「标题首要说的是哪个物种」。

    Args:
        text: 待分析文本（商品名 / 标题 / 描述）。

    Returns:
        物种组列表，按首次出现位置排序。

    Raises:
        无。
    """
    ordered: List[str] = []
    lowered = (text or "").lower()
    pos = 0
    while pos < len(lowered):
        for token, group in _TOKEN_INDEX:
            if lowered.startswith(token, pos):
                if group not in ordered:
                    ordered.append(group)
                # 命中后整词跳过，避免「花枝鼠」被继续拆出「鼠」而重复计入。
                pos += len(token)
                break
        else:
            pos += 1
    return ordered


def _species_of(text: str) -> Set[str]:
    """抽取文本涉及的物种组集合。

    Args:
        text: 待分析文本。

    Returns:
        物种组集合；无命中时为空集。

    Raises:
        无。
    """
    return set(_species_order(text))


def _build_intent(profile: Dict[str, Any]) -> Tuple[List[str], List[str], List[str]]:
    """从用户画像推导召回关键词与「自家物种」。

    主路径看宠物档案的 species / breed；没有宠物档案时退而求其次，
    从购物车、评价、订单、自己发的动态里推断用户在意的物种（新用户常见情况）。

    Args:
        profile: ``gather_profile`` 产出的画像。

    Returns:
        ``(keywords, own_groups, own_words)``：
            - keywords：用于搜索的关键词（含物种组及其同义词），已截断；
            - own_groups：用户饲养的物种组（用于过滤）；
            - own_words：用户自己填写的物种原词（用于生成推荐理由的措辞）。

    Raises:
        无。
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
            # 只用 species 生成措辞：breed（如「英短」）不适合说成「养英短的铲屎官」。
            if field == "species" and value not in own_words:
                own_words.append(value)

    if not groups:
        # 无宠物档案：从行为数据里拼一段文本推断物种。只需判断「买了/看了什么」，
        # 因此把商品名与动态标题拼起来统一做一次抽取即可。
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

    for group in sorted(groups):
        # 排序后拼接：保证同一画像每次生成的词序一致，缓存与结果才可复现。
        for term in [group] + _GROUP_SEARCH_LEADS.get(group, []):
            if term not in keywords:
                keywords.append(term)
    return keywords[:_MAX_KEYWORDS], sorted(groups), own_words[:5]


def _is_off_species(cand: Dict[str, Any], own_groups: Set[str]) -> bool:
    """判断候选是否为「别的物种的专属内容」。

    判定规则：标题里出现了用户不养的物种，且要么完全没有用户养的物种，要么该外来物种
    出现在标题最前面（说明它是主推对象）。这样「猫狗通用」的标题不会被误杀，
    而「猫爬架」对养鼠用户会被正确排除。

    Args:
        cand: 候选（需含 ``title``）。
        own_groups: 用户饲养的物种组；为空时不做过滤（新用户没有依据）。

    Returns:
        True 表示应当排除。

    Raises:
        无。
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
    """把接口返回的「可能是字符串的数字」安全转成整数。

    Args:
        value: 任意值（后端有时把数值序列化成字符串）。

    Returns:
        整数值；无法转换时返回 0。

    Raises:
        无。
    """
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _first_image_url(media_list: Any) -> str:
    """取动态的第一张图片地址。

    Args:
        media_list: 动态的 ``mediaList`` 字段。

    Returns:
        图片 URL；没有图片时返回空串。

    Raises:
        无。
    """
    if not isinstance(media_list, list):
        return ""
    for media in media_list:
        if not isinstance(media, dict):
            continue
        media_type = media.get("mediaType")
        # mediaType 约定 0 = 图片；其它类型（视频等）跳过，避免把视频封面当图片用。
        if media_type is not None and _as_number(media_type) != 0:
            continue
        url = media.get("url")
        if url:
            return str(url)
    return ""


def _relevance(cand: Dict[str, Any], own_groups: Set[str]) -> float:
    """启发式相关度打分（用于候选排序与 LLM 失败时的兜底）。

    分数语义（非概率）：出现在标题中的自家物种 +3，仅出现在描述中 +1.5，
    标题出现外来物种 -2 且每多一个再 -1；定向召回 +1；动态按点赞量最多 +0.5。

    Args:
        cand: 候选（含 ``title`` / ``_desc`` / ``_targeted`` / ``type`` / ``likeCount``）。
        own_groups: 用户饲养的物种组。

    Returns:
        相关度分数（可为负）。

    Raises:
        无。
    """
    in_title = _species_of(cand.get("title") or "")
    in_text = in_title | _species_of(cand.get("_desc") or "")
    score = 0.0
    if own_groups:
        # 标题命中权重远高于描述：标题是内容的主旨，描述里往往只是顺带提及。
        if in_title & own_groups:
            score += 3.0
        elif in_text & own_groups:
            score += 1.5
        foreign = in_title - own_groups
        if foreign:
            score -= 2.0 + len(foreign)
    # 定向召回（按用户关键词搜到的）比兜底召回更可能有相关性。
    if cand.get("_targeted"):
        score += 1.0
    if cand.get("type") == "space":
        # 动态额外给一点人气加成，上限 0.5，避免热门内容压过物种相关性。
        score += min(0.5, (cand.get("likeCount") or 0) / 100.0)
    return score


def _item_reason(cand: Dict[str, Any], own_groups: Set[str], own_words: List[str]) -> str:
    """生成规则版推荐理由（LLM 不可用或补充候选时使用）。

    Args:
        cand: 候选。
        own_groups: 用户饲养的物种组。
        own_words: 用户填写的物种原词（用于措辞，比物种组更贴近用户表述）。

    Returns:
        不超过一句话的推荐理由。

    Raises:
        无。
    """
    touched = _species_of(cand.get("title") or "") | _species_of(cand.get("_desc") or "")
    if own_groups and (touched & own_groups):
        # 优先挑一个「其物种组与候选有交集」的原词，避免说成「养猫的用户」而用户填的是「英短」。
        word = next((w for w in own_words if _species_of(w) & touched), "") or own_words[0]
        return f"适合你家{word}的实用好物" if cand["type"] == "product" else f"养{word}的铲屎官都在看"
    return "在售好物，值得看看" if cand["type"] == "product" else "社区热门动态"


def _to_item(cand: Dict[str, Any], reason: str, score: float) -> Dict[str, Any]:
    """把内部候选转成对外结构。

    Args:
        cand: 内部候选（含 ``_`` 前缀的内部字段）。
        reason: 推荐理由。
        score: 对外相关度（0~1）。

    Returns:
        去掉内部字段、补上 reason / score 的字典。

    Raises:
        无。
    """
    # 剔除 `_targeted` / `_desc` / `_score` 等内部字段：它们只服务于排序，
    # 暴露出去既增大响应体，也会让人误以为是接口契约的一部分。
    item = {k: v for k, v in cand.items() if not k.startswith("_")}
    item["reason"] = reason
    item["score"] = score
    return item


async def gather_profile(state: RecommendState) -> Dict[str, Any]:
    """画像聚合节点：并行拉取用户的宠物、订单、购物车、评价、动态。

    Args:
        state: 图状态（读 ``user_id``）。

    Returns:
        ``{"profile": {...}}``：五项数据均为列表（失败项退化为空列表）。

    Raises:
        无——``return_exceptions=True`` 保证单项失败不影响整体。
    """
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
        """把异常对象 / 非列表值统一成空列表。"""
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
    """候选召回节点：按关键词并发搜索商品 / 动态，过滤后按相关度截断。

    Args:
        state: 图状态（读 ``user_id`` / ``scene`` / ``profile``）。

    Returns:
        ``{"keywords", "own_groups", "own_words", "candidates"}``，
        ``candidates`` 已按相关度降序且不超过 ``_CANDIDATE_POOL`` 条。

    Raises:
        无——单次搜索异常由 ``return_exceptions`` 隔离。
    """
    user_id = state.get("user_id", 0)
    scene = state.get("scene", "home")
    profile = state.get("profile") or {}
    keywords, own_groups_list, own_words = _build_intent(profile)
    own_groups = set(own_groups_list)

    # 每个关键词一个请求，另加一个「兜底请求」（不带头关键词）保证候选池不会因为
    # 关键词过窄而空掉。
    product_coros: List[Any] = []
    product_targeted: List[bool] = []
    for kw in keywords:
        product_coros.append(biz.search_products(kw, limit=_PRODUCT_PER_KEYWORD, user_id=user_id))
        product_targeted.append(True)
    product_coros.append(biz.search_products("", limit=_BLANK_PRODUCT_LIMIT if not keywords
                                             else _PRODUCT_BACKUP_LIMIT, user_id=user_id))
    product_targeted.append(False)

    post_coros: List[Any] = []
    post_targeted: List[bool] = []
    # 商城场景只需要商品，省掉动态请求（少一半外部调用）。
    if scene != "shop":
        for kw in keywords[:_MAX_POST_KEYWORDS]:
            post_coros.append(biz.search_posts(kw, limit=_POST_PER_KEYWORD, user_id=user_id))
            post_targeted.append(True)
        post_coros.append(biz.get_hot_posts(limit=_HOT_POST_LIMIT, user_id=user_id))
        post_targeted.append(False)

    # 商品与动态一起并发；返回顺序与传入顺序一致，据此切分结果。
    results = await asyncio.gather(*product_coros, *post_coros, return_exceptions=True)
    product_results = results[:len(product_coros)]
    post_results = results[len(product_coros):]

    candidates: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, Any]] = set()

    def _add(kind: str, raw: dict, targeted: bool) -> None:
        """去重后把原始数据裁成统一候选结构。"""
        rid = raw.get("id")
        # 同一商品会被多个关键词搜到，用 (类型, id) 去重；id 缺失的直接丢弃（无法引用）。
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
                # 动态可能没有标题，退化为正文前 30 字。
                "title": raw.get("title") or (raw.get("content") or "")[:30],
                "image": _first_image_url(raw.get("mediaList")),
                "likeCount": _as_number(raw.get("likeCount")),
                "category": raw.get("category") or "",
            }
        # 内部字段：参与打分但不对外输出（见 _to_item）。
        cand["_targeted"] = targeted
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

    # 先做硬过滤再打分：被排除的候选不必参与排序，也避免它们占满候选池名额。
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
    """重排节点：LLM 精选并写推荐理由，失败则启发式排序。

    Args:
        state: 图状态。

    Returns:
        ``{"items": [...], "summary": str}``。

    Raises:
        无——模型异常已内部降级。
    """
    candidates = state.get("candidates") or []
    if not candidates:
        return {"items": [], "summary": "暂时没有可推荐的内容，先去逛逛圈子和商城吧～"}

    if settings.is_mock:
        return _heuristic_rank(state)

    profile = state.get("profile") or {}
    own_words = state.get("own_words") or []
    profile_text = _profile_digest(profile)
    # 只给模型 id / 标题 / 分类：给全量字段既浪费上下文，也容易诱导模型复述商品详情。
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


def _cache_key(user_id: int, scene: str) -> str:
    """拼缓存键。

    Args:
        user_id: 用户 ID。
        scene: 场景（home / shop）。

    Returns:
        形如 ``ai:recommend:v3:home:42`` 的键。

    Raises:
        无。
    """
    return f"ai:recommend:{_CACHE_VERSION}:{scene}:{user_id}"


async def persist_cache(state: RecommendState) -> Dict[str, Any]:
    """缓存落盘节点：只缓存非空结果。

    空结果不缓存是刻意的：业务数据短暂故障导致的空推荐若被缓存 5 分钟，
    用户刷新也看不到内容，体感上就是「推荐坏了」。

    Args:
        state: 图状态。

    Returns:
        空字典（该节点不修改状态）。

    Raises:
        无。
    """
    user_id = state.get("user_id", 0)
    scene = state.get("scene", "home")
    if state.get("items"):
        await cache.set_hot(
            _cache_key(user_id, scene),
            {"items": state.get("items") or [], "summary": state.get("summary") or ""},
            settings.RECOMMEND_CACHE_TTL)
    return {}


def _profile_digest(profile: Dict[str, Any]) -> str:
    """把画像压缩成给模型看的简短摘要。

    Args:
        profile: 画像字典。

    Returns:
        多行摘要文本；数据不足时返回一句兜底说明（提示模型只能按热门推荐）。

    Raises:
        无。
    """
    lines = []
    pets = profile.get("pets") or []
    if pets:
        # 物种去重后单独一行：这是判断「该不该推某类内容」的核心依据。
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
    # 每段都做了条数截断与字数裁剪：画像要的是「信号」，不是完整数据。
    return "\n".join(lines) or "（暂无足够画像数据，请推荐热门内容）"


def _identity(cand_type: str, raw_id: Any) -> Tuple[str, str]:
    """生成候选的唯一标识。

    统一转字符串是必要的：模型返回的 id 可能被写成 ``"42"``，而候选里是 ``42``，
    不归一化会导致匹配失败、模型挑中的内容被整体丢弃。

    Args:
        cand_type: ``product`` / ``space``。
        raw_id: 原始 id。

    Returns:
        ``(类型, id 字符串)``。

    Raises:
        无。
    """
    return cand_type, str(raw_id)


def _merge_items(model_items, candidates: List[Dict[str, Any]], own_groups: Set[str],
                 own_words: List[str]) -> List[Dict[str, Any]]:
    """把模型选择与候选池合并成最终列表。

    两段式：先采纳模型的选择（校验 id 存在、不重复、物种合规），再用启发式顺序补足到
    ``_TOP_N``——模型经常只给出 3~5 条，补齐能保证首页不会显得空。

    Args:
        model_items: 模型输出的 ``RecommendItem`` 列表。
        candidates: 候选池（已按相关度降序）。
        own_groups: 用户饲养的物种组。
        own_words: 用户填写的物种原词。

    Returns:
        最多 ``_TOP_N`` 条的对外结构列表。

    Raises:
        无。
    """
    index = {_identity(c["type"], c["id"]): c for c in candidates if c.get("id") is not None}
    merged = []
    picked: Set[Tuple[str, str]] = set()
    for mi in model_items:
        key = _identity(mi.type, mi.id)
        cand = index.get(key)
        # 三种情况一律丢弃：id 不存在（模型编造）、重复、物种不合规（模型没遵守硬约束）。
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
        # 用启发式分数换算成 0.1~1.0 的对外 score（除以 6 是把 ±分数压到合理区间）。
        merged.append(_to_item(cand, _item_reason(cand, own_groups, own_words),
                               round(max(0.1, min(1.0, 0.5 + cand.get("_score", 0) / 6)), 2)))
    return merged[:_TOP_N]


def _heuristic_rank(state: RecommendState) -> Dict[str, Any]:
    """纯规则排序（mock 模式与模型失败时的兜底）。

    Args:
        state: 图状态。

    Returns:
        ``{"items": [...], "summary": str}``。

    Raises:
        无。
    """
    scene = state.get("scene", "home")
    own_groups = set(state.get("own_groups") or [])
    own_words = state.get("own_words") or []
    candidates = [c for c in (state.get("candidates") or []) if not _is_off_species(c, own_groups)]
    products = [c for c in candidates if c["type"] == "product"]
    spaces = [c for c in candidates if c["type"] == "space"]

    if scene == "shop":
        chosen = products[:_TOP_N]
    else:
        # 首页先给动态留固定名额，再补商品；动态太少时用剩余商品填满。
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
    """取得（首次调用时编译）推荐图。

    Returns:
        已编译的图（无 checkpointer / store：推荐是单轮无状态流程）。

    Raises:
        无。
    """
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
    """生成个性化推荐流。

    Args:
        user_id: 用户 ID。
        scene: 场景，``home`` 或 ``shop``。
        use_cache: 是否使用缓存；False 表示强制重算（但仍会回填缓存）。

    Returns:
        ``{"items": [...], "summary": str}``。

    Raises:
        Exception: 图执行异常向上抛，由路由层转成统一错误报文。
    """
    cache_key = _cache_key(user_id, scene)
    if use_cache:
        cached = await cache.get_hot(cache_key)
        if cached is not None:
            return cached
    # 单飞：并发未命中时只让一个请求真正回源，其余等锁后直接读缓存，
    # 避免同一用户连点刷新把下游业务服务和 LLM 打满。
    async with cache.single_flight(cache_key):
        if use_cache:
            # 拿到锁后必须复查：等锁期间前一个请求可能已经把结果写进缓存。
            cached = await cache.get_hot(cache_key)
            if cached is not None:
                return cached
        state: RecommendState = {"user_id": user_id, "scene": scene}
        result = await get_recommend_graph().ainvoke(state)
        return {"items": result.get("items") or [], "summary": result.get("summary") or ""}
