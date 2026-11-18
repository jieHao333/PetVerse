"""业务数据查询工具（function calling）。

职责：把「用户真实数据」包装成模型可自主调用的工具，让对话能回答「我的订单到哪了」
「我家猫的疫苗记录」这类需要查库的问题，而不是让模型凭空编造。

主要导出：
    - ``build_pet_tools``        宠物档案（1 个）
    - ``build_shop_tools``       订单 / 购物车 / 商品检索 / 商品口碑（4 个）
    - ``build_community_tools``  社区热门动态（1 个）
    - ``build_profile_tools``    数据中台养护画像（1 个）

外部依赖：``langchain_core.tools``、``app.clients.biz``。

从 ``app/tools.py`` 迁移而来，**工具签名与 docstring 一字未改**——docstring 就是模型的
工具描述（``parse_docstring=True`` 会解析它生成参数 schema），改写会直接改变模型
「什么时候该调这个工具」的判断。分组只是为了按业务域开关，不影响工具本身。

安全边界：所有工具都闭包捕获 ``user_id``，因此模型无法通过参数指定「查谁的」数据——
越权查询在工具签名层面就不存在。
"""

import json
import logging
from typing import Annotated, Any, List

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from app.clients import biz

logger = logging.getLogger(__name__)

# 工具返回值长度上限：超长会挤爆上下文并推高 token 成本，列表按条目裁剪而不是粗暴截断字符串，
# 保证 JSON 结构完整、模型仍能解析。
_RESULT_MAX_CHARS = 2000
_HOT_POSTS_LIMIT = 6
_PRODUCT_SEARCH_LIMIT = 8
_MY_ORDERS_LIMIT = 10
_PRODUCT_REVIEW_LIMIT = 50


def _dumps(data: object) -> str:
    """把工具结果序列化为 JSON 文本，并按上限裁剪。

    Args:
        data: 业务接口返回的数据（通常是 dict / list）。

    Returns:
        JSON 字符串；超长时优先按列表条目裁剪，无法裁剪则截断并标注「已截断」。
        序列化失败时退化为 ``str(data)``，保证工具总能返回可读文本。

    Raises:
        无。
    """
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        logger.warning("工具结果序列化失败，回退为 str(): type=%s", type(data).__name__, exc_info=True)
        text = str(data)
    if len(text) <= _RESULT_MAX_CHARS:
        return text
    if isinstance(data, list) and data:
        # 逐条累加试探，直到再加一条就超限；保留完整条目比截断字符更利于模型理解。
        kept: List[Any] = []
        for item in data:
            if len(json.dumps(kept + [item], ensure_ascii=False, default=str)) > _RESULT_MAX_CHARS:
                break
            kept.append(item)
        if kept:
            logger.debug("工具结果超长，按条目裁剪: %s -> %s 条", len(data), len(kept))
            return json.dumps(kept, ensure_ascii=False, default=str) + f"\n(共 {len(data)} 条，仅返回前 {len(kept)} 条)"
    return text[:_RESULT_MAX_CHARS] + "…(已截断)"


# 商品对普通用户的可见/可购买口径。下游 /shop/product/page 已过滤「上架 + 店铺营业」，
# 但**不保证有库存**，也会回传 merchantId、店铺撤销状态等内部字段。工具层再收一次口：
# 只把「用户真能加购」的商品和「用户真会看到」的字段交给模型。
_PRODUCT_FIELDS = ("id", "name", "price", "stock", "shopName", "categoryName", "imageUrl")
_PRODUCT_DESC_CHARS = 80
_POST_FIELDS = ("id", "title", "category", "authorNickname", "likeCount", "createTime")
_POST_CONTENT_CHARS = 200


def _purchasable(item: dict) -> bool:
    """判断商品对当前用户是否「可见且可购买」。

    Args:
        item: 商品记录（``ProductVO`` 的字典形式）。

    Returns:
        True 表示上架、有库存、店铺正常营业。

    Raises:
        无。

    ``status`` 与 ``stock`` 是购买的硬前提，缺失或非法即视为不可购买；
    店铺字段缺失时不做拦截，避免误杀老接口数据。
    """
    try:
        if int(item.get("status")) != 1:      # 0-下架 1-上架
            return False
        if int(item.get("stock") or 0) <= 0:  # 无库存无法加购，不该出现在推荐里
            return False
    except (TypeError, ValueError):
        return False
    if item.get("shopSelling") is False:
        return False
    shop_status = item.get("shopStatus")
    if shop_status is not None and int(shop_status) != 1:   # 0-打烊 2-封禁
        return False
    if item.get("shopWithdrawStatus") not in (None, 0):      # 1-撤销冷静期
        return False
    return True


def _project_product(item: dict) -> dict:
    """裁掉商品里用户不该看到 / 模型用不上的字段。

    Args:
        item: ``ProductVO`` 字典。

    Returns:
        只保留购买决策所需字段的新字典（描述截断到 80 字）。

    Raises:
        无。
    """
    projected = {key: item.get(key) for key in _PRODUCT_FIELDS if item.get(key) is not None}
    desc = str(item.get("description") or "").strip()
    if desc:
        projected["description"] = desc[:_PRODUCT_DESC_CHARS]
    return projected


def _project_post(item: dict) -> dict:
    """裁剪动态字段：去掉 userId / visibility / 媒体列表等内部或非必要信息。

    Args:
        item: ``SpaceVO`` 字典。

    Returns:
        只保留浏览与互动所需字段的新字典（正文截断到 200 字）。

    Raises:
        无。
    """
    projected = {key: item.get(key) for key in _POST_FIELDS if item.get(key) is not None}
    content = str(item.get("content") or "").strip()
    if content:
        projected["content"] = content[:_POST_CONTENT_CHARS]
    return projected


def build_pet_tools(user_id: int) -> List[BaseTool]:
    """构造宠物档案类工具。

    Args:
        user_id: 当前登录用户 ID（闭包捕获，模型不可更改）。

    Returns:
        工具列表。

    Raises:
        无。
    """

    @tool(parse_docstring=True)
    async def get_my_pets() -> str:
        """查询当前登录用户自己名下的宠物档案：名字、物种、品种、年龄、性别、体重、体况评分、疫苗、驱虫、绝育、病史。

        用户提到「我的宠物」「我家猫 / 狗」，想了解其档案或健康状况时调用；返回空数组表示该用户还没有登记宠物。
        """
        return _dumps(await biz.get_my_pets(user_id))

    return [get_my_pets]


def build_shop_tools(user_id: int) -> List[BaseTool]:
    """构造商城类工具（订单 / 购物车 / 商品检索 / 商品口碑）。

    Args:
        user_id: 当前登录用户 ID（闭包捕获，模型不可更改）。

    Returns:
        工具列表，顺序即提示词中的出现顺序。

    Raises:
        无。
    """

    @tool(parse_docstring=True)
    async def get_my_orders() -> str:
        """查询当前登录用户自己的商城订单：订单号、店铺、商品明细、实付金额、订单状态、自提码、下单时间，最多 10 条。

        用户询问「我的订单」「我买的东西到哪了」「怎么自提」时调用；返回空数组表示没有订单。
        """
        return _dumps(await biz.get_my_orders(user_id, limit=_MY_ORDERS_LIMIT))

    @tool(parse_docstring=True)
    async def get_my_cart() -> str:
        """查询当前登录用户自己的购物车：商品名、单价、数量、是否失效（含失效原因）。

        用户询问「购物车里有什么」「这件商品我加购了吗」时调用；返回空数组表示购物车为空。
        """
        return _dumps(await biz.get_my_cart(user_id))

    @tool(parse_docstring=True)
    async def get_product_reviews(product_id: Annotated[int, Field(gt=0)]) -> str:
        """查询指定商品的用户评价：评分（1-5 分）、评价内容、评价人昵称，最多 50 条，用于回答商品口碑与优缺点；返回空数组表示该商品暂无评价。

        Args:
            product_id: 商城中商品的数字 ID（正整数），取自 search_products 返回的 id 字段，不要自行编造。
        """
        return _dumps(await biz.get_product_reviews(product_id, limit=_PRODUCT_REVIEW_LIMIT))

    @tool(parse_docstring=True)
    async def search_products(keyword: str = "") -> str:
        """按关键词搜索**当前可购买**的商品：商品名、价格、库存、店铺、分类，最多 8 条。

        只返回上架、有库存、店铺正常营业的商品。关键词太窄（如「肠胃敏感猫粮」）可能查不到，
        返回空数组时请换更宽泛的词（如「猫粮」）再搜一次，不要用一次空结果断定商城没有相关商品。
        用户想找某类商品（如猫粮、猫砂、牵引绳）或问「商城有什么」时调用；
        返回的 id 可直接用于加购（add_to_cart）与查评价（get_product_reviews）。

        Args:
            keyword: 搜索关键词，如「猫粮」「猫砂」；留空表示按上架时间返回可购买商品。
        """
        items = await biz.search_products(keyword, limit=_PRODUCT_SEARCH_LIMIT, user_id=user_id)
        # 下游只保证「上架 + 店铺营业」，库存与字段可见性在这里收口。
        available = [item for item in items if _purchasable(item)]
        if len(available) != len(items):
            logger.info("商城检索过滤不可售商品: %s -> %s", len(items), len(available))
        return _dumps([_project_product(item) for item in available])

    return [get_my_orders, get_my_cart, search_products, get_product_reviews]


def build_community_tools(user_id: int) -> List[BaseTool]:
    """构造社区类工具。

    Args:
        user_id: 当前登录用户 ID（用于社区侧的可见性判定）。

    Returns:
        工具列表。

    Raises:
        无。
    """

    @tool(parse_docstring=True)
    async def get_hot_posts() -> str:
        """查询社区当前的热门动态：标题、正文、分类、作者昵称、点赞数，最多 6 条。

        用户询问「社区最近在聊什么」「有什么热门话题」时调用；属于社区公开内容，与用户个人数据无关。
        """
        posts = await biz.get_hot_posts(limit=_HOT_POSTS_LIMIT, user_id=user_id)
        # 只给浏览与互动需要的字段：userId / visibility / 媒体列表不进入模型上下文。
        return _dumps([_project_post(item) for item in posts])

    return [get_hot_posts]


def build_profile_tools(user_id: int) -> List[BaseTool]:
    """构造养护画像类工具（数据中台）。

    Args:
        user_id: 当前登录用户 ID。

    Returns:
        工具列表。

    Raises:
        无。
    """

    @tool(parse_docstring=True)
    async def get_my_profile() -> str:
        """查询当前登录用户的养护画像与偏好：饲养物种、物种亲和度、活跃度、消费力、兴趣标签、推荐点击率。

        用户询问「我的偏好」「我适合养什么」「结合我的情况给建议」时调用；
        数据来自数据中台沉淀的行为画像，返回空对象表示暂无足够行为数据。
        """
        return _dumps(await biz.get_data_profile(user_id) or {})

    return [get_my_profile]
