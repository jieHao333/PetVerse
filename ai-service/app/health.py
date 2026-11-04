"""宠物健康评估接口。

职责：暴露 ``POST /ai/health/assess``（生成评估报告）与 ``GET /ai/health/history``
（查询历史报告）。评估逻辑在 ``app.graph.health_graph``，历史报告存在
``pet_health_report`` 表（见 ``app.pg_store``）。

主要导出：
    - ``router``  FastAPI 路由（前缀 ``/ai``）

外部依赖：``app.graph.health_graph.assess``、``app.pg_store``、``app.schemas``。

使用示例：
    POST /ai/health/assess
    Body: {"id": 7, "name": "豆豆", "species": "猫", "age": 3,
           "health": {"weight": "4.2kg", "vaccine": "已打三针"}}
"""

import logging
from typing import Optional

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from app import pg_store
from app.graph.health_graph import assess
from app.schemas import PetInfo, fail, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai")


def _parse_user_id(x_user_id: Optional[str]) -> Optional[int]:
    """解析网关注入的用户标识。

    Args:
        x_user_id: 请求头原始值。

    Returns:
        正整数用户 ID；缺失或非法时返回 None。

    Raises:
        无。
    """
    if not x_user_id:
        return None
    try:
        user_id = int(x_user_id)
    except (TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


@router.post("/health/assess")
async def assess_health(
    pet: PetInfo,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """对一只宠物做健康评估。

    请求体直接就是宠物档案（而非 ``{"pet": {...}}`` 包装），与前端表单结构保持一致。

    Args:
        pet: 宠物档案，``health`` 字段承载体重 / 疫苗 / 病史等评估输入。
        x_user_id: 网关注入的用户 ID。

    Returns:
        ``ok(PetHealthReport)``；未登录返回 401，内部异常返回 500 通用提示。

    Raises:
        无。
    """
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    try:
        report = await assess(user_id, pet.model_dump())
        return JSONResponse(status_code=200, content=ok(report))
    except Exception:
        logger.exception("健康评估失败: user_id=%s pet_id=%s", user_id, pet.id)
        return JSONResponse(status_code=200, content=fail(500, "AI 服务暂时开小差了，请稍后再试"))


@router.get("/health/history")
async def health_history(
    petId: Optional[int] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """查询宠物的历史评估报告（最近在前）。

    Args:
        petId: 宠物 ID（必填）。
        limit: 返回条数，1~50。
        x_user_id: 网关注入的用户 ID。

    Returns:
        ``ok({"reports": [...]})``；缺少 petId 返回 400。

    Raises:
        无——查询失败时 ``pg_store`` 已降级为空列表。
    """
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    if petId is None:
        return JSONResponse(status_code=200, content=fail(400, "参数错误"))
    # user_id 一并作为查询条件，避免通过猜 petId 读到他人宠物的报告。
    reports = await pg_store.list_health_reports(user_id, petId, limit=limit)
    return JSONResponse(status_code=200, content=ok({"reports": reports}))
