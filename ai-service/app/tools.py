"""LangGraph Agent 工具集

工具让 AI 对话能够查询用户的真实业务数据（而非凭空回答）：
  - 我的宠物：get_my_pets
  - 我的订单：get_my_orders
  - 我的购物车：get_my_cart
  - 商品评论：get_product_reviews
  - 热门动态：get_hot_posts

工具按请求动态构建（闭包捕获 user_id），因此天然具备用户隔离；
全部为只读查询，失败时返回友好的空提示，不会影响对话主流程。
"""
import json
import logging
from typing import List

from langchain_core.tools import StructuredTool

from app.clients import biz

logger = logging.getLogger(__name__)

# 工具返回内容的截断长度，避免把过长 JSON 塞进上下文
_MAX_CHARS = 2000


def _dumps(data) -> str:
    """紧凑 JSON 序列化（保证中文可读），并做长度截断"""
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        text = str(data)
    return text[:_MAX_CHARS]


def build_tools(user_id: int) -> List[StructuredTool]:
    """为指定用户构建工具列表（user_id<=0 时返回空列表，避免越权查询）"""
    if not user_id or user_id <= 0:
        return []

    async def get_my_pets() -> str:
        """查询当前用户全部宠物的档案与健康信息（名字、物种、品种、年龄、体重、疫苗、病史等）。"""
        return _dumps(await biz.get_my_pets(user_id))

    async def get_my_orders() -> str:
        """查询当前用户的近期订单（商品、金额、状态、自提码）。"""
        return _dumps(await biz.get_my_orders(user_id))

    async def get_my_cart() -> str:
        """查询当前用户的购物车内容（商品名、价格、数量）。"""
        return _dumps(await biz.get_my_cart(user_id))

    async def get_product_reviews(product_id: int) -> str:
        """查询指定商品的用户评论（评分与内容），用于回答商品口碑类问题。

        :param product_id: 商品 ID
        """
        return _dumps(await biz.get_product_reviews(product_id))

    async def get_hot_posts() -> str:
        """查询社区当前热门动态（标题、内容、点赞数），用于推荐或了解社区话题。"""
        return _dumps(await biz.get_hot_posts(limit=6, user_id=user_id))

    async def search_products(keyword: str) -> str:
        """按关键词搜索商城在售商品（商品名、价格、库存、店铺）。

        :param keyword: 搜索关键词，如「猫粮」「牵引绳」；传空字符串返回热门在售商品
        """
        return _dumps(await biz.search_products(keyword or "", limit=8, user_id=user_id))

    return [
        StructuredTool.from_function(coroutine=get_my_pets, name="get_my_pets",
                                     description="查询当前用户全部宠物的档案与健康信息"),
        StructuredTool.from_function(coroutine=get_my_orders, name="get_my_orders",
                                     description="查询当前用户的近期订单"),
        StructuredTool.from_function(coroutine=get_my_cart, name="get_my_cart",
                                     description="查询当前用户的购物车内容"),
        StructuredTool.from_function(coroutine=get_product_reviews, name="get_product_reviews",
                                     description="查询指定商品 ID 的用户评论，入参 product_id"),
        StructuredTool.from_function(coroutine=get_hot_posts, name="get_hot_posts",
                                     description="查询社区当前热门动态"),
        StructuredTool.from_function(coroutine=search_products, name="search_products",
                                     description="按关键词搜索商城在售商品（如猫粮、狗粮、用品）"),
    ]
