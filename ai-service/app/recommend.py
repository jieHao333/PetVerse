"""个性化推荐接口。

职责：暴露 ``GET /ai/recommend/feed``，为首页推荐流与商城「猜你喜欢」提供内容列表。
业务逻辑（画像聚合、候选召回、重排、缓存）在 ``app.graph.recommend_graph``。

主要导出：
    - ``router``  FastAPI 路由（前缀 ``/ai``）

外部依赖：``app.graph.recommend_graph.recommend``、``app.schemas``。

使用示例：
    GET /ai/recommend/feed?scene=home
    GET /ai/recommend/feed?scene=shop&refresh=true   # 跳过缓存强制重算
"""

import logging
from typing import Optional

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from app.graph.recommend_graph import recommend
from app.schemas import fail, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai")

# 推荐结果因人而异，禁止任何中间层缓存——否则用户可能看到别人的推荐（共享缓存/CDN 场景）。
_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/recommend/feed")
async def recommend_feed(
    scene: str = Query(default="home", pattern="^(home|shop)$"),
    refresh: bool = Query(default=False, description="是否跳过缓存强制刷新"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """获取个性化推荐流。

    Args:
        scene: 场景，``home`` 首页（商品 + 动态混排）/ ``shop`` 商城（仅商品）。
        refresh: True 时跳过缓存强制重算（用户主动下拉刷新）。
        x_user_id: 网关注入的用户 ID。

    Returns:
        ``ok({"items": [...], "summary": "..."})``；未登录返回 401。

    Raises:
        无。
    """
    if not x_user_id:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    try:
        user_id = int(x_user_id)
    except (TypeError, ValueError):
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    try:
        data = await recommend(user_id, scene=scene, use_cache=not refresh)
        return JSONResponse(status_code=200, content=ok(data), headers=_NO_STORE)
    except Exception:
        logger.exception("个性化推荐失败: user_id=%s scene=%s", user_id, scene)
        return JSONResponse(status_code=200, content=fail(500, "AI 服务暂时开小差了，请稍后再试"))
