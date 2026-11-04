"""宠物健康评估编排图。

职责：把宠物档案整理成评估输入 → 由 LLM 产出结构化报告（评分 / 风险 / 建议 / 计划 / 提醒）
→ 写入 ``pet_health_report`` 供历史回看。LLM 不可用或 mock 模式下走规则评估。

主要导出：
    - ``HealthState``        图状态定义
    - ``assess``             对外入口（一次性评估）
    - ``get_health_graph``   取得（并编译一次）图实例

外部依赖：``app.llm``、``app.pg_store``、``app.schemas.PetHealthReport``。

使用示例：
    from app.graph.health_graph import assess

    report = await assess(user_id=42, pet={"id": 7, "name": "豆豆", "species": "猫",
                                          "health": {"weight": "4.2kg"}})

合规约束：评估只能「基于用户填写的信息」，不做医疗诊断；报告固定带免责声明。
这条约束同时体现在提示词和 ``PetHealthReport`` 的默认值里。
"""

import logging
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from app import pg_store
from app.config import settings
from app.llm import astructured_invoke
from app.schemas import PetHealthReport

logger = logging.getLogger(__name__)

# 健康档案字段 → 中文标签。顺序即提示词里的展示顺序：先基础体征，再防疫，最后病史。
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
    """健康评估图状态。"""

    user_id: int
    pet_id: int
    pet: Dict[str, Any]
    profile_text: str
    missing: List[str]
    report: Dict[str, Any]


def _profile_lines(pet: Dict[str, Any]) -> tuple[List[str], List[str]]:
    """把宠物档案整理成「标签：值」行，并收集缺失项。

    缺失项不是可有可无的信息：它们既是规则评估的扣分依据，也会写进建议里
    「请补全档案」，让用户知道评估为何偏保守。

    Args:
        pet: 宠物档案字典。

    Returns:
        ``(行列表, 缺失字段标签列表)``。

    Raises:
        无。
    """
    lines: List[str] = []
    missing: List[str] = []
    name = (pet.get("name") or "").strip()
    species = (pet.get("species") or "").strip()
    breed = (pet.get("breed") or "").strip()
    age = pet.get("age")
    age_text = pet.get("ageText")
    # ageText 可能是任意类型（前端表单），只在确实是字符串时才 strip。
    age_text = age_text.strip() if isinstance(age_text, str) else ""
    if name:
        lines.append(f"名字：{name}")
    if species:
        lines.append(f"物种：{species}")
    else:
        missing.append("物种")
    if breed:
        lines.append(f"品种：{breed}")
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
    """档案整理节点。

    Args:
        state: 图状态（读 ``pet``）。

    Returns:
        ``{"profile_text": str, "missing": [...]}``。

    Raises:
        无。
    """
    pet = state.get("pet") or {}
    lines, missing = _profile_lines(pet)
    return {"profile_text": "\n".join(lines), "missing": missing}


async def analyze(state: HealthState) -> Dict[str, Any]:
    """评估节点：LLM 结构化生成，失败降级为规则评估。

    Args:
        state: 图状态（读 ``profile_text``）。

    Returns:
        ``{"report": {...}}``，与 ``PetHealthReport`` 同构。

    Raises:
        无。
    """
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
    """落库节点：把报告写入历史表。

    Args:
        state: 图状态（读 ``user_id`` / ``pet_id`` / ``report``）。

    Returns:
        空字典（该节点不修改状态）。

    Raises:
        无——``pg_store.save_health_report`` 内部已降级。
    """
    report = state.get("report") or {}
    await pg_store.save_health_report(
        state.get("user_id", 0), state.get("pet_id", 0),
        int(report.get("score", 0)), report.get("level", "unknown"), report)
    return {}


def _rule_report(state: HealthState) -> Dict[str, Any]:
    """规则版健康评估（mock / LLM 失败时使用）。

    打分逻辑：满分 90 起扣，档案缺失按项扣分（最多 20），病史 / 特殊时期 / 疫苗 / 驱虫
    各扣固定分。设计上偏保守——宁可低分提醒用户补全信息，也不要给出虚高的「优秀」。

    Args:
        state: 图状态（读 ``pet`` / ``missing``）。

    Returns:
        ``PetHealthReport`` 的字典形式。

    Raises:
        无。
    """
    pet = state.get("pet") or {}
    health = pet.get("health") or {}
    missing = state.get("missing") or []
    medical_history = str(health.get("medicalHistory") or "")
    special_period = str(health.get("specialPeriod") or "")

    # 起始 90 而非 100：档案信息永远不完整，留出「无缺失也拿不到满分」的语义。
    score = 90
    risks: List[str] = []
    suggestions: List[str] = []

    # 缺失项每项 -3，最多 -20：缺得再多也不该把分数压到不及格区间，
    # 那会让「信息没填」被误读为「宠物有病」。
    score -= min(20, len(missing) * 3)
    if missing:
        suggestions.append("建议补全宠物档案（" + "、".join(missing[:6]) + "），以便获得更准确的评估")

    if any(k in medical_history for k in ["病", "手术", "慢性", "过敏", "心脏", "肾"]):
        score -= 15
        risks.append(f"病史提示需关注：{medical_history}")
    # 「无 / 否 / -」是前端表单的空值写法，不算特殊时期。
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
    # 区间划分与提示词里的评分标准保持一致，避免规则与 LLM 两条路径的口径不同。
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
    """取得（首次调用时编译）健康评估图。

    Returns:
        已编译的图（单轮无状态，不需要 checkpointer）。

    Raises:
        无。
    """
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
    """对一只宠物做健康评估。

    Args:
        user_id: 用户 ID。
        pet: 宠物档案（含 ``health``）。

    Returns:
        评估报告字典；即使落库失败也会正常返回。

    Raises:
        Exception: 图执行异常向上抛，由路由层转成统一错误报文。
    """
    pet_id = pet.get("id") or 0
    # 宠物 ID 可能来自前端（字符串 / 空值），非法时按 0 处理，不能让类型错误阻断评估。
    try:
        pet_id = int(pet_id)
    except (TypeError, ValueError):
        pet_id = 0
    state: HealthState = {"user_id": user_id, "pet_id": pet_id, "pet": pet or {}}
    result = await get_health_graph().ainvoke(state)
    # 兜底：图正常结束时 report 一定存在，这里只是防御性返回，避免接口返回 null。
    return result.get("report") or _rule_report(state)
