"""自主 Agent 的状态定义。

职责：定义对话 AgentLoop 的图状态（TypedDict）与初始状态工厂。状态是「一轮对话」的
唯一事实来源——模型决策、工具结果、引用来源、护栏标记都挂在上面，并由 checkpointer
按 ``thread_id`` 持久化，下一轮自动恢复。

主要导出：
    - ``AgentState``     图状态定义
    - ``merge_knowledge`` 引用来源的合并 reducer
    - ``initial_state``  构造初始状态

外部依赖：``langchain_core.messages``、``langgraph.graph.message.add_messages``。

设计要点：
    1. ``messages`` 仍用 ``add_messages`` 且字段名不变——存量会话的 checkpoint 里只有这个
       通道，改名或换 reducer 会让老会话「失忆」。
    2. 其余字段全部 ``total=False``：节点只返回自己产出的键，未返回的保持原值。
    3. ``knowledge`` 用自定义 reducer 而不是覆盖：一次对话可能检索多次，引用要累积去重。
"""

from typing import Annotated, Any, Dict, List, TypedDict
from uuid import uuid4

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


def merge_knowledge(left: List[dict], right: List[dict]) -> List[dict]:
    """合并两批引用来源。

    LangGraph 的普通通道是「后写覆盖」，但引用来源需要累积：模型可能先查症状、再查护理，
    两次检索的片段都要出现在前端引用面板里。

    Args:
        left: 已累积的片段列表（LangGraph 传入的当前值，可能为 None）。
        right: 本次节点新增的片段列表。

    Returns:
        去重后的合并列表，保持「先到先得」的顺序（即引用编号的先后顺序）。

    Raises:
        无——结构异常的条目按内容哈希兜底，不会中断图执行。
    """
    merged: List[dict] = []
    seen = set()
    for item in list(left or []) + list(right or []):
        if not isinstance(item, dict):
            continue
        # chunk_id 是知识库片段的稳定标识；缺失时退化为「文档 + 小节 + 内容前缀」三元组。
        key = item.get("chunk_id") or (
            item.get("doc_id"),
            item.get("section"),
            str(item.get("content") or "")[:64],
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


class AgentState(TypedDict, total=False):
    """对话 AgentLoop 的图状态。

    Attributes:
        messages: 完整消息流（用户 / AI 含 tool_calls / Tool 结果 / 最终答案），
            由 ``add_messages`` 追加并按 id 去重。
        query: 本轮用户问题原文，供日志与 mock 检索使用。
        run_id: 本轮对话标识。写操作用它做幂等去重（同一轮内相同操作只执行一次），
            重试时沿用同一个 ``run_id``，避免把加购重复提交。
        pet: 宠物档案字典（工具与 Prompt 共用）。
        user_id: 当前登录用户 ID，工具据此做数据隔离。
        attachments: 本轮附件列表（图片 / 音频 / 视频）。
        system_prompt: ``prepare`` 组装的系统提示，``agent`` 每轮复用。
        memory_text: 召回到的长期记忆（保留下来便于排障与观测）。
        medical: 是否命中医疗 / 急症护栏。
        approval_available: 人工审批通道是否可用（图是否带 checkpointer）。
            敏感工具据此决定「挂起等审批」还是「直接拒绝执行」，不能降级为自动放行。
        needs_evidence: 是否属于需要知识依据的问题（仅用于强化提示词，不做流程分支）。
        step: 已执行的工具轮次，用于预算控制。
        knowledge: 本轮引用来源，编号全局递增。
        reply: 最终回答文本，供路由层落库。
        vision: 是否多模态只读轮次（视觉模型通常不支持工具调用）。
    """

    messages: Annotated[List[AnyMessage], add_messages]
    query: str
    run_id: str
    pet: Dict[str, Any]
    user_id: int
    attachments: List[Dict[str, Any]]
    system_prompt: str
    memory_text: str
    medical: bool
    approval_available: bool
    needs_evidence: bool
    step: int
    knowledge: Annotated[List[dict], merge_knowledge]
    reply: str
    vision: bool


def initial_state(query: str, pet: Dict[str, Any], user_id: int,
                  messages: List[AnyMessage],
                  attachments: List[Dict[str, Any]] | None = None) -> AgentState:
    """构造一轮对话的初始状态。

    显式列全字段（含空默认值）：节点读取未初始化字段时会拿到 None，统一成空容器
    可以省掉各节点里的判空分支。

    ``run_id`` 在每次调用时生成：路由层在「首帧前失败」时会用同一个 state 重跑一次图，
    ``run_id`` 不变，写操作因此能识别出「这次调用刚才已经执行过」。

    Args:
        query: 本轮用户问题（纯语音时为转写拼接结果）。
        pet: 宠物档案字典。
        user_id: 用户 ID。
        messages: 已拼接历史的输入消息（含本轮 HumanMessage）。
        attachments: 附件列表。

    Returns:
        可直接传给 ``graph.ainvoke`` / ``graph.astream`` 的状态字典。

    Raises:
        无。
    """
    return {
        "query": query,
        # 每次构造初始状态生成一次；路由层重试会复用同一个 state，因此 run_id 不变。
        "run_id": uuid4().hex,
        "pet": pet or {},
        "user_id": user_id,
        "attachments": attachments or [],
        "messages": messages or [],
        "system_prompt": "",
        "memory_text": "",
        "medical": False,
        "approval_available": False,
        "needs_evidence": False,
        "step": 0,
        "knowledge": [],
        "reply": "",
        "vision": False,
    }
