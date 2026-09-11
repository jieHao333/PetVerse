"""商城 AI 能力路由（APIRouter prefix="/ai"）

接口：
  1. POST /ai/shop/review/summary   商品评论 AI 摘要（结果缓存）

鉴权：依赖网关注入的 X-User-Id；评论摘要本身是公开数据，但仍要求登录调用。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.graph.review_graph import summarize_product
from app.schemas import fail, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai")


class ReviewSummaryRequest(BaseModel):
    """评论摘要请求体"""

    productId: int = Field(..., description="商品 ID")


@router.post("/shop/review/summary")
async def review_summary(
    req: ReviewSummaryRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """生成商品评论 AI 摘要

    返回 data：{sentiment, one_line, pros, cons, keywords, count}。
    无评论时返回中性空态；结果按商品缓存（TTL 可配，默认 6 小时）。
    """
    if not x_user_id:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    try:
        summary = await summarize_product(int(req.productId))
        return JSONResponse(status_code=200, content=ok(summary))
    except Exception:
        logger.exception("评论摘要失败: product_id=%s", req.productId)
        return JSONResponse(status_code=200, content=fail(500, "AI 服务暂时开小差了，请稍后再试"))
