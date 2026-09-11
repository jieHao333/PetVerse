"""个性化推荐路由（APIRouter prefix="/ai"）

接口：
  1. GET /ai/recommend/feed?scene=home|shop   个性化推荐流（结果按用户缓存）

鉴权：依赖网关注入的 X-User-Id；推荐按用户隔离并缓存。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from app.graph.recommend_graph import recommend
from app.schemas import fail, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai")


@router.get("/recommend/feed")
async def recommend_feed(
    scene: str = Query(default="home", pattern="^(home|shop)$"),
    refresh: bool = Query(default=False, description="是否跳过缓存强制刷新"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """个性化推荐流

    返回 data：{items: [{id,type,title,image,price?,reason,score,...}], summary}。
    数据来源：用户宠物 / 订单 / 购物车 / 评价 / 动态画像 + 商品与社区候选。
    """
    if not x_user_id:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    try:
        user_id = int(x_user_id)
    except (TypeError, ValueError):
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    try:
        data = await recommend(user_id, scene=scene, use_cache=not refresh)
        return JSONResponse(status_code=200, content=ok(data))
    except Exception:
        logger.exception("个性化推荐失败: user_id=%s scene=%s", user_id, scene)
        return JSONResponse(status_code=200, content=fail(500, "AI 服务暂时开小差了，请稍后再试"))
