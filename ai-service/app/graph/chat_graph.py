"""LangGraph 对话编排图（chat graph）

把原先手写的「拼消息 → 调模型」升级为显式状态图编排，节点职责单一、可观测、可扩展：

    START → classify_intent ──┬─(知识类)──→ retrieve_knowledge ──┬─(工具类)──→ tool_action ──┐
                             ├─(工具类)──────────────────────────┴───────────────────────────┤
                             └─(闲聊)──────────────────────────────────────────────────────→ compose → generate → END

  - classify_intent   意图识别（联网用 LLM 结构化输出，mock / 失败降级为关键词规则）
  - retrieve_knowledge RAG 检索：pgvector 语义检索，不可用时自动降级为关键词检索
  - tool_action       Agent 工具调用：查询用户真实宠物 / 订单 / 购物车 / 评论 / 动态
  - compose           组装最终 System Prompt（人设 + 宠物档案 + 参考知识 + 真实数据 + 医疗护栏）
  - generate          调用 LLM 生成回答（真实模式；mock 模式由路由层走打字机 mock 流）

设计取舍：生成环节在真实模式下直接由本图调用模型，路由层用 LangGraph 原生的
`astream(stream_mode="messages")` 逐 token 转发；mock 模式不调用模型，由路由层走
`llm.mock_stream`，但本图的意图识别 / 检索 / 工具 / 组装仍会执行，保证编排逻辑一致。
"""
import logging
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from app import persona, vectorstore
from app.config import settings
from app.llm import astructured_invoke, get_client
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
    """对话图状态（无 reducer，后写覆盖前值）"""

    query: str                     # 用户本轮输入
    pet: Dict[str, Any]            # 宠物档案（dict 形式）
    user_id: int                   # 当前用户 ID
    history: List[Dict[str, str]]  # 历史消息（role/content）
    intent: str                    # 意图
    intent_reason: str
    knowledge: List[Dict[str, Any]]  # RAG 检索结果
    knowledge_text: str            # 拼接后的知识上下文
    tool_context: str              # 工具查询结果
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
    """意图识别节点：真实模式用 LLM 结构化输出，其余走规则"""
    query = state.get("query", "")
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


async def compose(state: ChatState) -> Dict[str, Any]:
    """组装节点：拼装最终消息列表（不调用模型）"""
    pet_dict = state.get("pet") or {}
    try:
        pet = PetInfo(**pet_dict)
    except Exception:
        pet = PetInfo()
    intent = state.get("intent", "")
    medical = intent == "medical_urgent"

    system_prompt = persona.build_system_prompt(
        pet,
        knowledge_context=state.get("knowledge_text", ""),
        tool_context=state.get("tool_context", ""),
        medical=medical,
    )
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for item in state.get("history", []):
        role, content = item.get("role"), item.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": state.get("query", "")})
    return {"final_messages": messages, "medical": medical}


async def generate(state: ChatState) -> Dict[str, Any]:
    """生成节点：真实模式调用 LLM；mock 模式不调用（由路由层走 mock 流）"""
    if settings.is_mock:
        return {"reply": ""}
    try:
        resp = await get_client().ainvoke(state.get("final_messages", []))
        content = resp.content
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content
                              if isinstance(p, dict) and p.get("type") == "text")
        return {"reply": content or ""}
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
    return "compose"


def _route_after_retrieve(state: ChatState) -> str:
    return "tool_action" if state.get("intent") == "tool_query" else "compose"


# ---------- 图构建 ----------

_graph = None


def get_chat_graph():
    """构建并缓存对话编排图（编译一次，进程内复用）"""
    global _graph
    if _graph is not None:
        return _graph

    builder = StateGraph(ChatState)
    builder.add_node("classify_intent", classify_intent)
    builder.add_node("retrieve_knowledge", retrieve_knowledge)
    builder.add_node("tool_action", tool_action)
    builder.add_node("compose", compose)
    builder.add_node("generate", generate)

    builder.add_edge(START, "classify_intent")
    builder.add_conditional_edges("classify_intent", _route_after_classify,
                                  ["retrieve_knowledge", "tool_action", "compose"])
    builder.add_conditional_edges("retrieve_knowledge", _route_after_retrieve,
                                  ["tool_action", "compose"])
    builder.add_edge("tool_action", "compose")
    builder.add_edge("compose", "generate")
    builder.add_edge("generate", END)

    _graph = builder.compile()
    return _graph


def initial_state(query: str, pet: Dict[str, Any], user_id: int,
                  history: List[Dict[str, str]]) -> ChatState:
    """构造图初始状态"""
    return {
        "query": query,
        "pet": pet or {},
        "user_id": user_id,
        "history": history or [],
        "intent": "",
        "intent_reason": "",
        "knowledge": [],
        "knowledge_text": "",
        "tool_context": "",
        "medical": False,
        "final_messages": [],
        "reply": "",
    }
