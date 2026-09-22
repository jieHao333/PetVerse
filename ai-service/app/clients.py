"""业务微服务调用客户端（httpx.AsyncClient）

ai-service 需要读取用户侧数据（宠物档案、动态、订单、购物车、评价）来支撑
健康评估与个性化推荐。这里直连各业务服务，统一注入内部请求头 X-User-Id；
Java 侧 UserContextInterceptor 在无 JWT 时信任该内部头（外部请求由网关剥离），
直接复用同一套内部信任头机制拿到当前用户数据。

降级原则：任何调用失败（服务未启动 / 超时 / 报文异常）都返回空值并仅记日志，
绝不让 AI 主流程报错——推荐与健康评估在数据缺失时退化为通用结果。
"""
import logging
from typing import Any, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class BizClient:
    """业务服务只读客户端（懒创建，复用连接池）"""

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=settings.HTTP_TIMEOUT,
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
            )
        return self._client

    async def close(self) -> None:
        """关闭连接池（服务停机时调用）"""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.warning("关闭业务服务 HTTP 连接池异常（忽略）", exc_info=True)
            self._client = None

    async def get(self, base_url: str, path: str, params: dict = None,
                  user_id: int = 0) -> Any:
        """GET 请求，返回响应体中的 data 字段（Result 解包）；失败返回 None

        :param base_url: 目标服务根地址（如 http://127.0.0.1:8081）
        :param path: 服务内路径（如 /pet/my-list，注意网关已 StripPrefix，此处不带 /api）
        :param params: 查询参数
        :param user_id: 透传给业务服务的用户身份（0 表示不带身份）
        """
        headers = {}
        if user_id and user_id > 0:
            headers["X-User-Id"] = str(user_id)
        try:
            resp = await self._get_client().get(base_url + path, params=params, headers=headers)
            if resp.status_code != 200:
                logger.warning("业务接口返回非 200: %s%s -> %s", base_url, path, resp.status_code)
                return None
            body = resp.json()
            # 统一 Result 报文：仅当 code==200 时取 data
            if isinstance(body, dict) and "code" in body:
                return body.get("data") if body.get("code") == 200 else None
            return body
        except Exception:
            logger.warning("业务接口调用失败（降级为空）: %s%s", base_url, path, exc_info=True)
            return None

    # ---------- 宠物服务 ----------

    async def get_my_pets(self, user_id: int) -> List[dict]:
        """当前用户全部宠物（含健康信息）"""
        data = await self.get(settings.PET_SERVICE_URL, "/pet/my-list", user_id=user_id)
        return data if isinstance(data, list) else []

    # ---------- 空间服务 ----------

    async def get_hot_posts(self, limit: int = 8, user_id: int = 0) -> List[dict]:
        """热门动态（点赞数降序）"""
        data = await self.get(settings.SPACE_SERVICE_URL, "/space/page",
                              params={"sort": "hot", "pageNum": 1, "pageSize": limit},
                              user_id=user_id)
        return _records(data)

    async def get_my_posts(self, user_id: int, limit: int = 10) -> List[dict]:
        """当前用户发布的动态"""
        data = await self.get(settings.SPACE_SERVICE_URL, "/space/page",
                              params={"userId": user_id, "pageNum": 1, "pageSize": limit},
                              user_id=user_id)
        return _records(data)

    # ---------- 商城服务 ----------

    async def search_products(self, keyword: str = "", limit: int = 12,
                              category: Optional[int] = None, user_id: int = 0) -> List[dict]:
        """在售商品（关键词 / 分类可选）"""
        params: dict = {"pageNum": 1, "pageSize": limit}
        if keyword:
            params["keyword"] = keyword
        if category is not None:
            params["category"] = category
        data = await self.get(settings.SHOP_SERVICE_URL, "/shop/product/page",
                              params=params, user_id=user_id)
        return _records(data)

    async def get_my_cart(self, user_id: int) -> List[dict]:
        """当前用户购物车"""
        data = await self.get(settings.SHOP_SERVICE_URL, "/shop/cart", user_id=user_id)
        return data if isinstance(data, list) else []

    async def get_my_orders(self, user_id: int, limit: int = 10) -> List[dict]:
        """当前用户订单"""
        data = await self.get(settings.SHOP_SERVICE_URL, "/shop/order/page",
                              params={"pageNum": 1, "pageSize": limit}, user_id=user_id)
        return _records(data)

    async def get_product_reviews(self, product_id: int, limit: int = 50) -> List[dict]:
        """商品评论（评论摘要用；评论是公开数据，无需用户身份）"""
        data = await self.get(settings.SHOP_SERVICE_URL, "/shop/review/page",
                              params={"productId": product_id, "pageNum": 1, "pageSize": limit})
        return _records(data)

    async def get_my_reviews(self, user_id: int, limit: int = 10) -> List[dict]:
        """当前用户发表过的评价"""
        data = await self.get(settings.SHOP_SERVICE_URL, "/shop/review/my/page",
                              params={"pageNum": 1, "pageSize": limit}, user_id=user_id)
        return _records(data)


def _records(data: Any) -> List[dict]:
    """从 PageResult 结构中取 records 列表；非分页结构返回空列表"""
    if isinstance(data, dict):
        records = data.get("records")
        return records if isinstance(records, list) else []
    if isinstance(data, list):
        return data
    return []


# 模块级单例
biz = BizClient()
