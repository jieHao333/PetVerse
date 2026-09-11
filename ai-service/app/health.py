"""宠物健康智能评估路由（APIRouter prefix="/ai"）

接口：
  1. POST /ai/health/assess   对指定宠物执行健康评估，返回结构化报告并落库
  2. GET  /ai/health/history  查询某宠物的历史评估记录

鉴权：依赖网关注入的 X-User-Id；报告按 用户 + 宠物 隔离。
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
    """解析网关注入的 X-User-Id；缺失/非法返回 None"""
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
    """执行宠物健康评估

    请求体即宠物档案（字段与对话接口的 pet 一致，含 health 子对象）。
    返回 data 为结构化报告：{score, level, summary, risks, suggestions, carePlan, reminders, disclaimer}。
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
    """查询某宠物的历史健康评估（时间倒序）"""
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    if petId is None:
        return JSONResponse(status_code=200, content=fail(400, "参数错误"))
    reports = await pg_store.list_health_reports(user_id, petId, limit=limit)
    return JSONResponse(status_code=200, content=ok({"reports": reports}))
