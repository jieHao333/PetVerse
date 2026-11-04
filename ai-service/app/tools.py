"""LLM 可调用的业务查询工具（function calling）。

职责：把「用户真实数据」包装成模型可自主调用的工具，让对话能回答「我的订单到哪了」
「我家猫的疫苗记录」这类需要查库的问题，而不是让模型凭空编造。

主要导出：
    - ``build_tools``  按用户 ID 构造工具列表（未登录用户返回空列表）

外部依赖：``langchain_core.tools``、``app.clients.biz``。

使用示例：
    from app.tools import build_tools

    tools = build_tools(user_id=42)
    agent = create_agent(get_client(), tools, system_prompt=...)

重要：这些工具的 **docstring 就是工具描述**（``parse_docstring=True`` 会解析它生成
参数 schema）。改写 docstring 会直接改变模型「什么时候该调这个工具」的判断，
因此文案按「能力 + 适用场景 + 空结果含义」三段式书写，不要压缩成一行。
"""

import json
import logging
from typing import Annotated, List

from langchain_core.tools import StructuredTool, tool
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
        kept: List[object] = []
        for item in data:
            if len(json.dumps(kept + [item], ensure_ascii=False, default=str)) > _RESULT_MAX_CHARS:
                break
            kept.append(item)
        if kept:
            logger.debug("工具结果超长，按条目裁剪: %s -> %s 条", len(data), len(kept))
            return json.dumps(kept, ensure_ascii=False, default=str) + f"\n(共 {len(data)} 条，仅返回前 {len(kept)} 条)"
    return text[:_RESULT_MAX_CHARS] + "…(已截断)"


def build_tools(user_id: int) -> List[StructuredTool]:
    """按用户构造工具集。

    所有工具都闭包捕获 ``user_id``，因此模型无法通过参数指定「查谁的」数据——
    这是数据隔离的关键：越权查询在工具签名层面就不存在。

    Args:
        user_id: 当前登录用户 ID。

    Returns:
        工具列表；``user_id`` 非法（None / <= 0）时返回空列表，调用方应跳过工具节点。

    Raises:
        无。
    """
    if user_id is None or user_id <= 0:
        return []

    @tool(parse_docstring=True)
    async def get_my_pets() -> str:
        """查询当前登录用户自己名下的宠物档案：名字、物种、品种、年龄、性别、体重、体况评分、疫苗、驱虫、绝育、病史。

        用户提到「我的宠物」「我家猫 / 狗」，想了解其档案或健康状况时调用；返回空数组表示该用户还没有登记宠物。
        """
        return _dumps(await biz.get_my_pets(user_id))

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
    async def get_hot_posts() -> str:
        """查询社区当前的热门动态：标题、内容、分类、作者昵称、点赞数，最多 6 条。

        用户询问「社区最近在聊什么」「有什么热门话题」时调用；属于社区公开内容，与用户个人数据无关。
        """
        return _dumps(await biz.get_hot_posts(limit=_HOT_POSTS_LIMIT, user_id=user_id))

    @tool(parse_docstring=True)
    async def search_products(keyword: str = "") -> str:
        """按关键词搜索商城在售商品：商品名、价格、库存、店铺、分类，最多 8 条。

        用户想找某类商品（如猫粮、猫砂、牵引绳）或问「商城有什么」时调用；返回的 id 可直接用于 get_product_reviews。

        Args:
            keyword: 搜索关键词，如「猫粮」「猫砂」；留空表示按热度返回在售商品。
        """
        return _dumps(await biz.search_products(keyword, limit=_PRODUCT_SEARCH_LIMIT, user_id=user_id))

    # 顺序即工具在提示词中的出现顺序，把最常用的档案 / 订单类放在前面。
    return [get_my_pets, get_my_orders, get_my_cart, get_product_reviews, get_hot_posts, search_products]
