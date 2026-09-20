"""LangGraph 对话编排图（chat graph）

把原先手写的「拼消息 → 调模型」升级为显式状态图编排，节点职责单一、可观测、可扩展：

    START → classify_intent ──┬─(知识类)──→ retrieve_knowledge ──┬─(工具类)──→ tool_action ──┐
                             ├─(工具类)──────────────────────────┴───────────────────────────┤
                             └─(闲聊)──────────────────────────────────────────────────────→ recall_memory → compose → generate → END

  - classify_intent   意图识别（联网用 LLM 结构化输出，mock / 失败降级为关键词规则）
  - retrieve_knowledge RAG 检索：pgvector 语义检索，不可用时自动降级为关键词检索
  - tool_action       Agent 工具调用：查询用户真实宠物 / 订单 / 购物车 / 评论 / 动态
  - recall_memory     长期记忆读取：从 runtime store 读取用户级 + 宠物级记忆
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
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app import checkpoint, memstore, persona, vectorstore
from app.config import settings
from app.llm import astructured_invoke, get_client, get_vision_client
from app.media import KIND_IMAGE, image_data_url
from app.schemas import IntentResult, PetInfo
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
    # 最终请求时按当前轮附件现算——避免 base64 图片进 checkpoint 撑爆记忆存储
    messages: Annotated[List[AnyMessage], add_messages]
    intent: str                    # 意图
    intent_reason: str
    knowledge: List[Dict[str, Any]]  # RAG 检索结果
    knowledge_text: str            # 拼接后的知识上下文
    tool_context: str              # 工具查询结果
    memory_text: str               # 长期记忆上下文（recall_memory 从 runtime store 读取渲染）
    medical: bool                  # 是否命中医疗护栏
    final_messages: List[Dict[str, str]]  # 最终发给模型的消息
    reply: str                     # 生成结果（真实模式）


# ---------- 节点实现 ----------

def _rule_intent(query: str) -> str:
    """关键词规则意图识别（mock / 降级）"""
    q = query.lower()
    if any(w in query for w in _URGENT_WORDS):
        return "medical_urgent"
    if any(w in query for w in _MEDICAL_WORDS):
        return "medical_urgent"
    if any(w in query for w in _TOOL_WORDS):
        return "tool_query"
    if any(w in query for w in _HEALTH_WORDS):
        return "health"
    if any(w in query for w in _CHITCHAT_WORDS) or len(query.strip()) <= 2:
        return "chitchat"
    return "knowledge"


async def classify_intent(state: ChatState) -> Dict[str, Any]:
    """意图识别节点：真实模式用 LLM 结构化输出，其余走规则

    纯附件消息（无文字）没有可分类的文本：养宠场景发图片 / 音频 / 视频
    绝大多数是「看看这是什么情况」的知识或健康咨询，直接归 knowledge，
    不再让分类器对「用户发送了 N 张图片」这类摘要文本猜意图。
    """
    query = state.get("query", "")
    if state.get("attachments") and not query.strip():
        return {"intent": "knowledge", "intent_reason": "attachment/no-text"}
    if settings.is_mock:
        intent = _rule_intent(query)
        return {"intent": intent, "intent_reason": "mock/rule"}

    prompt = (
        "你是宠物社区 AI 助手的意图分类器。请判断用户问题的意图，"
        "只输出意图分类结果。\n"
        "可选意图：\n"
        "- chitchat：打招呼、闲聊、寒暄\n"
        "- knowledge：养宠知识咨询（喂养、疫苗、驱虫、行为、护理等）\n"
        "- health：宠物健康评估、体检、养护计划\n"
        "- medical_urgent：疑似生病、症状描述、用药、急症（如中毒/抽搐/尿不出）\n"
        "- tool_query：需要查询用户本人数据（我的宠物、订单、购物车、商品评论、社区动态）\n"
    )
    try:
        result: IntentResult = await astructured_invoke(IntentResult, prompt, query)
        return {"intent": result.intent, "intent_reason": result.reason}
    except Exception:
        logger.warning("意图识别失败，降级为关键词规则", exc_info=True)
        intent = _rule_intent(query)
        return {"intent": intent, "intent_reason": "fallback/rule"}


async def retrieve_knowledge(state: ChatState) -> Dict[str, Any]:
    """RAG 检索节点：命中知识类意图时检索知识库"""
    query = state.get("query", "")
    intent = state.get("intent", "")
    if intent not in _KB_INTENTS or not settings.RAG_ENABLED:
        return {"knowledge": [], "knowledge_text": ""}
    try:
        results = await vectorstore.retrieve(query)
        return {"knowledge": results, "knowledge_text": vectorstore.format_context(results)}
    except Exception:
        logger.warning("知识检索失败，跳过 RAG 上下文", exc_info=True)
        return {"knowledge": [], "knowledge_text": ""}


async def tool_action(state: ChatState) -> Dict[str, Any]:
    """工具调用节点：意图为 tool_query 时运行 ReAct Agent 查询真实数据"""
    user_id = state.get("user_id", 0)
    query = state.get("query", "")
    if state.get("intent") != "tool_query" or settings.is_mock:
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

    视觉可用时图片以 image_url 分片直发（base64 data URL，服务商无法回源拉取
    本服务内网附件地址）；图片文件缺失（被清理）时跳过该图并保留文字描述。
    返回内容为 list 表示走视觉模型；无有效图片时返回 None 由调用方退回纯文本。
    """
    parts: List[Dict[str, Any]] = [{"type": "text", "text": text or "请帮我看看这些内容"}]
    used = False
    for att in attachments or []:
        if att.get("type") != KIND_IMAGE:
            continue
        data_url = image_data_url(att)
        if data_url:
            parts.append({"type": "image_url", "image_url": {"url": data_url}})
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
    intent = state.get("intent", "")
    medical = intent == "medical_urgent"

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

def _route_after_classify(state: ChatState) -> str:
    intent = state.get("intent", "")
    if intent in _KB_INTENTS:
        return "retrieve_knowledge"
    if intent == "tool_query":
        return "tool_action"
    return "recall_memory"


def _route_after_retrieve(state: ChatState) -> str:
    return "tool_action" if state.get("intent") == "tool_query" else "recall_memory"


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
    builder.add_conditional_edges("classify_intent", _route_after_classify,
                                  ["retrieve_knowledge", "tool_action", "recall_memory"])
    builder.add_conditional_edges("retrieve_knowledge", _route_after_retrieve,
                                  ["tool_action", "recall_memory"])
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
        "intent": "",
        "intent_reason": "",
        "knowledge": [],
        "knowledge_text": "",
        "tool_context": "",
        "memory_text": "",
        "medical": False,
        "final_messages": [],
        "reply": "",
    }
