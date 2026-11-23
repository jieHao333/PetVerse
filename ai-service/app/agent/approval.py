"""敏感操作的人工审批（Human-in-the-loop）。

在工具执行到「真正产生副作用」之前挂起整张图，把「要做什么、对象是谁、金额多少」
交给前端弹出审批卡片；只有用户明确批准后，图才从挂起点继续执行。

工作原理：
    ``interrupt(payload)`` 会抛出一个内部控制流异常，LangGraph 捕获后把当前任务标记为
    「待恢复」并结束本次执行；``payload`` 随流事件下发，checkpoint 保存现场。
    前端审批后调用 ``POST /ai/chat/approve``，路由层用 ``Command(resume=decision)``
    重新进入图——**被中断的节点会从头重跑**，``interrupt()`` 这次直接返回审批结果。

因此有一条硬约束：**``interrupt()`` 之前不能有任何副作用**。本项目的敏感工具在挂起前
只做只读查询（查订单金额、算购物车总价），写请求全部在 ``interrupt()`` 之后。

降级：审批依赖 checkpointer 保存现场。没有可用 checkpointer 时 ``request_approval``
直接返回「不可用」，工具拒绝执行——**失败关闭**，绝不因为存不了现场就悄悄放行。
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from langgraph.types import interrupt

from app.config import settings

logger = logging.getLogger(__name__)

# 审批卡片里的条目：{"label": "应付金额", "value": "¥199.00"}
Summary = List[Dict[str, str]]


@dataclass(frozen=True)
class ApprovalOutcome:
    """一次审批的结果。

    Attributes:
        required: 该工具是否配置了人工审批。
        approved: 是否获得批准（``required=False`` 时恒为 True，表示可直接执行）。
        reason: 拒绝原因（用户填写，可能为空）。
        unavailable: 审批通道不可用（无 checkpointer）——此时 ``approved=False``，
            调用方应直接拒绝执行，不能降级为「自动通过」。
    """

    required: bool
    approved: bool
    reason: str = ""
    unavailable: bool = False

    @property
    def blocked(self) -> bool:
        """是否应阻止本次操作。"""
        return self.required and not self.approved


def guarded_tools() -> set[str]:
    """读取配置中需要人工审批的工具名集合。

    Returns:
        工具名集合；配置为空表示不需要任何审批。
    """
    raw = (settings.AGENT_APPROVAL_TOOLS or "").strip()
    return {name.strip() for name in raw.split(",") if name.strip()}


def is_guarded(tool_name: str) -> bool:
    """判断某个工具是否需要人工审批。

    Args:
        tool_name: 工具名（与 ``@tool`` 函数名一致）。

    Returns:
        True 表示需要审批。
    """
    return tool_name in guarded_tools()


def request_approval(state: Dict[str, Any], tool_name: str, title: str,
                     summary: Summary, resource: Optional[Any] = None) -> ApprovalOutcome:
    """在工具内请求人工审批；需要审批时挂起整张图。

    Args:
        state: 注入的图状态（读 ``approval_available``）。
        title: 审批卡片标题，如「发布动态」。
        summary: 卡片明细（label/value 列表），应包含金额、对象、数量等关键信息。
        resource: 关联资源 ID（订单 ID / 商品 ID 等），便于前端跳转。

    Returns:
        ``ApprovalOutcome``。``required=False`` 表示该工具无需审批，可直接执行；
        需要审批且被拒时 ``blocked=True``，调用方必须立即返回、不得发起写请求。

    注意：本函数会阻塞（挂起）当前图执行；恢复后**会再次执行到这里**，因此调用点之前
    不能有已发生的副作用。
    """
    if not is_guarded(tool_name):
        return ApprovalOutcome(required=False, approved=True)

    if not state.get("approval_available"):
        # 没有 checkpointer 就存不了中断现场，恢复时无从继续——只能拒绝。
        logger.warning("审批通道不可用，拒绝执行敏感操作: tool=%s user=%s", tool_name, state.get("user_id"))
        return ApprovalOutcome(required=True, approved=False, unavailable=True)

    decision = interrupt({
        "type": "approval",
        "action": tool_name,
        "title": title,
        "summary": summary,
        "resource": resource,
    })

    # 恢复后 interrupt 直接返回审批结果；结构异常一律视为拒绝（失败关闭）。
    if isinstance(decision, dict) and decision.get("approved") is True:
        logger.info("人工审批通过: tool=%s user=%s", tool_name, state.get("user_id"))
        return ApprovalOutcome(required=True, approved=True)
    reason = ""
    if isinstance(decision, dict):
        reason = str(decision.get("reason") or "").strip()
    logger.info("人工审批拒绝: tool=%s user=%s reason=%s", tool_name, state.get("user_id"), reason)
    return ApprovalOutcome(required=True, approved=False, reason=reason)
