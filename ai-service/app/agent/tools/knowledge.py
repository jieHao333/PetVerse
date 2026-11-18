"""知识检索工具：把 RAG 混合检索包装成模型可自主调用的能力。

职责：模型决定「要查什么」之后，这里负责把问句变成可检索的自包含问句、走混合检索、
按全局序号格式化片段，并把结构化命中结果通过 ``artifact`` 回传给图（用于 SSE 引用面板）。

主要导出：
    - ``search_pet_knowledge``  LangChain 工具（``response_format="content_and_artifact"``）

外部依赖：``app.kb.search``（混合检索）、``app.llm``（追问改写）、``app.config``。

为什么用 ``content_and_artifact``：
    模型只需要看格式化后的文本（带 ``[n]`` 编号），而服务端还需要原始命中（docId / 分数 /
    摘要）来渲染前端引用面板。artifact 是 LangChain 官方提供的「内容给模型、附件给程序」
    通道，避免把 JSON 元数据塞进 Prompt 白烧 token。

引用编号为什么在这里分配：
    模型可能连续检索多次，若每次从 [1] 重新编号，前端面板的序号会与答案里的标注错位。
    工具从 ``InjectedState`` 读出已累积的引用条数，按全局偏移继续编号，编号在整轮内唯一。
"""

import logging
from typing import Annotated, Any, Dict, List, Tuple

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.config import settings
from app.kb import search as kb_search
from app.llm import astructured_invoke
from app.schemas import SearchQuery

logger = logging.getLogger(__name__)

# 追问信号词：出现这些词说明当前问句脱离上文就无法检索（「那它呢」「怎么办」）。
_FOLLOWUP_HINTS = ["它", "他", "她", "这个", "那个", "这样", "那样", "还有", "可是", "但是",
                   "那", "呢", "怎么办", "为什么", "多久", "几次", "要不要", "能不能", "可以"]

_EMPTY_RESULT = "（知识库未检索到与该问题相关的内容。请如实告知用户暂无相关资料，并建议咨询专业兽医。）"


def _needs_rewrite(query: str) -> bool:
    """判断问句是否需要先改写再检索。

    Args:
        query: 待检索的问句。

    Returns:
        True 表示过短或含指代/追问信号，需要结合上下文补全。

    Raises:
        无。
    """
    q = query.strip()
    if len(q) <= settings.RAG_REWRITE_MIN_CHARS:
        return True
    return any(hint in q for hint in _FOLLOWUP_HINTS)


def _recent_dialogue(state: Dict[str, Any], limit: int = 4) -> str:
    """抽取最近几轮对话作为改写上下文。

    Args:
        state: 图状态（读 ``messages``）。
        limit: 取最近多少条消息。

    Returns:
        形如「用户：…\\n助手：…」的文本；无历史时为空串。

    Raises:
        无。
    """
    messages = [m for m in (state.get("messages") or [])
                if isinstance(m, (HumanMessage, AIMessage))]
    # 末尾的 HumanMessage 就是本轮问题本身，不能当「历史」喂给改写器。
    if messages and isinstance(messages[-1], HumanMessage):
        messages = messages[:-1]
    lines = []
    for msg in messages[-limit:]:
        role = "用户" if isinstance(msg, HumanMessage) else "助手"
        # 压平空白并截断：改写只需要语义线索，长段落会浪费 token。
        text = " ".join(str(msg.text or "").split())[:120]
        if text:
            lines.append(f"{role}：{text}")
    return "\n".join(lines)


async def _rewrite_query(query: str, state: Dict[str, Any]) -> Tuple[str, List[str]]:
    """把多轮追问改写成可独立检索的完整问句。

    Args:
        query: 模型传入的检索问句（可能是「那它呢」这类指代句）。
        state: 图状态，用于取对话历史。

    Returns:
        ``(检索用问句, 物种列表)``；无需改写或改写失败时返回原句与空物种。

    Raises:
        无——改写失败退回原句，不让一次失败的改写阻断检索。
    """
    if not settings.RAG_QUERY_REWRITE or settings.is_mock or not _needs_rewrite(query):
        return query, []
    context = _recent_dialogue(state)
    if not context:
        # 首轮提问没有上下文可补全，改写只会引入臆测。
        return query, []

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
            return rewritten, result.species or []
    except Exception:
        logger.warning("检索查询改写失败，使用原句检索", exc_info=True)
    return query, []


def _species_of(state: Dict[str, Any], species: str) -> List[str]:
    """决定本轮检索的物种过滤条件。

    Args:
        state: 图状态（读 ``pet.species``）。
        species: 模型显式传入的物种（可为空）。

    Returns:
        物种列表，优先级：模型参数 > 宠物档案。

    Raises:
        无。

    模型显式指定优先：用户问「狗能不能吃这个」时，即便档案里养的是猫，也应按狗过滤。
    """
    explicit = [s.strip() for s in str(species or "").split(",") if s.strip()]
    if explicit:
        return explicit
    pet_species = (state.get("pet") or {}).get("species") or []
    if isinstance(pet_species, str):
        return [pet_species] if pet_species.strip() else []
    return [str(s) for s in pet_species if str(s).strip()]


@tool(response_format="content_and_artifact", parse_docstring=True)
async def search_pet_knowledge(query: str, state: Annotated[dict, InjectedState],
                               species: str = "") -> Tuple[str, List[dict]]:
    """检索 PetVerse 养宠知识库，获取有出处的专业依据（喂养、疫苗、驱虫、疾病症状、行为训练、日常护理等）。

    凡是询问养宠知识、宠物健康、症状判断、护理方法的问题，都应先调用本工具再作答；
    同一问题检索一次即可，不要用同义句重复调用。返回内容每条以 [n] 开头，n 即引用序号。

    Args:
        query: 检索问句，尽量保留用户原话中的关键实体（物种、症状、年龄阶段、具体场景）。
        species: 可选的物种限定，只填「猫」或「狗」；不确定时留空，系统按宠物档案自动过滤。
    """
    # 延迟 import 避免模块循环：检索链路依赖 kb.store → pg_store，启动期不需要加载。
    rewritten, species_from_query = await _rewrite_query(query, state)
    target_species = species_from_query or _species_of(state, species)

    # 检索失败直接向上抛（RagUnavailable）：知识问答宁可整轮明确失败，
    # 也不要让模型拿着「检索失败」继续编答案。
    hits = await kb_search.retrieve(rewritten, pet_species=target_species)
    if not hits:
        logger.info("知识检索无命中: query=%r species=%s", rewritten, target_species)
        return _EMPTY_RESULT, []

    # 全局偏移：已累积的引用条数 + 1，保证整轮编号连续且唯一。
    offset = len(state.get("knowledge") or []) + 1
    for index, hit in enumerate(hits, start=offset):
        hit["rank"] = index

    logger.info("知识检索命中 %s 条: query=%r offset=%s", len(hits), rewritten, offset)
    return kb_search.format_context(hits), hits
