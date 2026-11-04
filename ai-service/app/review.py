"""商品评论摘要接口。

职责：暴露 ``POST /ai/shop/review/summary``，把「按商品 ID 生成口碑摘要」的能力提供给
商城页面。业务逻辑（拉评论、LLM 摘要、缓存）在 ``app.graph.review_graph``，本文件只做协议。

主要导出：
    - ``router``            FastAPI 路由（前缀 ``/ai``）
    - ``ReviewSummaryRequest`` 请求体模型

外部依赖：``app.graph.review_graph.summarize_product``、``app.schemas``。

使用示例：
    POST /ai/shop/review/summary
    Body: {"productId": 128}
    → {"code":200,"data":{"sentiment":"positive","one_line":"...","count":32,...}}
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
    """评论摘要请求体。"""

    productId: int = Field(..., description="商品 ID")


@router.post("/shop/review/summary")
async def review_summary(
    req: ReviewSummaryRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """生成指定商品的口碑摘要。

    评论本身是公开数据，但仍要求登录：摘要调用会产生 LLM 成本，需要可追溯的调用者。

    Args:
        req: 请求体（含 ``productId``）。
        x_user_id: 网关注入的用户 ID。

    Returns:
        ``ok(ReviewSummary)``；未登录返回 401，内部异常返回 500 通用提示。

    Raises:
        无——异常统一转成业务报文，前端不会收到裸 5xx。
    """
    if not x_user_id:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    try:
        summary = await summarize_product(int(req.productId))
        return JSONResponse(status_code=200, content=ok(summary))
    except Exception:
        logger.exception("评论摘要失败: product_id=%s", req.productId)
        return JSONResponse(status_code=200, content=fail(500, "AI 服务暂时开小差了，请稍后再试"))
