"""下游业务微服务 HTTP 客户端。

职责：ai-service 需要宠物档案、订单、购物车、评价、社区动态等真实数据来做上下文注入与
推荐重排，这些数据分散在 pet / space / shop 等 Java 微服务里。本模块提供**只读**的统一访问层：
共享一个连接池、自动注入内部用户标识、把所有异常压成「空结果」。

主要导出：
    - ``BizClient``  业务接口客户端（含各业务语义方法）
    - ``biz``        进程级单例，直接 import 使用
    - ``_records``   分页响应解包工具（内部）

外部依赖：``httpx``（异步连接池）、``app.config.settings`` 中的各 ``*_SERVICE_URL``。

使用示例：
    from app.clients import biz

    pets = await biz.get_my_pets(user_id=42)
    posts = await biz.search_posts("猫砂", limit=8, user_id=42)

设计取舍：所有方法**不抛业务异常**，失败一律返回空列表 / None。原因是这些数据只用于
「给回答补充背景」，拉不到时应当退化为通用回答，而不是让用户看到 500。
需要强一致性的写操作不要放到这里。
"""

import logging
from typing import Any, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class BizClient:
    """业务微服务只读客户端。

    连接池惰性创建并全进程复用；``close`` 由应用生命周期钩子在停机时调用。
    """

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """获取（并按需创建）共享的异步 HTTP 客户端。

        Returns:
            配置好超时与连接上限的 ``httpx.AsyncClient``。

        Raises:
            无（构造阶段不会发起网络请求）。
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=settings.HTTP_TIMEOUT,
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
            )
        return self._client

    async def close(self) -> None:
        """关闭连接池；异常仅记录日志，不影响停机流程。

        Returns:
            None。

        Raises:
            无。
        """
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.warning("关闭业务服务 HTTP 连接池异常（忽略）", exc_info=True)
            self._client = None

    async def get(self, base_url: str, path: str, params: dict = None,
                  user_id: int = 0) -> Any:
        """发起一次 GET 并解包统一报文。

        Args:
            base_url: 目标服务根地址（取自 ``settings.*_SERVICE_URL``）。
            path: 接口路径，如 ``/pet/my-list``。
            params: 查询参数；None 表示不带参数。
            user_id: 大于 0 时注入 ``X-User-Id`` 头。网关在外部请求上会剥离该头，
                这里是服务间调用，用它表明「以哪个用户的身份查询」。

        Returns:
            报文 ``data`` 字段；非 200、业务码非 200 或发生任何异常时返回 None。

        Raises:
            无——异常在内部吞掉并降级为 None。
        """
        headers = {}
        # 只在有效用户下注入：user_id=0 表示匿名/系统调用，带上反而会被下游判为非法。
        if user_id and user_id > 0:
            headers["X-User-Id"] = str(user_id)
        try:
            resp = await self._get_client().get(base_url + path, params=params, headers=headers)
            if resp.status_code != 200:
                logger.warning("业务接口返回非 200: %s%s -> %s", base_url, path, resp.status_code)
                return None
            body = resp.json()
            # 兼容两种响应：标准 {code,msg,data} 报文与直接返回裸数据的老接口。
            if isinstance(body, dict) and "code" in body:
                return body.get("data") if body.get("code") == 200 else None
            return body
        except Exception:
            logger.warning("业务接口调用失败（降级为空）: %s%s", base_url, path, exc_info=True)
            return None


    async def get_my_pets(self, user_id: int) -> List[dict]:
        """查询用户名下的宠物档案列表。

        Args:
            user_id: 用户 ID。

        Returns:
            宠物档案字典列表；失败或无数据时为空列表。

        Raises:
            无。
        """
        data = await self.get(settings.PET_SERVICE_URL, "/pet/my-list", user_id=user_id)
        return data if isinstance(data, list) else []


    async def get_hot_posts(self, limit: int = 8, user_id: int = 0) -> List[dict]:
        """查询社区热门动态（按热度排序）。

        Args:
            limit: 返回条数上限。
            user_id: 调用者用户 ID，用于下游做可见性过滤。

        Returns:
            动态字典列表；失败时为空列表。

        Raises:
            无。
        """
        data = await self.get(settings.SPACE_SERVICE_URL, "/space/page",
                              params={"sort": "hot", "pageNum": 1, "pageSize": limit},
                              user_id=user_id)
        return _records(data)

    async def search_posts(self, keyword: str, limit: int = 8, user_id: int = 0) -> List[dict]:
        """按关键词搜索社区动态。

        Args:
            keyword: 搜索词（如物种、品种）。
            limit: 返回条数上限。
            user_id: 调用者用户 ID。

        Returns:
            动态字典列表；失败时为空列表。

        Raises:
            无。
        """
        data = await self.get(settings.SPACE_SERVICE_URL, "/space/page",
                              params={"keyword": keyword, "sort": "hot", "pageNum": 1, "pageSize": limit},
                              user_id=user_id)
        return _records(data)

    async def get_my_posts(self, user_id: int, limit: int = 10) -> List[dict]:
        """查询用户自己发布的动态（用于推断其关注的物种/品类）。

        Args:
            user_id: 用户 ID。
            limit: 返回条数上限。

        Returns:
            动态字典列表；失败时为空列表。

        Raises:
            无。
        """
        data = await self.get(settings.SPACE_SERVICE_URL, "/space/page",
                              params={"userId": user_id, "pageNum": 1, "pageSize": limit},
                              user_id=user_id)
        return _records(data)


    async def search_products(self, keyword: str = "", limit: int = 12,
                              category: Optional[int] = None, user_id: int = 0) -> List[dict]:
        """按关键词 / 分类搜索在售商品。

        Args:
            keyword: 搜索词；空串表示不加关键词（按默认排序取在售商品）。
            limit: 返回条数上限。
            category: 分类 ID；None 表示不限分类。
            user_id: 调用者用户 ID。

        Returns:
            商品字典列表；失败时为空列表。

        Raises:
            无。
        """
        params: dict = {"pageNum": 1, "pageSize": limit}
        # 空关键词不传参：部分下游会把空串当作「匹配空」而返回 0 条。
        if keyword:
            params["keyword"] = keyword
        if category is not None:
            params["category"] = category
        data = await self.get(settings.SHOP_SERVICE_URL, "/shop/product/page",
                              params=params, user_id=user_id)
        return _records(data)

    async def get_my_cart(self, user_id: int) -> List[dict]:
        """查询用户购物车。

        Args:
            user_id: 用户 ID。

        Returns:
            购物车项字典列表（含失效项及其原因）；失败时为空列表。

        Raises:
            无。
        """
        data = await self.get(settings.SHOP_SERVICE_URL, "/shop/cart", user_id=user_id)
        return data if isinstance(data, list) else []

    async def get_my_orders(self, user_id: int, limit: int = 10) -> List[dict]:
        """查询用户订单（最近若干条）。

        Args:
            user_id: 用户 ID。
            limit: 返回条数上限。

        Returns:
            订单字典列表；失败时为空列表。

        Raises:
            无。
        """
        data = await self.get(settings.SHOP_SERVICE_URL, "/shop/order/page",
                              params={"pageNum": 1, "pageSize": limit}, user_id=user_id)
        return _records(data)

    async def get_product_reviews(self, product_id: int, limit: int = 50) -> List[dict]:
        """查询指定商品的评价列表。

        注意：这是公开数据，不传 ``user_id``，避免下游按用户做可见性裁剪。

        Args:
            product_id: 商品 ID。
            limit: 返回条数上限。

        Returns:
            评价字典列表；失败时为空列表。

        Raises:
            无。
        """
        data = await self.get(settings.SHOP_SERVICE_URL, "/shop/review/page",
                              params={"productId": product_id, "pageNum": 1, "pageSize": limit})
        return _records(data)

    async def get_my_reviews(self, user_id: int, limit: int = 10) -> List[dict]:
        """查询用户发表过的商品评价（用于推断其消费偏好）。

        Args:
            user_id: 用户 ID。
            limit: 返回条数上限。

        Returns:
            评价字典列表；失败时为空列表。

        Raises:
            无。
        """
        data = await self.get(settings.SHOP_SERVICE_URL, "/shop/review/my/page",
                              params={"pageNum": 1, "pageSize": limit}, user_id=user_id)
        return _records(data)

    async def get_data_profile(self, user_id: int) -> Optional[dict]:
        """从数据中台读取用户画像/特征（统一数据服务，一次取数替代逐接口现拉）。

        失败降级为 None，调用方据此回退到本地推断，不影响推荐主流程。
        """
        data = await self.get(settings.DATA_SERVICE_URL, f"/data/internal/profile/{user_id}",
                              user_id=user_id)
        return data if isinstance(data, dict) else None

    async def get_product_stats(self, product_ids: List[int], user_id: int = 0) -> List[dict]:
        """从数据中台批量读取商品统计（CTR/热度），供推荐重排参考。失败降级为空列表。"""
        if not product_ids:
            return []
        ids = ",".join(str(p) for p in product_ids if p is not None)
        data = await self.get(settings.DATA_SERVICE_URL, "/data/internal/product-stats",
                              params={"ids": ids}, user_id=user_id)
        return data if isinstance(data, list) else []

    async def get_rerank_model(self, user_id: int = 0) -> Optional[dict]:
        """从数据中台读取当前激活的重排模型（特征/权重）。无模型或失败时返回 None。"""
        data = await self.get(settings.DATA_SERVICE_URL, "/data/internal/model", user_id=user_id)
        return data if isinstance(data, dict) else None


def _records(data: Any) -> List[dict]:
    """从分页响应中取出记录数组。

    下游分页结构有两种形态：``{"records": [...], "total": n}`` 与直接返回数组。
    统一在这里适配，调用方不必各自判断。

    Args:
        data: 已解包的 ``data`` 字段。

    Returns:
        记录列表；结构不符时返回空列表（不抛错，保持「取不到就当没有」的契约）。

    Raises:
        无。
    """
    if isinstance(data, dict):
        records = data.get("records")
        return records if isinstance(records, list) else []
    if isinstance(data, list):
        return data
    return []


# 进程级单例：所有业务接口共用同一个连接池。
biz = BizClient()
