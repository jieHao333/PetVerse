"""养宠顾问对话编排图。

职责：把一轮对话拆成可组合的节点并编排执行——
``意图识别 →（并行扇出）知识检索 / 工具调用 → 汇合 → 长期记忆召回 → Prompt 组装 → 生成``。
知识类与工具类意图可同时命中（用 ``Send`` 并行），互不阻塞。

主要导出：
    - ``ChatState``        图状态定义
    - ``get_chat_graph``   取得（并编译一次）图实例
    - ``initial_state``    构造初始状态
    - 各节点函数（``classify_intent`` / ``retrieve_knowledge`` / ``tool_action`` /
      ``recall_memory`` / ``compose`` / ``generate``），供单测或调试直接调用

外部依赖：``langgraph``（StateGraph / Send / add_messages）、``app.llm``、``app.kb.search``、
``app.persona``、``app.tools``、``app.checkpoint`` / ``app.memstore``。

使用示例：
    from app.graph.chat_graph import get_chat_graph, initial_state

    graph = get_chat_graph()
    state = initial_state(query="猫咪打疫苗要注意什么", pet=pet, user_id=42, messages=[])
    result = await graph.ainvoke(state, config=checkpoint.thread_config(42, 7))

降级策略：意图识别 / 查询改写 / 工具调用失败都退回规则或空上下文，只有生成节点的异常
向上抛出（由路由层转成 error 帧）。
"""

import logging
from typing import Annotated, Any, Dict, List, Optional, Sequence, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send

from app import checkpoint, memstore, persona
from app.config import settings
from app.kb import search as kb_search
from app.llm import astructured_invoke, get_client, get_vision_client
from app.media import KIND_IMAGE
from app.schemas import IntentResult, PetInfo, SearchQuery
from app.tools import build_tools

logger = logging.getLogger(__name__)

# 这些意图需要知识库支撑；chitchat / tool_query 不触发检索。
_KB_INTENTS = {"knowledge", "health", "medical_urgent"}

# 规则兜底用的关键词表。模型不可用（mock / 超时 / 结构化输出失败）时靠它分类，
# 因此词表要覆盖真实问法中的高频说法，宁可多召回也不要漏掉急症。
_URGENT_WORDS = ["急救", "抽搐", "呼吸困难", "中毒", "大量出血", "尿不出", "尿闭",
                 "误食", "车祸", "昏迷", "高烧", "窒息", "站不起来"]
_MEDICAL_WORDS = ["病", "呕吐", "腹泻", "拉稀", "发烧", "体温", "症状", "用药", "吃药",
                  "治疗", "就医", "咳嗽", "打喷嚏", "不吃", "便血", "精神差", "皮肤病",
                  "寄生虫", "藓", "耳螨"]
_HEALTH_WORDS = ["健康", "体检", "体重", "疫苗", "驱虫", "评分", "bcs", "养护", "护理"]
_TOOL_WORDS = ["我的订单", "我的购物车", "购物车", "订单", "我的宠物", "推荐", "评论", "口碑", "买到", "买什么"]
_CHITCHAT_WORDS = ["你好", "您好", "hi", "hello", "在吗", "谢谢", "再见", "哈喽"]


class ChatState(TypedDict, total=False):
    """对话图状态。

    ``total=False``：节点只需返回自己产出的字段，未返回的字段保持原值。
    ``messages`` 用 ``add_messages`` reducer 做「追加 + 按 id 去重」，这是多轮记忆的基础。
    """

    query: str
    pet: Dict[str, Any]
    user_id: int
    attachments: List[Dict[str, Any]]
    messages: Annotated[List[AnyMessage], add_messages]
    intents: List[str]
    intent_reason: str
    search_query: str
    knowledge: List[Dict[str, Any]]
    knowledge_text: str
    tool_context: str
    memory_text: str
    medical: bool
    final_messages: List[AnyMessage]
    reply: str


def _rule_intents(query: str) -> List[str]:
    """基于关键词的意图分类（模型不可用时的兜底）。

    优先级：急症/疾病 > 健康养护；工具查询独立并存；都没有时按「是否寒暄」区分
    chitchat 与 knowledge。

    Args:
        query: 用户问题原文。

    Returns:
        意图列表，至少一项。

    Raises:
        无。
    """
    q = query.lower()
    intents: List[str] = []
    if any(w in q for w in _URGENT_WORDS) or any(w in q for w in _MEDICAL_WORDS):
        # 用 elif 而非独立 if：命中疾病词时不必再判健康词——两者都会触发检索，
        # 但医疗意图还要额外追加就医护栏，优先级更高。
        intents.append("medical_urgent")
    elif any(w in q for w in _HEALTH_WORDS):
        intents.append("health")
    if any(w in q for w in _TOOL_WORDS):
        intents.append("tool_query")
    if not intents:
        # 两个字符以内的短句（「在吗」「嗯嗯」）几乎都是寒暄，按后者处理可省一次检索。
        if any(w in q for w in _CHITCHAT_WORDS) or len(query.strip()) <= 2:
            intents.append("chitchat")
        else:
            intents.append("knowledge")
    return intents


async def classify_intent(state: ChatState) -> Dict[str, Any]:
    """意图识别节点。

    Args:
        state: 图状态（使用 ``query`` / ``attachments``）。

    Returns:
        ``{"intents": [...], "intent_reason": str}``。多意图去重且保持模型输出顺序。

    Raises:
        无——模型异常降级为关键词规则。
    """
    query = state.get("query", "")
    # 纯附件消息（如只发一张照片）无法用文字分类，直接按知识类处理让检索 / 生成接手。
    if state.get("attachments") and not query.strip():
        return {"intents": ["knowledge"], "intent_reason": "attachment/no-text"}
    if settings.is_mock:
        return {"intents": _rule_intents(query), "intent_reason": "mock/rule"}

    prompt = (
        "你是宠物社区 AI 助手的意图分类器。请判断用户问题命中的全部意图，"
        "可以多选：一句话可能同时包含多个意图，命中项必须全部输出。\n"
        "可选意图：\n"
        "- chitchat：打招呼、闲聊、寒暄\n"
        "- knowledge：养宠知识咨询（喂养、疫苗、驱虫、行为、护理等）\n"
        "- health：宠物健康评估、体检、养护计划\n"
        "- medical_urgent：疑似生病、症状描述、用药、急症（如中毒/抽搐/尿不出）\n"
        "- tool_query：需要查询用户本人数据（我的宠物、订单、购物车、商品评论、社区动态、养护画像与偏好），"
        "或需要商城的真实商品信息（找商品、商品推荐、比价、查价格 / 库存 / 是否在售）\n"
        "注意：只问养宠知识或选购常识（如「猫粮怎么选」「幼猫吃什么」）时归 knowledge；"
        "明确想在商城里找、买、看价格库存时才加 tool_query。\n"
        "只输出意图分类结果。\n"
    )
    try:
        result: IntentResult = await astructured_invoke(IntentResult, prompt, query)
        # dict.fromkeys 去重并保序：模型偶尔会把同一意图输出两次。
        intents = list(dict.fromkeys(result.intents or []))
        if intents:
            return {"intents": intents, "intent_reason": result.reason}
        logger.warning("意图识别返回空列表，降级为关键词规则")
    except Exception:
        logger.warning("意图识别失败，降级为关键词规则", exc_info=True)
    return {"intents": _rule_intents(query), "intent_reason": "fallback/rule"}


# 追问信号词：出现这些词说明当前问句脱离了上文就无法检索（「那它呢」「怎么办」）。
_FOLLOWUP_HINTS = ["它", "他", "她", "这个", "那个", "这样", "那样", "还有", "可是", "但是",
                   "那", "呢", "怎么办", "为什么", "多久", "几次", "要不要", "能不能", "可以"]


def _needs_rewrite(query: str) -> bool:
    """判断问句是否需要先改写再检索。

    Args:
        query: 用户原句。

    Returns:
        True 表示过短或含指代/追问信号，需要结合上下文补全。
        过短必改写：几个字很难带出可检索的实体词。

    Raises:
        无。
    """
    q = query.strip()
    if len(q) <= settings.RAG_REWRITE_MIN_CHARS:
        return True
    return any(hint in q for hint in _FOLLOWUP_HINTS)


def _recent_dialogue(state: ChatState, limit: int = 4) -> str:
    """抽取最近几轮对话作为改写上下文。

    Args:
        state: 图状态（读 ``messages``）。
        limit: 取最近多少条消息。

    Returns:
        形如「用户：…\\n助手：…」的文本；无历史时为空串。

    Raises:
        无。
    """
    messages = [m for m in (state.get("messages") or []) if isinstance(m, (HumanMessage, AIMessage))]
    # 末尾的 HumanMessage 就是本轮问题本身，不能当「历史」喂给改写器，
    # 否则它会把自己的问句再复述一遍。
    if messages and isinstance(messages[-1], HumanMessage):
        messages = messages[:-1]
    lines = []
    for msg in messages[-limit:]:
        role = "用户" if isinstance(msg, HumanMessage) else "助手"
        # 压平空白并截断：改写只需要语义线索，长段落会浪费 token。
        text = " ".join(msg.text.split())[:120]
        if text:
            lines.append(f"{role}：{text}")
    return "\n".join(lines)


async def _build_search_query(state: ChatState) -> Dict[str, Any]:
    """把追问改写成可独立检索的完整问句。

    Args:
        state: 图状态。

    Returns:
        ``{"query": str, "species": [...]}``；无需或无法改写时返回原句 + 空物种。
        物种由改写器顺带抽取，用于后续过滤。

    Raises:
        无。
    """
    query = state.get("query", "")
    if not settings.RAG_QUERY_REWRITE or settings.is_mock or not _needs_rewrite(query):
        return {"query": query, "species": []}

    context = _recent_dialogue(state)
    # 首轮提问没有上下文可补全，改写只会引入臆测，直接跳过。
    if not context:
        return {"query": query, "species": []}
    prompt = (
        "你是宠物知识库的检索查询改写器。用户在多轮对话中提出了最新一句话，"
        "请结合对话历史把它改写成一个脱离上下文也能独立检索的完整问句。\n"
        "要求：补全「它/这个/那」等指代与前文省略的主体；不要新增用户没问的内容；"
        "不要给出答案；只输出 JSON。\n"
        f"对话历史：\n{context}\n"
    )
    try:
        result: SearchQuery = await astructured_invoke(SearchQuery, prompt, query)
        rewritten = (result.query or "").strip()
        if rewritten:
            return {"query": rewritten, "species": result.species or []}
    except Exception:
        logger.warning("检索查询改写失败，使用原句检索", exc_info=True)
    return {"query": query, "species": []}


async def retrieve_knowledge(state: ChatState) -> Dict[str, Any]:
    """知识检索节点。

    Args:
        state: 图状态。

    Returns:
        ``{"knowledge": [...], "knowledge_text": str, "search_query": str}``；
        不需要检索时三者均为空。

    Raises:
        ``kb.search.RagUnavailable``：知识库不可用时向上抛出——知识类提问宁可明确失败，
        也不要让模型「无依据却答得像真的」。
    """
    intents = state.get("intents") or []
    if not _KB_INTENTS.intersection(intents) or not settings.RAG_ENABLED:
        return {"knowledge": [], "knowledge_text": "", "search_query": ""}

    rewritten = await _build_search_query(state)
    pet_species = (state.get("pet") or {}).get("species") or []
    # 改写器抽到的物种更贴近本轮问题，优先于档案物种：用户问「狗能不能…」时
    # 即便档案里养的是猫，也应按狗来过滤。
    results = await kb_search.retrieve(
        rewritten["query"],
        pet_species=rewritten["species"] or pet_species,
    )
    return {
        "knowledge": results,
        "knowledge_text": kb_search.format_context(results),
        "search_query": rewritten["query"],
    }


async def tool_action(state: ChatState) -> Dict[str, Any]:
    """工具调用节点：让模型自主查询用户真实数据。

    用 ``create_agent`` 而非固定编排，是因为「问档案 + 问订单」这类组合问题需要模型
    自行决定调用哪些工具、调用几次。

    Args:
        state: 图状态。

    Returns:
        ``{"tool_context": str}``：工具执行结果的文字汇总；无工具可用或失败时为空串。

    Raises:
        无——工具链路失败只降级为「没有真实数据」，不影响回答。
    """
    user_id = state.get("user_id", 0)
    query = state.get("query", "")
    if "tool_query" not in (state.get("intents") or []) or settings.is_mock:
        return {"tool_context": ""}
    tools = build_tools(user_id)
    if not tools:
        return {"tool_context": ""}
    try:
        # 延迟 import：``langchain.agents`` 的导入较重，只有真正需要工具时才加载。
        from langchain.agents import create_agent
        agent = create_agent(
            get_client(),
            tools,
            system_prompt="你是宠物社区的数据查询助手。根据用户问题调用合适的工具查询真实数据；"
                          "问题同时涉及多类数据（例如既问宠物档案又问购物车或订单）时，"
                          "可以连续调用多个工具，再统一汇总；"
                          "汇总要简洁准确，某个工具返回空数组时如实说明该项没有数据。",
        )
        result = await agent.ainvoke({"messages": [HumanMessage(content=query)]})
        messages = result.get("messages", []) if isinstance(result, dict) else []
        if messages:
            # 最后一条即 agent 对工具结果的汇总；中间的工具调用消息不注入 Prompt。
            return {"tool_context": messages[-1].text.strip()}
    except Exception:
        logger.warning("工具调用失败，跳过真实数据上下文", exc_info=True)
    return {"tool_context": ""}


async def recall_memory(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """长期记忆召回节点。

    从 LangGraph store 读取「用户级 + 当前宠物级」两个命名空间的记忆，拼成提示词片段。

    Args:
        state: 图状态（读 ``pet`` / ``user_id``）。
        config: 运行配置，必须携带 ``store``（编译期注入），否则直接跳过。

    Returns:
        ``{"memory_text": str}``：每行一条，形如 ``- [用户] 偏好…``；失败或未开启时为空串。

    Raises:
        无。
    """
    if not settings.MEMORY_ENABLED:
        return {"memory_text": ""}
    store = (config or {}).get("store") if isinstance(config, dict) else None
    if store is None:
        return {"memory_text": ""}

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
                # 两级共享同一个总量上限：避免记忆过多把知识和档案挤出上下文窗口。
                if len(lines) >= settings.MEMORY_MAX_ITEMS:
                    break
            if len(lines) >= settings.MEMORY_MAX_ITEMS:
                break
    except Exception:
        logger.warning("读取长期记忆失败，本轮不注入长期记忆", exc_info=True)
        return {"memory_text": ""}
    return {"memory_text": "\n".join(lines)}


def _multimodal_content(text: str, attachments: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """构造多模态消息内容（文字 + 图片）。

    Args:
        text: 用户文字。
        attachments: 附件列表。

    Returns:
        多模态 content 列表；没有可用图片时返回 None，调用方据此保持纯文本消息
        （纯文本模型的接口不接受 list 形式的 content）。

    Raises:
        无。
    """
    parts: List[Dict[str, Any]] = [{"type": "text", "text": text or "请帮我看看这些内容"}]
    used = False
    for att in attachments or []:
        url = (att.get("url") or "").strip()
        # 只处理图片；音频已在路由层转成文字，视频当前不支持。
        if att.get("type") != KIND_IMAGE or not url:
            continue
        parts.append({"type": "image", "url": url})
        used = True
    return parts if used else None


async def compose(state: ChatState) -> Dict[str, Any]:
    """Prompt 组装节点：把 System Prompt 与裁剪后的历史消息拼成最终输入。

    Args:
        state: 图状态。

    Returns:
        ``{"final_messages": [...], "medical": bool}``。

    Raises:
        无（``PetInfo`` 校验失败时退化为空档案）。
    """
    pet_dict = state.get("pet") or {}
    try:
        pet = PetInfo(**pet_dict)
    except Exception:
        # 前端可能多传/传错字段，档案不合法不应让整轮对话失败。
        pet = PetInfo()
    medical = "medical_urgent" in (state.get("intents") or [])

    system_prompt = persona.build_system_prompt(
        pet,
        memory_context=state.get("memory_text", ""),
        knowledge_context=state.get("knowledge_text", ""),
        tool_context=state.get("tool_context", ""),
        medical=medical,
    )
    messages: List[AnyMessage] = [SystemMessage(content=system_prompt)]
    history = state.get("messages") or []
    # 只取最近 N 条：checkpoint 会无限累积，全量送模型会超上下文并显著增加成本。
    window = history[-settings.HISTORY_MAX_MESSAGES:]
    # 本轮问题（最后一条 HumanMessage）要替换成多模态版本，先定位它。
    current_human = next((m for m in reversed(window) if isinstance(m, HumanMessage)), None)
    multimodal = None
    if current_human is not None and settings.vision_enabled:
        multimodal = _multimodal_content(
            current_human.text, state.get("attachments") or [])
    for msg in window:
        content = msg.text
        # 跳过空消息（如只有工具调用没有文本的 AI 消息），但保留本轮问题以免丢失输入。
        if not content.strip() and msg is not current_human:
            continue
        if isinstance(msg, HumanMessage):
            if msg is current_human and multimodal is not None:
                messages.append(HumanMessage(content=multimodal))
            elif content.strip():
                messages.append(HumanMessage(content=content))
        elif isinstance(msg, AIMessage):
            # 重建消息对象而不是直接复用：丢弃 checkpoint 里的 id / additional_kwargs，
            # 避免历史「中断标记」等元信息影响本次生成。
            messages.append(AIMessage(content=content))
    return {"final_messages": messages, "medical": medical}


async def generate(state: ChatState) -> Dict[str, Any]:
    """生成节点：调用 LLM 产出最终回复。

    Args:
        state: 图状态（读 ``final_messages``）。

    Returns:
        ``{"messages": [AIMessage] 或 [], "reply": str}``：AI 消息写回图状态形成记忆，
        ``reply`` 供路由层使用。mock 模式下返回空 reply，由路由层接管模拟流。

    Raises:
        Exception: 模型调用失败时向上抛出，由路由层决定重试或返回 error 帧。
    """
    if settings.is_mock:
        return {"reply": ""}
    final_messages = state.get("final_messages", [])
    # 多模态消息的 content 是 list，据此判断要不要换视觉模型（两者可能是不同服务商/型号）。
    has_image = any(isinstance(m.content, list) for m in final_messages)
    client = get_vision_client() if has_image else get_client()
    try:
        resp = await client.ainvoke(final_messages)
        content = resp.text
        return {
            # 空回复不写入 messages：否则下一轮会在历史里出现一条空 AI 消息。
            "messages": [AIMessage(content=content)] if content.strip() else [],
            "reply": content,
        }
    except Exception:
        logger.warning("LLM 生成失败", exc_info=True)
        raise


def _route_after_classify(state: ChatState) -> Sequence[Send]:
    """意图识别后的条件路由：按意图并行扇出。

    用 ``Send`` 而非条件边，是为了让「知识检索」与「工具调用」同时进行；
    两条分支都在 ``recall_memory`` 汇合（LangGraph 会等所有入边完成）。

    Args:
        state: 图状态。

    Returns:
        待执行的分支列表；什么意图都没命中时兜底走 ``recall_memory``，保证图能走到终点。

    Raises:
        无。
    """
    intents = state.get("intents") or []
    pet = state.get("pet") or {}
    res: List[Send] = []

    if _KB_INTENTS.intersection(intents):
        # 显式传参而不是让分支继承全量状态：并行分支之间不应互相看到对方的中间结果。
        res.append(Send("retrieve_knowledge", {
            "query": state.get("query", ""),
            "pet": pet,
            "intents": intents,
            "messages": state.get("messages") or [],
        }))

    if "tool_query" in intents:
        res.append(Send("tool_action", {
            "query": state.get("query", ""),
            "user_id": state.get("user_id", 0),
            "intents": intents,
        }))

    if not res:
        # chitchat 之类不需要外部上下文，但仍要召回记忆（用户可能只是打招呼）。
        res.append(Send("recall_memory", {
            "pet": pet,
            "user_id": state.get("user_id", 0),
        }))

    return res


_graph = None


def get_chat_graph():
    """取得（首次调用时编译）对话图。

    编译成本不低，且图本身无状态（状态在 checkpoint 里），因此按进程缓存一份。

    Returns:
        已编译的 ``CompiledStateGraph``，注入了 checkpointer（对话记忆）与 store（长期记忆）。

    Raises:
        无——checkpointer / store 为 None 时图退化为无记忆模式，仍可正常对话。
    """
    global _graph
    if _graph is not None:
        return _graph

    builder = StateGraph(ChatState)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("retrieve_knowledge", retrieve_knowledge)
    builder.add_node("tool_action", tool_action)
    builder.add_node("recall_memory", recall_memory)
    builder.add_node("compose", compose)
    builder.add_node("generate", generate)

    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges("classify_intent", _route_after_classify)
    # 检索与工具两条分支都汇入 recall_memory：LangGraph 会等待全部入边完成再执行下游。
    builder.add_edge("retrieve_knowledge", "recall_memory")
    builder.add_edge("tool_action", "recall_memory")
    builder.add_edge("recall_memory", "compose")
    builder.add_edge("compose", "generate")
    builder.add_edge("generate", END)

    saver = checkpoint.get_saver()
    store = memstore.get_store()
    _graph = builder.compile(checkpointer=saver, store=store)
    return _graph


def initial_state(query: str, pet: Dict[str, Any], user_id: int,
                  messages: List[AnyMessage],
                  attachments: Optional[List[Dict[str, Any]]] = None) -> ChatState:
    """构造对话图的初始状态。

    显式列出全部字段（含空默认值）而不是只传输入项：并行分支读取未初始化字段时
    会拿到 None，统一成空容器可省掉各节点里的判空分支。

    Args:
        query: 本轮检索用查询（文字，或纯语音时的转写拼接）。
        pet: 宠物档案字典。
        user_id: 用户 ID。
        messages: 已拼接历史的输入消息（含本轮 HumanMessage）。
        attachments: 附件列表。

    Returns:
        可直接传给 ``graph.ainvoke`` 的状态字典。

    Raises:
        无。
    """
    return {
        "query": query,
        "pet": pet or {},
        "user_id": user_id,
        "attachments": attachments or [],
        "messages": messages or [],
        "intents": [],
        "intent_reason": "",
        "search_query": "",
        "knowledge": [],
        "knowledge_text": "",
        "tool_context": "",
        "memory_text": "",
        "medical": False,
        "final_messages": [],
        "reply": "",
    }
