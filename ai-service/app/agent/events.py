"""AgentLoop 与 SSE 接入层之间的事件适配。

把 LangGraph 的原始流事件翻译成路由层关心的两件事——「哪些 token 该下发给用户」
与「这一批更新里有没有新的引用来源」。把这些判断收敛到一处，是为了让路由层不必知道
图里有哪些节点、哪个节点会产出引用。

设计要点：AgentLoop 里每一次模型调用都会产出 token（决策轮、改写轮、收口轮），
但只有 ``agent`` 的「非工具调用轮」和 ``conclude`` 的收口轮才是答案。按节点名 +
是否携带 ``tool_call_chunks`` 双重过滤，比事后在文本里找重复内容可靠得多。
"""

import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessageChunk

from app.agent.graph import NODE_ACT, NODE_AGENT, NODE_CONCLUDE, NODE_PREPARE

logger = logging.getLogger(__name__)

# 允许把 token 下发给用户的节点：agent 的答案轮 + 预算耗尽时的收口轮。
_ANSWER_NODES = frozenset({NODE_AGENT, NODE_CONCLUDE})
# 可能产出引用来源的节点：工具执行节点（正常检索）与准备节点（多模态预检索）。
_REF_NODES = (NODE_ACT, NODE_PREPARE)


def extract_refs(payload: Any) -> Optional[List[dict]]:
    """从一次 ``updates`` 事件里提取新增的引用来源。

    Args:
        payload: ``graph.astream(..., stream_mode="updates")`` 产出的字典
            （形如 ``{"act": {"messages": [...], "knowledge": [...]}}``）。

    Returns:
        新增片段列表；本批更新里没有引用时返回 None（调用方据此决定是否推 refs 帧）。
    """
    if not isinstance(payload, dict):
        return None
    for node in _REF_NODES:
        update = payload.get(node)
        if not isinstance(update, dict):
            continue
        refs = update.get("knowledge")
        if refs:
            return list(refs)
    return None


def extract_interrupt(payload: Any) -> Optional[Dict[str, Any]]:
    """从一次 ``updates`` 事件里取出人工审批请求。

    Args:
        payload: ``stream_mode="updates"`` 的载荷。图因工具内 ``interrupt()`` 挂起时，
            载荷形如 ``{"__interrupt__": (Interrupt(value={...}, id="..."),)}``。

    Returns:
        审批请求：``{"approvalId", "type", "action", "title", "summary", "resource"}``；
        本次更新不是审批中断时返回 None。
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("__interrupt__")
    if not raw:
        return None
    first = raw[0] if isinstance(raw, (list, tuple)) else raw
    value = getattr(first, "value", None)
    if not isinstance(value, dict) or value.get("type") != "approval":
        logger.warning("收到未知类型的中断，已忽略: %r", value)
        return None
    return {"approvalId": str(getattr(first, "id", "") or ""), **value}


def is_answer_chunk(chunk: Any, meta: Optional[Dict[str, Any]]) -> bool:
    """判断一条流式片段是否是要下发给用户的答案文本。

    Args:
        chunk: ``stream_mode="messages"`` 产出的消息片段。
        meta: 该片段的元数据（含 ``langgraph_node``）。

    Returns:
        True 表示这是答案节点的正文片段，应当转成 SSE ``delta``。

    说明：模型决定调用工具的那一轮通常只产出 ``tool_call_chunks``、正文为空；
    少数模型会先说一句「我查一下」再发起调用，这类文本会被 ``tool_call_chunks``
    判定挡掉，避免用户看到「我查一下」之后答案又从零开始。
    """
    if not isinstance(chunk, AIMessageChunk):
        return False
    if (meta or {}).get("langgraph_node") not in _ANSWER_NODES:
        return False
    if getattr(chunk, "tool_call_chunks", None):
        return False
    return bool(chunk.text)
