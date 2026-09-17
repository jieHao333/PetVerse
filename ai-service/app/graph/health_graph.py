"""宠物健康评估工作流（health graph）

LangGraph 多步编排：
    load_profile → analyze → persist
  - load_profile：整理宠物档案 + 健康字段，缺失字段提示补全；
  - analyze：真实模式用 LLM 结构化输出 PetHealthReport；mock 模式用规则生成可演示报告；
  - persist：把报告落 PostgreSQL（pet_health_report），失败仅记日志。

对外由 app/health.py 的 POST /ai/health/assess 调用。
"""
import logging
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from app import pg_store
from app.config import settings
from app.llm import astructured_invoke
from app.schemas import PetHealthReport

logger = logging.getLogger(__name__)

# 健康字段中文标签
_FIELD_LABELS = {
    "weight": "体重",
    "bcs": "BCS体况评分",
    "deworming": "驱虫",
    "specialPeriod": "特殊时期",
    "vaccine": "疫苗",
    "rearingMethod": "养育方式",
    "medicalHistory": "病史",
}


class HealthState(TypedDict, total=False):
    """健康评估图状态"""

    user_id: int
    pet_id: int
    pet: Dict[str, Any]          # 宠物档案（含 health 字典）
    profile_text: str            # 整理后的档案文本
    missing: List[str]           # 缺失的关键字段
    report: Dict[str, Any]       # 评估报告（PetHealthReport.model_dump）


def _profile_lines(pet: Dict[str, Any]) -> tuple[List[str], List[str]]:
    """把宠物档案整理为文本行，并返回缺失的关键字段"""
    lines: List[str] = []
    missing: List[str] = []
    name = (pet.get("name") or "").strip()
    species = (pet.get("species") or "").strip()
    breed = (pet.get("breed") or "").strip()
    age = pet.get("age")
    age_text = pet.get("ageText")
    age_text = age_text.strip() if isinstance(age_text, str) else ""
    if name:
        lines.append(f"名字：{name}")
    if species:
        lines.append(f"物种：{species}")
    else:
        missing.append("物种")
    if breed:
        lines.append(f"品种：{breed}")
    # 年龄优先用前端按生日换算的精确文本（含月龄），缺失时回退数值年龄
    if age_text:
        lines.append(f"年龄：{age_text}")
    elif age is not None:
        lines.append(f"年龄：{age} 岁")
    else:
        missing.append("年龄")

    health = pet.get("health") or {}
    for key, label in _FIELD_LABELS.items():
        value = (health.get(key) or "").strip() if isinstance(health.get(key), str) else health.get(key)
        if value:
            lines.append(f"{label}：{value}")
        else:
            missing.append(label)
    return lines, missing


async def load_profile(state: HealthState) -> Dict[str, Any]:
    """整理宠物档案，标记缺失字段"""
    pet = state.get("pet") or {}
    lines, missing = _profile_lines(pet)
    return {"profile_text": "\n".join(lines), "missing": missing}


async def analyze(state: HealthState) -> Dict[str, Any]:
    """健康分析：真实模式走 LLM 结构化输出，mock 模式走规则"""
    if settings.is_mock:
        return {"report": _rule_report(state)}

    profile_text = state.get("profile_text", "")
    system_prompt = (
        "你是专业的宠物健康评估顾问。请根据宠物档案与健康信息，"
        "给出客观、保守的健康评估，并严格按结构化字段输出。\n"
        "评分标准（0-100）：信息完整且各项正常 85-100；有轻微需改善项 70-84；"
        "有明显风险项 50-69；存在严重/紧急风险 <50。\n"
        "注意：不得做医疗诊断，仅基于用户填写信息评估；有就医必要时在 risks 中提示。"
    )
    user_prompt = f"宠物档案：\n{profile_text or '（用户未填写任何档案信息）'}"
    try:
        report: PetHealthReport = await astructured_invoke(PetHealthReport, system_prompt, user_prompt)
        return {"report": report.model_dump()}
    except Exception:
        logger.warning("LLM 健康评估失败，降级为规则评估", exc_info=True)
        return {"report": _rule_report(state)}


async def persist(state: HealthState) -> Dict[str, Any]:
    """持久化评估报告（PG 不可用时静默跳过）"""
    report = state.get("report") or {}
    await pg_store.save_health_report(
        state.get("user_id", 0), state.get("pet_id", 0),
        int(report.get("score", 0)), report.get("level", "unknown"), report)
    return {}


def _rule_report(state: HealthState) -> Dict[str, Any]:
    """规则评估（mock 模式 / LLM 失败降级）：按档案完整度与风险词给分"""
    pet = state.get("pet") or {}
    health = pet.get("health") or {}
    missing = state.get("missing") or []
    medical_history = str(health.get("medicalHistory") or "")
    special_period = str(health.get("specialPeriod") or "")

    score = 90
    risks: List[str] = []
    suggestions: List[str] = []

    # 档案缺失扣分
    score -= min(20, len(missing) * 3)
    if missing:
        suggestions.append("建议补全宠物档案（" + "、".join(missing[:6]) + "），以便获得更准确的评估")

    if any(k in medical_history for k in ["病", "手术", "慢性", "过敏", "心脏", "肾"]):
        score -= 15
        risks.append(f"病史提示需关注：{medical_history}")
    if special_period and special_period not in ("无", "否", "-"):
        score -= 8
        risks.append(f"处于特殊时期（{special_period}），需加强观察与护理")
    if not str(health.get("vaccine") or "").strip():
        score -= 8
        suggestions.append("疫苗记录缺失，建议确认免疫程序是否完成并补打")
    if not str(health.get("deworming") or "").strip():
        score -= 6
        suggestions.append("驱虫记录缺失，建议按周期进行体内外驱虫")

    score = max(0, min(100, score))
    if score >= 85:
        level = "excellent"
    elif score >= 70:
        level = "good"
    elif score >= 50:
        level = "fair"
    else:
        level = "warning"

    return PetHealthReport(
        score=score,
        level=level,
        summary=f"基于当前档案信息，{pet.get('name') or '你的宠物'}整体状况评估为{'优秀' if level=='excellent' else '良好' if level=='good' else '一般' if level=='fair' else '需关注'}。",
        risks=risks,
        suggestions=suggestions or ["保持规律喂养与充足运动，定期体检"],
        care_plan=[
            "保持定时定量喂养，保证充足饮水",
            "每周观察体重与精神状态并记录",
            "按免疫与驱虫周期执行并留存记录",
        ],
        reminders=[
            {"type": "疫苗", "advice": "确认疫苗是否在有效期内，到期及时加强", "urgency": "medium"},
            {"type": "驱虫", "advice": "体内外驱虫建议每 3 个月 / 每月执行", "urgency": "medium"},
            {"type": "体检", "advice": "建议每年至少一次全面体检", "urgency": "low"},
        ],
        disclaimer="本评估基于用户填写信息由 AI 生成，不能替代兽医面诊，如有异常请及时就医。",
    ).model_dump()


_graph = None


def get_health_graph():
    """构建并缓存健康评估图"""
    global _graph
    if _graph is not None:
        return _graph
    builder = StateGraph(HealthState)
    builder.add_node("load_profile", load_profile)
    builder.add_node("analyze", analyze)
    builder.add_node("persist", persist)
    builder.add_edge(START, "load_profile")
    builder.add_edge("load_profile", "analyze")
    builder.add_edge("analyze", "persist")
    builder.add_edge("persist", END)
    _graph = builder.compile()
    return _graph


async def assess(user_id: int, pet: Dict[str, Any]) -> Dict[str, Any]:
    """执行一次健康评估，返回报告 dict"""
    pet_id = pet.get("id") or 0
    try:
        pet_id = int(pet_id)
    except (TypeError, ValueError):
        pet_id = 0
    state: HealthState = {"user_id": user_id, "pet_id": pet_id, "pet": pet or {}}
    result = await get_health_graph().ainvoke(state)
    return result.get("report") or _rule_report(state)
