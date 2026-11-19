"""养宠顾问对话的自主 AgentLoop。

职责：以「模型 + 工具」的最小循环替代改造前的固定编排图。图里只有五个节点，
其中真正做决策的只有 ``agent`` 一个——调不调工具、调哪个、调几次、什么时候停，
全部由模型在 ``agent`` 节点里自行判断；其余节点负责上下文准备、工具执行与结果收口。

拓扑（与 LangChain 1.0 ``create_agent`` 的 model ⇄ tools 循环同构）::

    START → prepare → agent ─┬─(有 tool_calls 且未超预算)→ act → agent
                             ├─(达到 AGENT_MAX_STEPS)────→ conclude → finalize
                             └─(无 tool_calls)──────────→ finalize → END

主要导出：
    - ``get_chat_agent``     取得（并编译一次）图实例
    - ``initial_state``      构造初始状态（转出，便于路由层单点导入）
    - ``preview_knowledge``  mock 模式下的引用预览（不跑模型）

外部依赖：``langgraph``（StateGraph / ToolNode / add_messages）、``app.agent.*``、
``app.llm``、``app.kb``、``app.checkpoint`` / ``app.memstore``。

使用示例：
    from app.agent import get_chat_agent, initial_state

    graph = get_chat_agent()
    state = initial_state(query="猫咪打疫苗要注意什么", pet=pet, user_id=42, messages=[])
    result = await graph.astream(state, config=checkpoint.thread_config(42, 7),
                                 stream_mode=["updates", "messages"])

降级策略：
    - 长期记忆读失败 → 本轮不注入记忆，不影响对话；
    - 工具参数错误 → ToolNode 转成错误 ToolMessage 交回模型自行纠正；
    - 知识检索不可用 → ``RagUnavailable`` 向上抛，由路由层转 error 帧（保持改造前的语义：
      知识问答宁可明确失败，也不要无依据作答）。
"""

import logging
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4

from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app import checkpoint, memstore
from app.agent.policy import classify_hints, repair_dangling_tool_calls, trim_history
from app.agent.prompt import build_agent_prompt
from app.agent.state import AgentState, initial_state
from app.agent.tools import ToolContext, build_tools, enabled_groups, has_write_tools
from app.config import settings
from app.kb import search as kb_search
from app.llm import get_client, get_vision_client
from app.media import KIND_IMAGE
from app.schemas import PetInfo

logger = logging.getLogger(__name__)

# 对外公开的入口：``initial_state`` 与图一起导出，调用方不必再记状态定义在哪个模块。
__all__ = ["approval_available", "get_chat_agent", "initial_state", "preview_knowledge", "should_act"]

# 节点名集中定义：路由层按节点名过滤流式 token 与引用更新，改名必须同步 chat.py。
NODE_PREPARE = "prepare"
NODE_AGENT = "agent"
NODE_ACT = "act"
NODE_CONCLUDE = "conclude"
NODE_FINALIZE = "finalize"

# 预算耗尽时的收口指令：必须显式禁止再调工具，否则模型会继续返回 tool_calls，
# 而历史里没有对应的 ToolMessage，下一轮请求会被接口判为非法。
_CONCLUDE_HINT = ("\n\n【重要】本轮工具调用次数已达上限，禁止再发起任何工具调用，"
                  "请直接基于当前已有信息给出最终答复；信息不足时如实说明，不要编造。")

# 预算耗尽时补写的工具结果文案（模型可见，需说明「未执行」而不是「失败」）。
_SKIPPED_TOOL_NOTE = "本轮工具调用次数已达上限，该调用未执行。请基于已有信息作答。"


def _pet_info(state: AgentState) -> PetInfo:
    """从状态里还原宠物档案。

    Args:
        state: 图状态（``pet`` 为前端透传的字典）。

    Returns:
        校验通过的 ``PetInfo``；字段非法时退化为空档案（档案不合法不该让整轮失败）。

    Raises:
        无。
    """
    try:
        return PetInfo(**(state.get("pet") or {}))
    except Exception:
        logger.warning("宠物档案解析失败，按空档案处理", exc_info=True)
        return PetInfo()


def _last_ai_message(messages: Sequence[AnyMessage]) -> Optional[AIMessage]:
    """取最近一条 AI 消息。

    Args:
        messages: 消息序列。

    Returns:
        最后一条 ``AIMessage``；没有时返回 None。

    Raises:
        无。
    """
    for msg in reversed(list(messages or [])):
        if isinstance(msg, AIMessage):
            return msg
    return None


async def _recall_memory(state: AgentState, config: RunnableConfig) -> str:
    """召回「用户级 + 当前宠物级」长期记忆。

    Args:
        state: 图状态（读 ``pet`` / ``user_id``）。
        config: 运行配置，必须携带 ``store``（编译期注入），否则直接跳过。

    Returns:
        每行一条、形如 ``- [用户] 偏好…`` 的文本；未开启或读取失败时为空串。

    Raises:
        无——记忆是增强项，读失败只降级为空。
    """
    if not settings.MEMORY_ENABLED:
        return ""
    store = (config or {}).get("store") if isinstance(config, dict) else None
    if store is None:
        return ""

    pet_dict = state.get("pet") or {}
    pet_id = pet_dict.get("id") or 0
    pet_name = (pet_dict.get("name") or "").strip()
    user_id = state.get("user_id", 0)

    lines: List[str] = []
    try:
        # 先用户级再宠物级：用户偏好通常比单只宠物的细节更「全局」，放前面更醒目。
        for ns, label in (
            (memstore.user_ns(user_id), "用户"),
            (memstore.pet_ns(user_id, pet_id), pet_name or "宠物"),
        ):
            items = await store.asearch(ns, limit=settings.MEMORY_MAX_ITEMS)
            for item in items:
                content = str((item.value or {}).get("content", "")).strip()
                if content:
                    lines.append(f"- [{label}] {content}")
                # 两级共享同一个总量上限：避免记忆过多把档案与知识挤出上下文窗口。
                if len(lines) >= settings.MEMORY_MAX_ITEMS:
                    break
            if len(lines) >= settings.MEMORY_MAX_ITEMS:
                break
    except Exception:
        logger.warning("读取长期记忆失败，本轮不注入长期记忆", exc_info=True)
        return ""
    return "\n".join(lines)


def _has_image(attachments: Sequence[Dict[str, Any]]) -> bool:
    """判断本轮是否带可识别的图片。

    Args:
        attachments: 附件列表。

    Returns:
        True 表示至少有一张带 URL 的图片。

    Raises:
        无。
    """
    for att in attachments or []:
        if att.get("type") == KIND_IMAGE and (att.get("url") or "").strip():
            return True
    return False


async def prepare(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """上下文准备节点（确定性，不调用模型）。

    职责边界：只准备「每一轮都必然需要」的上下文——宠物档案、长期记忆、医疗护栏、
    系统提示。**不预取知识检索结果**，那是模型在 ``agent`` 节点里自行决定的事。

    例外是多模态轮次：视觉模型通常不支持工具调用，因此图片轮次改为「预检索 + 单轮直答」，
    否则用户发一张皮肤患处照片提问，模型将拿不到任何知识依据。

    Args:
        state: 图状态。
        config: 运行配置（用于取 store）。

    Returns:
        需要写回的字段：档案、记忆、护栏标记、系统提示、视觉标记、初始步数。

    Raises:
        ``kb.search.RagUnavailable``：视觉轮次预检索失败时向上抛，由路由层转 error 帧。
    """
    query = (state.get("query") or "").strip()
    pet = _pet_info(state)
    memory_text = await _recall_memory(state, config)
    medical, needs_evidence = classify_hints(query)

    attachments = state.get("attachments") or []
    vision = settings.vision_enabled and _has_image(attachments)

    knowledge: List[dict] = []
    knowledge_text = ""
    if vision and settings.RAG_ENABLED and query:
        # 视觉轮次不绑工具，知识只能提前取好；检索失败照旧向上抛（与文本轮次同语义）。
        hits = await kb_search.retrieve(query, pet_species=pet.species or [])
        knowledge = hits or []
        knowledge_text = kb_search.format_context(knowledge)

    # 提示词里的能力描述必须与本次实际绑定的工具一致：多写一段「发布会审批」，
    # 模型就会去调一个不存在的工具，然后回复「我没有这个权限」。
    groups = enabled_groups()
    system_prompt = build_agent_prompt(
        pet,
        memory_context=memory_text,
        knowledge_context=knowledge_text,
        medical=medical,
        needs_evidence=needs_evidence,
        tools_enabled=not vision,
        write_actions=has_write_tools(groups) and not vision,
        publish_action="publish" in groups and not vision,
        max_steps=settings.AGENT_MAX_STEPS,
    )
    logger.info(
        "Agent 上下文就绪: user_id=%s vision=%s medical=%s needs_evidence=%s memory_lines=%s",
        state.get("user_id", 0), vision, medical, needs_evidence,
        len(memory_text.splitlines()) if memory_text else 0,
    )
    return {
        "pet": pet.model_dump(),
        "memory_text": memory_text,
        "medical": medical,
        "needs_evidence": needs_evidence,
        "vision": vision,
        "system_prompt": system_prompt,
        "knowledge": knowledge,
        "step": 0,
        # 审批靠 checkpointer 保存现场；图若编译成无记忆模式，敏感工具必须拒绝执行。
        "approval_available": approval_available(),
    }


def _model_for(state: AgentState):
    """按本轮是否多模态选择模型客户端。

    Args:
        state: 图状态（读 ``vision``）。

    Returns:
        对话模型或视觉模型客户端。

    Raises:
        ImportError: 未安装 ``langchain-openai`` 时抛出。
    """
    return get_vision_client() if state.get("vision") else get_client()


async def call_model(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """模型节点：把决策权交给模型。

    每次进入本节点，模型会看到「系统提示 + 裁剪后的完整消息流（含之前的工具结果）」，
    然后自行选择：继续调用工具（返回 ``tool_calls``），或给出最终答复（返回文本）。

    Args:
        state: 图状态。
        config: 运行配置（LangGraph 注入，本节点未直接使用）。

    Returns:
        ``{"messages": [AIMessage]}``：模型本轮产出，可能是工具调用也可能是答案。

    Raises:
        Exception: 模型调用失败时向上抛，由路由层决定重试或返回 error 帧。
    """
    if settings.is_mock:
        # mock 模式不跑图（路由层直接走模拟流），这里仅作防御。
        return {"messages": []}

    history = trim_history(state.get("messages") or [], settings.HISTORY_MAX_MESSAGES)
    # 用户中止可能留下「AI 请求了工具但没有结果」的半截调用，送模型前补齐。
    history = repair_dangling_tool_calls(history)
    messages: List[AnyMessage] = [SystemMessage(content=state.get("system_prompt") or "")]
    messages.extend(history)

    model = _model_for(state)
    if not state.get("vision"):
        tools = build_tools(ToolContext(user_id=state.get("user_id", 0)))
        if tools:
            model = model.bind_tools(tools)

    resp = await model.ainvoke(messages)
    tool_names = [tc.get("name") for tc in (getattr(resp, "tool_calls", None) or [])]
    if tool_names:
        logger.info("Agent 决定调用工具: %s", tool_names)
    else:
        logger.info("Agent 决定直接作答（长度 %s）", len(resp.text or ""))
    return {"messages": [resp]}


async def run_tools(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """工具执行节点。

    用 LangGraph 官方的 ``ToolNode`` 而不是自己写循环：它已经处理好并行调用、
    参数校验失败的错误消息回填、以及 ``InjectedState`` 的状态注入。

    工具若声明了 ``response_format="content_and_artifact"``（知识检索就是这样），
    模型只看 content，结构化命中放在 ``ToolMessage.artifact`` 里，本节点把它收集到
    ``knowledge`` 通道，供路由层推送引用面板。

    Args:
        state: 图状态。
        config: 运行配置。

    Returns:
        ``{"messages": [...], "knowledge": [...], "step": n+1}``。

    Raises:
        ``kb.search.RagUnavailable``：知识库不可用时向上抛（工具执行期异常不被 ToolNode 吞掉）。
    """
    step = int(state.get("step") or 0) + 1
    tools = build_tools(ToolContext(user_id=state.get("user_id", 0)))
    if not tools:
        # 理论上不会发生（没有工具时模型不会返回 tool_calls），防御性收口，避免死循环。
        logger.warning("收到工具调用但无可用工具，跳过执行")
        return {"step": step}

    node = ToolNode(tools)
    result = await node.ainvoke(dict(state), config)
    produced: List[AnyMessage] = list(result.get("messages") or [])

    refs: List[dict] = []
    for msg in produced:
        artifact = getattr(msg, "artifact", None)
        if isinstance(artifact, list):
            refs.extend(item for item in artifact if isinstance(item, dict))

    logger.info("工具执行完成: step=%s 结果条数=%s 新增引用=%s", step, len(produced), len(refs))
    return {"messages": produced, "knowledge": refs, "step": step}


def _pending_tool_calls(state: AgentState) -> List[dict]:
    """取出「已请求但尚未执行」的工具调用。

    Args:
        state: 图状态。

    Returns:
        工具调用字典列表；没有时为空列表。

    Raises:
        无。
    """
    last = _last_ai_message(state.get("messages") or [])
    if last is None:
        return []
    return list(getattr(last, "tool_calls", None) or [])


async def conclude(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """预算耗尽时的强制收口语节点。

    模型可能一直想调工具。到上限后不能直接丢一个「有 tool_calls 却没有 ToolMessage」的
    历史给下一轮——OpenAI 兼容接口会报 400。这里先为每个未执行的调用补一条说明性的
    ToolMessage（模型自己也知道该调用了没执行），再不绑工具地要一次最终答复。

    Args:
        state: 图状态。
        config: 运行配置（未直接使用）。

    Returns:
        ``{"messages": [ToolMessage..., AIMessage]}``。

    Raises:
        Exception: 模型调用失败时向上抛。
    """
    pending = _pending_tool_calls(state)
    patches = [
        ToolMessage(
            content=_SKIPPED_TOOL_NOTE,
            # tool_call_id 必须回填原 id，否则历史不合法；异常数据用随机 id 兜底。
            tool_call_id=str(tc.get("id") or uuid4().hex),
            name=str(tc.get("name") or "tool"),
        )
        for tc in pending
    ]
    logger.info("工具预算耗尽，强制收敛: 未执行调用=%s", [tc.get("name") for tc in pending])

    prompt = (state.get("system_prompt") or "") + _CONCLUDE_HINT
    history = trim_history(list(state.get("messages") or []) + patches,
                           settings.HISTORY_MAX_MESSAGES)
    history = repair_dangling_tool_calls(history)
    messages: List[AnyMessage] = [SystemMessage(content=prompt)]
    messages.extend(history)

    resp = await get_client().ainvoke(messages)
    return {"messages": patches + [resp]}


async def finalize(state: AgentState, config: RunnableConfig) -> Dict[str, Any]:
    """收口节点：把最终答复提取到 ``reply``，供路由层落库。

    Args:
        state: 图状态。
        config: 运行配置（未直接使用）。

    Returns:
        ``{"reply": str}``。空回复保持空串（与改造前一致，路由层按原逻辑处理）。

    Raises:
        无。
    """
    last = _last_ai_message(state.get("messages") or [])
    reply = (last.text or "").strip() if last else ""
    if not reply:
        logger.warning("Agent 未产出有效答复: user_id=%s", state.get("user_id", 0))
    return {"reply": reply}


def should_act(state: AgentState) -> str:
    """条件边：决定循环继续、收敛还是结束。

    Args:
        state: 图状态。

    Returns:
        ``"act"`` / ``"conclude"`` / ``"finalize"`` 之一。

    Raises:
        无。
    """
    pending = _pending_tool_calls(state)
    if not pending:
        # 模型没有请求工具，说明它认为信息够了——这条 AI 消息就是答案。
        return NODE_FINALIZE
    if int(state.get("step") or 0) >= max(1, int(settings.AGENT_MAX_STEPS)):
        return NODE_CONCLUDE
    return NODE_ACT


_graph = None
# 当前编译出的图是否带 checkpointer。人工审批靠它保存中断现场，无 saver 时必须失败关闭。
_graph_has_saver = False


def approval_available() -> bool:
    """人工审批通道是否可用。

    Returns:
        True 表示图编译时注入了 checkpointer，``interrupt`` 能挂起并恢复；False 表示
        没有可恢复的现场，敏感工具应直接拒绝执行。

    Raises:
        无。
    """
    return _graph_has_saver


def get_chat_agent():
    """取得（首次调用时编译）对话 Agent 图。

    编译成本不低，且图本身无状态（状态在 checkpoint 里），因此按进程缓存一份。

    Returns:
        已编译的 ``CompiledStateGraph``，注入了 checkpointer（对话记忆）与 store（长期记忆）。

    Raises:
        无——checkpointer / store 为 None 时图退化为无记忆模式，仍可正常对话。
    """
    global _graph, _graph_has_saver
    if _graph is not None:
        return _graph

    builder = StateGraph(AgentState)
    builder.add_node(NODE_PREPARE, prepare)
    builder.add_node(NODE_AGENT, call_model)
    builder.add_node(NODE_ACT, run_tools)
    builder.add_node(NODE_CONCLUDE, conclude)
    builder.add_node(NODE_FINALIZE, finalize)

    builder.add_edge(START, NODE_PREPARE)
    builder.add_edge(NODE_PREPARE, NODE_AGENT)
    # 唯一的条件边：循环与否由模型产出的 tool_calls 决定。
    builder.add_conditional_edges(
        NODE_AGENT,
        should_act,
        {NODE_ACT: NODE_ACT, NODE_CONCLUDE: NODE_CONCLUDE, NODE_FINALIZE: NODE_FINALIZE},
    )
    builder.add_edge(NODE_ACT, NODE_AGENT)
    builder.add_edge(NODE_CONCLUDE, NODE_FINALIZE)
    builder.add_edge(NODE_FINALIZE, END)

    saver = checkpoint.get_saver()
    _graph_has_saver = saver is not None
    _graph = builder.compile(checkpointer=saver, store=memstore.get_store())
    logger.info("AgentLoop 图已编译: max_steps=%s groups=%s approval_available=%s",
                settings.AGENT_MAX_STEPS, settings.AGENT_TOOL_GROUPS, _graph_has_saver)
    return _graph


async def preview_knowledge(state: AgentState) -> List[dict]:
    """mock 模式下的引用预览。

    mock 模式不调用模型（也就没有工具决策），但前端仍应看到「这个问题能命中哪些资料」。
    这里按与正式链路一致的关键词判定做一次直达检索，失败只记日志。

    Args:
        state: 图状态（读 ``query`` / ``pet``）。

    Returns:
        知识片段列表；不需要检索或检索失败时为空列表。

    Raises:
        无——mock 是演示路径，任何异常都不该让模拟对话中断。
    """
    query = (state.get("query") or "").strip()
    if not settings.RAG_ENABLED or not query:
        return []
    _, needs_evidence = classify_hints(query)
    if not needs_evidence:
        return []
    try:
        pet = _pet_info(state)
        return await kb_search.retrieve(query, pet_species=pet.species or []) or []
    except Exception:
        logger.warning("mock 模式预检索失败（忽略）", exc_info=True)
        return []
