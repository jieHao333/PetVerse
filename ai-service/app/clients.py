"""下游业务微服务 HTTP 客户端。

ai-service 需要宠物档案、订单、购物车、评价、社区动态等真实数据来做上下文注入与
推荐重排，这些数据分散在 pet / space / shop 等 Java 微服务里。本模块提供统一访问层：
共享一个连接池、自动注入内部用户标识，并把**读**与**写**的错误语义分开处理。

读 / 写两种契约（刻意不统一）：
    - ``get`` 系列：失败一律返回空列表 / None。这些数据只用于「给回答补充背景」，
      拉不到时应退化为通用回答，而不是让用户看到 500。
    - ``request``：返回 ``{ok, code, msg, data}``，**保留业务错误**。加购失败要告诉用户
      「库存不足」，而不是静默当作成功——写操作不能降级。

服务间身份（为什么不只看 IP）：
    每次调用都会带上 ``X-Internal-Token``（值取 ``settings.INTERNAL_TOKEN``）。业务侧的鉴权
    过滤器先校验这个共享令牌，通过了才信任 ``X-User-Id``。早期实现是用「来源 IP 是否在
    回环地址白名单里」来判断内部调用，但 Nacos 注册的是局域网地址（192.168/10.x），
    ai-service 即便同机部署，源地址也不是 127.0.0.1，身份头会被当成外部伪造头剥离，
    导致宠物档案、订单、购物车这些需要身份的接口全部 401 —— 画像一空，推荐就退化成热门兜底。
"""

import logging
from typing import Any, List, Optional

import httpx

from app.config import settings
from app.service_discovery import discovery

logger = logging.getLogger(__name__)


def _identity_headers(user_id: int) -> dict:
    """构造服务间调用的身份头。

    两个头各司其职，缺一不可：``X-Internal-Token`` 证明「请求来自受信任的内部服务」，
    ``X-User-Id`` 说明「以哪个用户的身份查询」。令牌未配置时不发送该头，业务侧会退回
    「按来源 IP 信任」的旧逻辑——那条通道只在同回环地址下有效，跨主机 / 容器部署会失效。

    Args:
        user_id: 目标用户 ID；0 表示匿名或系统级调用，不注入用户身份。

    Returns:
        可直接传给 httpx 的请求头字典；两个头都不需要时为空字典。
    """
    headers: dict = {}
    token = settings.INTERNAL_TOKEN.strip()
    if token:
        headers["X-Internal-Token"] = token
    # 只在有效用户下注入：user_id=0 表示匿名/系统调用，带上反而会被下游判为非法。
    if user_id and user_id > 0:
        headers["X-User-Id"] = str(user_id)
    return headers


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
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=settings.HTTP_TIMEOUT,
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
            )
        return self._client

    async def close(self) -> None:
        """关闭连接池；异常仅记录日志，不影响停机流程。
        """
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                logger.warning("关闭业务服务 HTTP 连接池异常（忽略）", exc_info=True)
            self._client = None

    async def get(self, service_name: str, path: str, params: dict = None,
                  user_id: int = 0) -> Any:
        """发起一次 GET 并解包统一报文。

        Args:
            service_name: Nacos 中的逻辑服务名（取自 ``settings.*_SERVICE_NAME``）。
            path: 接口路径，如 ``/pet/my-list``。
            params: 查询参数；None 表示不带参数。
            user_id: 大于 0 时注入 ``X-User-Id`` 头。网关在外部请求上会剥离该头，
                这里是服务间调用，用它表明「以哪个用户的身份查询」；配合
                ``X-Internal-Token`` 才能让业务侧接受这个身份。

        Returns:
            报文 ``data`` 字段；非 200、业务码非 200 或发生任何异常时返回 None。
        """
        headers = _identity_headers(user_id)
        try:
            base_url = await discovery.resolve(service_name)
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
            logger.warning("业务接口调用失败（降级为空）: service=%s path=%s", service_name, path, exc_info=True)
            return None


    async def request(self, method: str, service_name: str, path: str, *,
                      json: Optional[dict] = None, params: Optional[dict] = None,
                      user_id: int = 0) -> dict:
        """发起一次写请求，并**保留**下游的业务错误。

        与 ``get`` 的契约刻意不同：写操作不能「失败当成功」。加购失败要如实告诉用户
        「库存不足 / 商品已下架」，否则模型会向用户宣布一个并未发生的操作。

        Args:
            method: HTTP 方法（``POST`` / ``PUT`` / ``DELETE``）。
            service_name: Nacos 中的逻辑服务名；单体模式由发现器统一映射到单体服务。
            path: 接口路径，如 ``/shop/cart``。
            json: 请求体；None 表示无 body。
            params: 查询参数；None 表示不带参数。
            user_id: 大于 0 时注入 ``X-User-Id`` 头（下游据此确定操作者）。

        Returns:
            ``{"ok": bool, "code": int, "msg": str, "data": Any}``。
            网络异常与超时也走同一结构（``code=500``、``msg`` 为可直接展示的中文）。
        """
        headers = _identity_headers(user_id)
        try:
            base_url = await discovery.resolve(service_name)
            resp = await self._get_client().request(
                method, base_url + path, json=json, params=params, headers=headers)
        except Exception:
            logger.warning("业务写接口调用失败: %s service=%s path=%s", method, service_name, path, exc_info=True)
            return {"ok": False, "code": 500, "msg": "服务暂时不可用，请稍后再试", "data": None}

        if resp.status_code != 200:
            logger.warning("业务写接口返回非 200: %s %s%s -> %s", method, base_url, path, resp.status_code)
            return {"ok": False, "code": resp.status_code,
                    "msg": f"服务返回异常（{resp.status_code}），请稍后再试", "data": None}

        try:
            body = resp.json()
        except Exception:
            logger.warning("业务写接口响应无法解析: %s %s%s", method, base_url, path, exc_info=True)
            return {"ok": False, "code": 500, "msg": "服务响应异常，请稍后再试", "data": None}

        # 标准 {code,msg,data} 报文：业务码非 200 视为失败，但 msg 原样透出（如「库存不足」）。
        if isinstance(body, dict) and "code" in body:
            ok = body.get("code") == 200
            return {
                "ok": ok,
                "code": int(body.get("code") or 500),
                "msg": str(body.get("msg") or ("操作成功" if ok else "操作失败")),
                "data": body.get("data"),
            }
        # 少数老接口直接返回裸数据，视为成功。
        return {"ok": True, "code": 200, "msg": "操作成功", "data": body}

    async def get_my_pets(self, user_id: int) -> List[dict]:
        """查询用户名下的宠物档案列表。

        Returns:
            宠物档案字典列表；失败或无数据时为空列表。
        """
        data = await self.get(settings.PET_SERVICE_NAME, "/pet/my-list", user_id=user_id)
        return data if isinstance(data, list) else []


    async def get_hot_posts(self, limit: int = 8, user_id: int = 0) -> List[dict]:
        """查询社区热门动态（按热度排序）。

        Args:
            limit: 返回条数上限。
            user_id: 调用者用户 ID，用于下游做可见性过滤。

        Returns:
            动态字典列表；失败时为空列表。
        """
        data = await self.get(settings.SPACE_SERVICE_NAME, "/space/page",
                              params={"sort": "hot", "pageNum": 1, "pageSize": limit},
                              user_id=user_id)
        return _records(data)

    async def search_posts(self, keyword: str, limit: int = 8, user_id: int = 0) -> List[dict]:
        """按关键词搜索社区动态。

        Args:
            keyword: 搜索词（如物种、品种）。
            limit: 返回条数上限。

        Returns:
            动态字典列表；失败时为空列表。
        """
        data = await self.get(settings.SPACE_SERVICE_NAME, "/space/page",
                              params={"keyword": keyword, "sort": "hot", "pageNum": 1, "pageSize": limit},
                              user_id=user_id)
        return _records(data)

    async def get_my_posts(self, user_id: int, limit: int = 10) -> List[dict]:
        """查询用户自己发布的动态（用于推断其关注的物种/品类）。

        Args:
            limit: 返回条数上限。

        Returns:
            动态字典列表；失败时为空列表。
        """
        data = await self.get(settings.SPACE_SERVICE_NAME, "/space/page",
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

        Returns:
            商品字典列表；失败时为空列表。
        """
        params: dict = {"pageNum": 1, "pageSize": limit}
        # 空关键词不传参：部分下游会把空串当作「匹配空」而返回 0 条。
        if keyword:
            params["keyword"] = keyword
        if category is not None:
            params["category"] = category
        data = await self.get(settings.SHOP_SERVICE_NAME, "/shop/product/page",
                              params=params, user_id=user_id)
        return _records(data)

    async def get_my_cart(self, user_id: int) -> List[dict]:
        """查询用户购物车。

        Returns:
            购物车项字典列表（含失效项及其原因）；失败时为空列表。
        """
        data = await self.get(settings.SHOP_SERVICE_NAME, "/shop/cart", user_id=user_id)
        return data if isinstance(data, list) else []

    async def get_my_orders(self, user_id: int, limit: int = 10) -> List[dict]:
        """查询用户订单（最近若干条）。

        Args:
            limit: 返回条数上限。

        Returns:
            订单字典列表；失败时为空列表。
        """
        data = await self.get(settings.SHOP_SERVICE_NAME, "/shop/order/page",
                              params={"pageNum": 1, "pageSize": limit}, user_id=user_id)
        return _records(data)

    async def get_product_reviews(self, product_id: int, limit: int = 50) -> List[dict]:
        """查询指定商品的评价列表。

        注意：这是公开数据，不传 ``user_id``，避免下游按用户做可见性裁剪。

        Args:
            limit: 返回条数上限。

        Returns:
            评价字典列表；失败时为空列表。
        """
        data = await self.get(settings.SHOP_SERVICE_NAME, "/shop/review/page",
                              params={"productId": product_id, "pageNum": 1, "pageSize": limit})
        return _records(data)

    async def get_my_reviews(self, user_id: int, limit: int = 10) -> List[dict]:
        """查询用户发表过的商品评价（用于推断其消费偏好）。

        Args:
            limit: 返回条数上限。

        Returns:
            评价字典列表；失败时为空列表。
        """
        data = await self.get(settings.SHOP_SERVICE_NAME, "/shop/review/my/page",
                              params={"pageNum": 1, "pageSize": limit}, user_id=user_id)
        return _records(data)

    async def get_data_profile(self, user_id: int) -> Optional[dict]:
        """从数据中台读取用户画像/特征（统一数据服务，一次取数替代逐接口现拉）。

        失败降级为 None，调用方据此回退到本地推断，不影响推荐主流程。
        """
        data = await self.get(settings.DATA_SERVICE_NAME, f"/data/internal/profile/{user_id}",
                              user_id=user_id)
        return data if isinstance(data, dict) else None

    async def get_product_stats(self, product_ids: List[int], user_id: int = 0) -> List[dict]:
        """从数据中台批量读取商品统计（CTR/热度），供推荐重排参考。失败降级为空列表。"""
        if not product_ids:
            return []
        ids = ",".join(str(p) for p in product_ids if p is not None)
        data = await self.get(settings.DATA_SERVICE_NAME, "/data/internal/product-stats",
                              params={"ids": ids}, user_id=user_id)
        return data if isinstance(data, list) else []

    async def get_rerank_model(self, user_id: int = 0) -> Optional[dict]:
        """从数据中台读取当前激活的重排模型（特征/权重）。无模型或失败时返回 None。"""
        data = await self.get(settings.DATA_SERVICE_NAME, "/data/internal/model", user_id=user_id)
        return data if isinstance(data, dict) else None


def _records(data: Any) -> List[dict]:
    """从分页响应中取出记录数组。

    下游分页结构有两种形态：``{"records": [...], "total": n}`` 与直接返回数组。
    统一在这里适配，调用方不必各自判断。

    Returns:
        记录列表；结构不符时返回空列表（不抛错，保持「取不到就当没有」的契约）。
    """
    if isinstance(data, dict):
        records = data.get("records")
        return records if isinstance(records, list) else []
    if isinstance(data, list):
        return data
    return []


# 进程级单例：所有业务接口共用同一个连接池。
biz = BizClient()
