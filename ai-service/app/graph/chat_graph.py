"""LangGraph 对话编排图（chat graph）

把原先手写的「拼消息 → 调模型」升级为显式状态图编排，节点职责单一、可观测、可扩展：

    START → classify_intent ──┬─(知识类, Send)──→ retrieve_knowledge ──┐
                              ├─(工具类, Send)──→ tool_action ────────┼→ recall_memory → compose → generate → END
                              └─(闲聊)───────────────────────────────┘

  知识类（knowledge / health / medical_urgent）与工具类（tool_query）可以同时命中：
  路由层用 Send 并行扇出两个分支，两个分支各自写入互不重叠的状态字段，随后在
  recall_memory 汇合（同一超级步完成后只执行一次）。

  - classify_intent   意图识别（多标签：一句话可同时命中多个意图；联网用 LLM 结构化
                       输出，mock / 失败降级为关键词规则）
  - retrieve_knowledge RAG 检索：多轮追问先改写成自包含查询，再做向量+BM25 混合检索
                       （见 app/kb/search.py），检索链路不可用时直接报错（不降级）
  - tool_action       Agent 工具调用：查询用户真实宠物 / 订单 / 购物车 / 评论 / 动态
  - recall_memory     并行分支的汇合点 + 长期记忆读取：从 runtime store 读取用户级 + 宠物级记忆
  - compose           组装最终 System Prompt（人设 + 宠物档案 + 长期记忆 + 参考知识 + 真实数据 + 医疗护栏）
  - generate          调用 LLM 生成回答（真实模式；mock 模式由路由层走打字机 mock 流）

会话记忆（LangGraph 官方 checkpoint）：
  图状态中的 messages 通道（add_messages reducer）跨轮累积对话消息，编译时注入
checkpoint.get_saver() 提供的官方 PostgresSaver 后由 checkpoint 自动持久化
（thread_id = 用户+会话），下一轮对话自动恢复历史；被用户中止的轮次由路由层
补写回 messages（见 chat._persist_interrupted）。
compose 组装上下文时只取最近 HISTORY_MAX_MESSAGES 条窗口，控制 token 成本。

长期记忆（LangGraph 官方 runtime store）：
  编译时另注入 memstore.get_store() 提供的官方 PostgresStore，recall_memory 节点
  经 config["store"] 读取（用户 + 宠物命名空间隔离，跨会话生效，见 app/memstore.py）；
  store 不可用或 MEMORY_ENABLED 关闭时降级为无长期记忆，不影响对话。
"""
import logging
from typing import Annotated, Any, Dict, List, Optional, Sequence, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
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

# 需要 RAG 检索的意图
_KB_INTENTS = {"knowledge", "health", "medical_urgent"}

# 意图关键词规则（mock 模式与 LLM 失败时的降级判断）
_URGENT_WORDS = ["急救", "抽搐", "呼吸困难", "中毒", "大量出血", "尿不出", "尿闭",
                 "误食", "车祸", "昏迷", "高烧", "窒息", "站不起来"]
_MEDICAL_WORDS = ["病", "呕吐", "腹泻", "拉稀", "发烧", "体温", "症状", "用药", "吃药",
                  "治疗", "就医", "咳嗽", "打喷嚏", "不吃", "便血", "精神差", "皮肤病",
                  "寄生虫", "藓", "耳螨"]
_HEALTH_WORDS = ["健康", "体检", "体重", "疫苗", "驱虫", "评分", "bcs", "养护", "护理"]
_TOOL_WORDS = ["我的订单", "我的购物车", "购物车", "订单", "我的宠物", "推荐", "评论", "口碑", "买到", "买什么"]
_CHITCHAT_WORDS = ["你好", "您好", "hi", "hello", "在吗", "谢谢", "再见", "哈喽"]


class ChatState(TypedDict, total=False):
    """对话图状态（messages 走 add_messages reducer，其余字段后写覆盖前值）"""

    query: str                     # 用户本轮输入（纯附件时为附件摘要文本，含音频转写）
    pet: Dict[str, Any]            # 宠物档案（dict 形式）
    user_id: int                   # 当前用户 ID
    attachments: List[Dict[str, Any]]  # 本轮多模态附件（图片/音频/视频，dict 形式）
    # 对话消息（跨轮累积）：输入的本轮 HumanMessage、generate 写回的 AIMessage、
    # 中断补写的人机消息都汇聚于此，由 checkpoint 按 thread 持久化，构成长期记忆；
    # 消息内容恒为纯文本（附件描述 / 转写以文字并入），图片分片仅在 compose 组装
    # 最终请求时按当前轮附件现算——避免图片分片进 checkpoint 撑爆记忆存储
    messages: Annotated[List[AnyMessage], add_messages]
    intents: List[str]             # 本轮命中的全部意图（知识类与工具类可同时命中）
    intent_reason: str
    search_query: str              # 实际用于检索的查询（多轮追问改写后的自包含问法，空表示未改写）
    knowledge: List[Dict[str, Any]]  # RAG 检索结果（同时作为前端「参考来源」数据）
    knowledge_text: str            # 拼接后的知识上下文
    tool_context: str              # 工具查询结果
    memory_text: str               # 长期记忆上下文（recall_memory 从 runtime store 读取渲染）
    medical: bool                  # 是否命中医疗护栏
    final_messages: List[Dict[str, str]]  # 最终发给模型的消息
    reply: str                     # 生成结果（真实模式）


# ---------- 节点实现 ----------

def _rule_intents(query: str) -> List[str]:
    """关键词规则意图识别（mock / 降级），知识类与工具类可以同时命中"""
    q = query.lower()
    intents: List[str] = []
    # 知识类只取一个：医疗优先，其次健康，否则交给默认的 knowledge
    if any(w in q for w in _URGENT_WORDS) or any(w in q for w in _MEDICAL_WORDS):
        intents.append("medical_urgent")
    elif any(w in q for w in _HEALTH_WORDS):
        intents.append("health")
    # 工具查询与知识类正交，可并存（如「猫吐了，顺便查下订单」）
    if any(w in q for w in _TOOL_WORDS):
        intents.append("tool_query")
    if not intents:
        if any(w in q for w in _CHITCHAT_WORDS) or len(query.strip()) <= 2:
            intents.append("chitchat")
        else:
            intents.append("knowledge")
    return intents


async def classify_intent(state: ChatState) -> Dict[str, Any]:
    """意图识别节点：真实模式用 LLM 结构化输出（多标签），其余走规则

    一句话可能同时命中多个意图（如「猫吐了，顺便看下我之前买的猫粮订单」
    既要知识检索又要查用户数据），因此输出意图列表，由路由层并行扇出。
    纯附件消息（无文字）没有可分类的文本：养宠场景发图片 / 音频 / 视频
    绝大多数是「看看这是什么情况」的知识或健康咨询，直接归 knowledge，
    不再让分类器对「用户发送了 N 张图片」这类摘要文本猜意图。
    """
    query = state.get("query", "")
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
        "- tool_query：需要查询用户本人数据（我的宠物、订单、购物车、商品评论、社区动态）\n"
        "只输出意图分类结果。\n"
    )
    try:
        result: IntentResult = await astructured_invoke(IntentResult, prompt, query)
        intents = list(dict.fromkeys(result.intents or []))
        if intents:
            return {"intents": intents, "intent_reason": result.reason}
        logger.warning("意图识别返回空列表，降级为关键词规则")
    except Exception:
        logger.warning("意图识别失败，降级为关键词规则", exc_info=True)
    return {"intents": _rule_intents(query), "intent_reason": "fallback/rule"}


# 追问指代词：命中任一，或问题很短，说明脱离上下文就检索不到东西，需要改写
_FOLLOWUP_HINTS = ["它", "他", "她", "这个", "那个", "这样", "那样", "还有", "可是", "但是",
                   "那", "呢", "怎么办", "为什么", "多久", "几次", "要不要", "能不能", "可以"]


def _needs_rewrite(query: str) -> bool:
    """是否需要改写：短问句或含指代/省略词时改写，自包含长问句直接检索（省一次 LLM 调用）"""
    q = query.strip()
    if len(q) <= settings.RAG_REWRITE_MIN_CHARS:
        return True
    return any(hint in q for hint in _FOLLOWUP_HINTS)


def _recent_dialogue(state: ChatState, limit: int = 4) -> str:
    """取最近若干条对话作为改写上下文（当前轮 HumanMessage 已在末尾，排除掉）"""
    messages = [m for m in (state.get("messages") or []) if isinstance(m, (HumanMessage, AIMessage))]
    if messages and isinstance(messages[-1], HumanMessage):
        messages = messages[:-1]
    lines = []
    for msg in messages[-limit:]:
        role = "用户" if isinstance(msg, HumanMessage) else "助手"
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        text = " ".join(text.split())[:120]
        if text:
            lines.append(f"{role}：{text}")
    return "\n".join(lines)


async def _build_search_query(state: ChatState) -> Dict[str, Any]:
    """构造检索查询：多轮追问时结合上下文补全指代，返回 {query, species}

    改写只影响检索用文本，不改用户可见消息，也不写回 messages（不污染对话记忆）；
    无需改写、mock 模式或 LLM 调用失败时一律退回原句——改写是召回优化，
    不是对话前提，失败必须静默降级而不是让本轮报错。
    """
    query = state.get("query", "")
    if not settings.RAG_QUERY_REWRITE or settings.is_mock or not _needs_rewrite(query):
        return {"query": query, "species": []}

    context = _recent_dialogue(state)
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
    """RAG 检索节点：命中知识类意图时做混合检索（向量 + BM25 关键词）

    检索链路不可用时抛 RagUnavailable（由路由层转成 error 事件），不降级为
    关键词检索、也不静默跳过——避免让模型在没有知识依据的情况下作答。

    物种信号优先取本轮提问里显式提到的物种（用户可能在替别人的宠物问），
    提问没提到时才回退到宠物档案物种。
    """
    intents = state.get("intents") or []
    if not _KB_INTENTS.intersection(intents) or not settings.RAG_ENABLED:
        return {"knowledge": [], "knowledge_text": "", "search_query": ""}

    rewritten = await _build_search_query(state)
    pet_species = (state.get("pet") or {}).get("species") or []
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
    """工具调用节点：命中 tool_query 意图时运行 ReAct Agent 查询真实数据"""
    user_id = state.get("user_id", 0)
    query = state.get("query", "")
    if "tool_query" not in (state.get("intents") or []) or settings.is_mock:
        return {"tool_context": ""}
    tools = build_tools(user_id)
    if not tools:
        return {"tool_context": ""}
    try:
        from langchain.agents import create_agent
        agent = create_agent(
            get_client(),
            tools,
            system_prompt="你是宠物社区的数据查询助手，根据用户问题调用合适的工具查询真实数据，"
                          "并简洁汇总查询结果；若工具无数据，如实说明。",
        )
        result = await agent.ainvoke({"messages": [{"role": "user", "content": query}]})
        messages = result.get("messages", []) if isinstance(result, dict) else []
        if messages:
            content = messages[-1].content
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content
                                  if isinstance(p, dict) and p.get("type") == "text")
            return {"tool_context": (content or "").strip()}
    except Exception:
        logger.warning("工具调用失败，跳过真实数据上下文", exc_info=True)
    return {"tool_context": ""}


async def recall_memory(state: ChatState, config: RunnableConfig) -> Dict[str, Any]:
    """长期记忆读取节点：从 runtime store 读取用户级 + 当前宠物级记忆

    store 由图编译时注入（config["store"]，LangGraph 官方 runtime store 机制）；
    store 缺失（未初始化 / 未注入）、MEMORY_ENABLED 关闭或读取失败时降级为
    空字符串，compose 对应省略记忆块，对话主流程不受影响。
    记忆条数按 MEMORY_MAX_ITEMS 截断（用户级 + 宠物级合计），控制 token 成本。
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
        for ns, label in (
            (memstore.user_ns(user_id), "用户"),
            (memstore.pet_ns(user_id, pet_id), pet_name or "宠物"),
        ):
            items = await store.asearch(ns, limit=settings.MEMORY_MAX_ITEMS)
            for item in items:
                content = str((item.value or {}).get("content", "")).strip()
                if content:
                    lines.append(f"- [{label}] {content}")
                if len(lines) >= settings.MEMORY_MAX_ITEMS:
                    break
            if len(lines) >= settings.MEMORY_MAX_ITEMS:
                break
    except Exception:
        logger.warning("读取长期记忆失败，本轮不注入长期记忆", exc_info=True)
        return {"memory_text": ""}
    return {"memory_text": "\n".join(lines)}


def _multimodal_content(text: str, attachments: List[Dict[str, Any]]) -> list:
    """当前轮用户消息的多模态内容分片（OpenAI 兼容格式）

    图片以 image_url 分片直发，URL 为 OSS 公网地址，由视觉模型服务商回源拉取；
    URL 缺失（异常数据）时跳过该图并保留文字描述。返回内容为 list 表示走
    视觉模型；无有效图片时返回 None 由调用方退回纯文本。
    """
    parts: List[Dict[str, Any]] = [{"type": "text", "text": text or "请帮我看看这些内容"}]
    used = False
    for att in attachments or []:
        url = (att.get("url") or "").strip()
        if att.get("type") != KIND_IMAGE or not url:
            continue
        parts.append({"type": "image_url", "image_url": {"url": url}})
        used = True
    return parts if used else None


async def compose(state: ChatState) -> Dict[str, Any]:
    """组装节点：拼装最终消息列表（不调用模型）

    历史窗口从图状态 messages 中取最近 HISTORY_MAX_MESSAGES 条（checkpoint 保留
    全量记忆，此处按窗口裁剪控制上下文与 token 成本）；窗口内为空内容的助手消息
    （如 LLM 空输出）直接跳过。
    多模态：仅当前轮（窗口内最后一条 HumanMessage）携带 image_url 分片发给视觉
    模型；历史轮的附件早已以文字描述并入消息内容，不再重复发图控制 token 成本。
    """
    pet_dict = state.get("pet") or {}
    try:
        pet = PetInfo(**pet_dict)
    except Exception:
        pet = PetInfo()
    medical = "medical_urgent" in (state.get("intents") or [])

    system_prompt = persona.build_system_prompt(
        pet,
        memory_context=state.get("memory_text", ""),
        knowledge_context=state.get("knowledge_text", ""),
        tool_context=state.get("tool_context", ""),
        medical=medical,
    )
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    history = state.get("messages") or []
    window = history[-settings.HISTORY_MAX_MESSAGES:]
    # 当前轮用户消息：窗口内最后一条 HumanMessage（compose 运行时 generate 尚未执行）
    current_human = next((m for m in reversed(window) if isinstance(m, HumanMessage)), None)
    multimodal = None
    if current_human is not None and settings.vision_enabled:
        multimodal = _multimodal_content(
            current_human.content if isinstance(current_human.content, str) else str(current_human.content),
            state.get("attachments") or [])
    for msg in window:
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if not content.strip() and msg is not current_human:
            continue
        if isinstance(msg, HumanMessage):
            if msg is current_human and multimodal is not None:
                messages.append({"role": "user", "content": multimodal})
            elif content.strip():
                messages.append({"role": "user", "content": content})
        elif isinstance(msg, AIMessage):
            messages.append({"role": "assistant", "content": content})
    return {"final_messages": messages, "medical": medical}


async def generate(state: ChatState) -> Dict[str, Any]:
    """生成节点：真实模式调用 LLM 并把回复写回 messages（随 checkpoint 持久化）；
    mock 模式不调用（由路由层走 mock 流，回复由路由层补写进图状态）

    最终消息含多模态分片（当前轮带图）时切换视觉模型客户端；视觉模型不可用
    的兜底由 compose 保证（不会组装出带图内容），此处仅按内容形态选客户端。
    """
    if settings.is_mock:
        return {"reply": ""}
    final_messages = state.get("final_messages", [])
    has_image = any(isinstance(m.get("content"), list) for m in final_messages)
    client = get_vision_client() if has_image else get_client()
    try:
        resp = await client.ainvoke(final_messages)
        content = resp.content
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content
                              if isinstance(p, dict) and p.get("type") == "text")
        content = content or ""
        return {
            # 助手回复写回图状态：checkpoint 在本超级步结束后自动持久化，
            # 下一轮对话从 checkpoint 恢复即为跨轮记忆；空输出不落记忆
            "messages": [AIMessage(content=content)] if content.strip() else [],
            "reply": content,
        }
    except Exception:
        logger.warning("LLM 生成失败", exc_info=True)
        raise


# ---------- 路由 ----------

def _route_after_classify(state: ChatState) -> Sequence[Send]:
    """多意图路由：知识类与工具类可同时命中，用 Send 并行扇出后在 recall_memory 汇合

    每个 Send 的第二个参数是目标节点本次执行的输入，只携带该节点真正用到的
    字段：检索分支需要 query / pet / messages（多轮追问改写）与 intents，
    工具分支需要 query / user_id / intents，闲聊直达记忆分支只需 pet / user_id。
    分支返回的增量会合并回主图状态，因此 compose 仍能拿到完整上下文。
    两个分支写入互不重叠的字段（检索结果 / 工具结果），状态通道无需 reducer。
    """
    intents = state.get("intents") or []
    pet = state.get("pet") or {}
    res: List[Send] = []

    if _KB_INTENTS.intersection(intents):
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
        res.append(Send("recall_memory", {
            "pet": pet,
            "user_id": state.get("user_id", 0),
        }))

    return res


# ---------- 图构建 ----------

_graph = None


def get_chat_graph():
    """构建并缓存对话编排图（编译一次，进程内复用）

    编译时注入 checkpoint saver（PostgreSQL 持久化图状态 / 会话记忆，
    见 app/checkpoint.py）与 runtime store（PostgreSQL 持久化长期记忆，
    见 app/memstore.py）；任一不可用时对应能力退化（无会话记忆 / 无长期记忆），
    保证对话功能仍可用。
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
    """构造图初始状态：messages 为本轮输入消息（本轮 HumanMessage，老会话首次接入时为回填历史 + 本轮）；
    attachments 为本轮多模态附件（compose 组装带图内容时用，消息通道本身只存文本）"""
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
