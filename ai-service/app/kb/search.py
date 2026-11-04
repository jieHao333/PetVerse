"""混合检索：向量召回 + BM25 关键词召回 + 序值融合。

职责：把「语义相近」与「术语精确命中」两种召回能力组合起来，并做物种过滤与门槛筛选，
最终产出可直接拼进 Prompt 的片段列表。

主要导出：
    - ``RagUnavailable``   检索链路不可用（知识库为空、Embedding 未配、向量召回异常）
    - ``retrieve``         主入口：返回 Top-K 片段
    - ``format_context``   把片段格式化成 Prompt 上下文（带 ``[n]`` 序号供引用）
    - ``snippet_of``       片段摘要（用于前端引用面板）
    - ``SNIPPET_CHARS``    摘要长度

外部依赖：``app.kb.store``（向量库）、``app.kb.lexicon``（BM25 索引）、
``app.kb.documents``（物种归一化）、``app.llm.get_embeddings``。

使用示例：
    from app.kb import search

    hits = await search.retrieve("幼猫什么时候打疫苗", pet_species=["猫"])
    context = search.format_context(hits)

为什么用「序值融合」而不是分数加权：余弦相似度在 0~1 之间且分布集中，BM25 分数是无上界的
绝对值，直接相加会让 BM25 主导排序。改用「通道内排名归一化后加权」（即 RRF 的简化版），
两个通道的量纲差异就被消掉了。
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from app.config import settings
from app.kb import lexicon, store
from app.kb.documents import normalize_species, species_compatible
from app.llm import get_embeddings

logger = logging.getLogger(__name__)

SNIPPET_CHARS = 180

# 摘要清洗：去掉 Markdown 标记（* ` #）与列表符号，否则前端引用面板会显示一堆符号。
_MD_NOISE = re.compile(r"[*`#]+|\s+-\s+")


class RagUnavailable(Exception):
    """知识检索不可用。

    刻意不做静默降级：知识类提问若拿不到依据却照常作答，会产出「听起来很确定但无出处」的
    内容，比明确报错更有害。调用方（chat_graph）让它向上抛，由路由层转成 error 帧。
    """

async def retrieve(query: str, k: Optional[int] = None,
                   pet_species: Any = None, extra_terms: Sequence[str] = ()) -> List[dict]:
    """混合检索主入口。

    Args:
        query: 检索问句（多轮追问应已由上游改写为自包含问句）。
        k: 返回片段数；None 时取 ``RAG_TOP_K``。
        pet_species: 目标物种（字符串或列表），用于过滤 / 降权不相关物种的片段。
        extra_terms: 额外关键词（如宠物品种），参与关键词通道分词。

    Returns:
        片段列表（按相关度降序），每项含 chunk_id / doc_id / content / source / doc_title /
        kind / section / category / species / snippet / score / matched / matched_by / rank。
        无命中时返回空列表。

    Raises:
        RagUnavailable: 知识库为空，或 Embedding / 向量召回链路异常。
    """
    text = (query or "").strip()
    # 空查询直接返回，避免把空串送去向量化（多数服务商会直接报 400）。
    if not text:
        return []
    top_k = k or settings.RAG_TOP_K
    # 召回深度必须不小于最终条数，否则融合后可能凑不满 top_k。
    recall_k = max(top_k, settings.RAG_RECALL_K)

    dense = await _dense_channel(text, recall_k)
    lexical = await _lexical_channel(text, extra_terms, recall_k)
    if not dense and not lexical:
        # 两条通道都空：需要区分「知识库没导入」与「确实没匹配到」——
        # 前者是部署问题，必须报错提示跑导入脚本；后者正常返回空即可。
        stats = await store.stats()
        if stats["chunks"] == 0:
            raise RagUnavailable(
                "知识库为空：请先执行 scripts/ingest_knowledge.py 导入知识文档")
        return []

    fused = _fuse(dense, lexical)
    if not fused:
        return []

    # 回填完整信息（文档标题 / 来源），并按 chunk_id 建立索引以便与融合结果对齐。
    rows = await store.chunks_by_ids([item["chunk_id"] for item in fused])
    by_id = {row["chunk_id"]: row for row in rows}
    # 融合用的是分数，这里把原始记录挂到 item["row"] 上供后续过滤与输出使用。
    candidates = [dict(item, row=by_id[item["chunk_id"]]) for item in fused
                  if item["chunk_id"] in by_id]

    # 物种优先级：调用方显式指定（如改写器抽取） > 查询语句里出现 > 不过滤。
    target_species = normalize_species(pet_species or []) or _species_from_query(text)
    candidates = _apply_species(candidates, target_species)

    results: List[dict] = []
    for order, item in enumerate(candidates[:top_k], start=1):
        row = item["row"]
        results.append({
            "chunk_id": row["chunk_id"],
            "doc_id": row["doc_id"],
            "content": row["content"],
            "source": row["source"],
            "doc_title": row["doc_title"],
            "kind": row["kind"],
            "section": row["section"],
            # 分类缺失时用文档标题兜底：引用面板的分类列不应为空。
            "category": row["category"] or row["doc_title"],
            "species": row["species"],
            "snippet": snippet_of(row["content"]),
            "score": round(float(item["sim"]), 4),
            "matched": item["matched"],
            "matched_by": item["matched_by"],
            # rank 从 1 开始，与 Prompt 里的 [1] [2] 引用序号一一对应。
            "rank": order,
        })
    return results


def format_context(results: List[dict]) -> str:
    """把检索结果格式化成 Prompt 上下文。

    Args:
        results: ``retrieve`` 的返回值。

    Returns:
        多条片段以空行分隔的文本，每条以 ``[序号] 来源：文档｜小节`` 开头——
        序号是模型标注引用的唯一依据，格式不能随意改。

    Raises:
        无。
    """
    if not results:
        return ""
    lines = []
    for r in results:
        title = r.get("doc_title") or r.get("source") or "知识库"
        # 小节路径只取末级：完整路径（文档 > 章 > 节）太长，会挤占正文篇幅。
        section = (r.get("section") or "").split(" > ")[-1]
        where = f"{title}｜{section}" if section else title
        lines.append(f"[{r['rank']}] 来源：{where}\n{r['content']}")
    return "\n\n".join(lines)


async def _dense_channel(query: str, recall_k: int) -> List[dict]:
    """向量召回通道。

    Args:
        query: 检索问句。
        recall_k: 召回条数。

    Returns:
        候选列表（``sim`` 为余弦相似度，``matched`` 恒为 0——该通道没有「命中词数」概念）。

    Raises:
        RagUnavailable: 向量化或向量检索失败。
    """
    try:
        vector = await _embed_query(query)
        rows = await store.dense_search(vector, recall_k)
    except Exception as exc:
        logger.warning("向量检索通道失败", exc_info=True)
        raise RagUnavailable("知识库检索失败：向量召回异常") from exc
    return [{"chunk_id": r["chunk_id"], "sim": r["sim"], "matched": 0,
             "matched_by": "vector"} for r in rows]


async def _lexical_channel(query: str, extra_terms: Sequence[str],
                           recall_k: int) -> List[dict]:
    """BM25 关键词召回通道。

    关键词通道的价值在精确术语：「黑下巴」「氯菊酯」「MDR1」这类词向量模型常常召回不准，
    但字面命中非常可靠。

    Args:
        query: 检索问句。
        extra_terms: 附加词（品种等），一并分词参与检索。
        recall_k: 召回条数。

    Returns:
        候选列表（``sim`` 恒为 0——该通道没有语义相似度，只用 ``matched`` 表达命中词数）。

    Raises:
        无——索引未就绪时返回空列表，由向量通道单独支撑。
    """
    index = await lexicon.get_index()
    if index is None or index.total == 0:
        # 没装 jieba 或库里没数据：静默退化为纯向量检索，不阻断问答。
        return []
    terms = lexicon.tokenize(query) + [t for e in extra_terms for t in lexicon.tokenize(str(e))]
    if not terms:
        return []
    hits = index.search(terms, recall_k)
    return [{"chunk_id": cid, "sim": 0.0, "matched": matched,
             "matched_by": "keyword"} for cid, _score, matched in hits]


async def _embed_query(query: str) -> List[float]:
    """把查询语句向量化。

    Args:
        query: 检索问句。

    Returns:
        查询向量。

    Raises:
        RagUnavailable: 未配置 Embedding、客户端初始化失败或调用失败。
    """
    if not settings.embedding_enabled:
        raise RagUnavailable(
            "知识库检索不可用：请在 ai-service/.env 配置 EMBEDDING_API_KEY / EMBEDDING_MODEL")
    try:
        embeddings = get_embeddings()
    except Exception as exc:
        raise RagUnavailable("知识库检索不可用：Embedding 客户端初始化失败") from exc
    try:
        # embed_query 是同步阻塞调用，放线程池以免阻塞事件循环。
        return await asyncio.to_thread(embeddings.embed_query, query)
    except Exception as exc:
        logger.warning("查询向量化失败", exc_info=True)
        raise RagUnavailable("知识库检索失败：问题向量化异常") from exc


def _fuse(dense: List[dict], lexical: List[dict]) -> List[dict]:
    """融合两条通道的候选，并按双门槛过滤。

    融合方式：把「通道内排名」线性映射为 0~1 的序值（第 1 名 1.0，第 n 名线性递减），
    再按 ``RAG_FUSION_ALPHA`` 加权求和。这样两个量纲完全不同的分数变得可比。

    Args:
        dense: 向量通道候选（已按相似度降序）。
        lexical: 关键词通道候选（已按 BM25 分数降序）。

    Returns:
        融合后的候选列表（按融合分降序），每项额外带 ``score``。

    Raises:
        无。
    """
    depth = max(1, settings.RAG_RECALL_K)
    fused: Dict[int, dict] = {}
    for rank, item in enumerate(dense, start=1):
        fused[item["chunk_id"]] = {"chunk_id": item["chunk_id"], "sim": item["sim"],
                                   "matched": 0, "matched_by": "vector",
                                   "d_norm": 1.0 - (rank - 1) / depth, "l_norm": 0.0}
    for rank, item in enumerate(lexical, start=1):
        entry = fused.get(item["chunk_id"])
        if entry is None:
            entry = fused[item["chunk_id"]] = {"chunk_id": item["chunk_id"], "sim": 0.0,
                                               "matched": 0, "matched_by": "keyword",
                                               "d_norm": 0.0, "l_norm": 0.0}
        # 同一片段被两条通道同时召回时，保留向量相似度并标记 matched_by=both，
        # 融合分自然更高——这正是「双通道都认可」应有的效果。
        entry["l_norm"] = 1.0 - (rank - 1) / depth
        entry["matched"] = item["matched"]
        if entry["sim"] > 0:
            entry["matched_by"] = "both"

    # 权重做一次夹取：配置写错（如 1.5）不应让融合公式失真。
    alpha = min(1.0, max(0.0, settings.RAG_FUSION_ALPHA))
    kept = []
    for entry in fused.values():
        strong_vector = entry["sim"] >= settings.RAG_MIN_SIM
        strong_keyword = entry["matched"] >= settings.RAG_LEX_MIN_TERMS
        # 双门槛取「或」：语义够近、或关键词命中够多，任一满足即可保留。
        # 关键词门槛是精确术语的兜底——它们的向量相似度往往不高但确实正确。
        if not (strong_vector or strong_keyword):
            continue
        entry["score"] = alpha * entry["d_norm"] + (1.0 - alpha) * entry["l_norm"]
        kept.append(entry)

    # 融合分相同时用向量相似度作次级排序键：融合分是离散的排名序值，容易并列。
    kept.sort(key=lambda e: (e["score"], e["sim"]), reverse=True)
    return kept


def _apply_species(candidates: List[dict], target_species: List[str]) -> List[dict]:
    """按物种做过滤或降权。

    Args:
        candidates: 融合后的候选（每项含 ``row``）。
        target_species: 目标物种；为空时不做处理。

    Returns:
        处理后的候选（重新按分数降序）。

    Raises:
        无。
    """
    if not target_species:
        return candidates
    out: List[dict] = []
    for item in candidates:
        if species_compatible(item["row"].get("species") or [], target_species):
            out.append(item)
            continue
        if settings.RAG_SPECIES_STRICT:
            continue
        # 默认降权而非直接丢弃：通用护理类文档的 species 可能标得不全，
        # 硬过滤会误杀有用的通用知识。
        adjusted = dict(item)
        adjusted["score"] = item["score"] * settings.RAG_SPECIES_PENALTY
        out.append(adjusted)
    out.sort(key=lambda e: (e["score"], e["sim"]), reverse=True)
    return out


def _species_from_query(query: str) -> List[str]:
    """从查询语句里识别物种。

    Args:
        query: 检索问句。

    Returns:
        物种列表（仅「猫」「狗」）；未识别到时为空列表。

    Raises:
        无。
    """
    found: List[str] = []
    lowered = query.lower()
    # 只覆盖猫狗：语料与 species 标注目前也只区分这两类（加「通用」「其他」）。
    if any(w in query for w in ("猫", "喵", "cat")) or "cat" in lowered:
        found.append("猫")
    if any(w in query for w in ("狗", "犬", "汪")) or "dog" in lowered:
        found.append("狗")
    return found


def snippet_of(content: str, limit: int = SNIPPET_CHARS) -> str:
    """生成片段摘要（用于引用面板展示）。

    Args:
        content: 片段正文（首行是「文档｜分类｜小节」上下文前缀）。
        limit: 摘要最大字符数。

    Returns:
        去掉 Markdown 噪声、空白压平后的摘要，超长时以省略号结尾。

    Raises:
        无。
    """
    # 丢掉首行上下文前缀：引用面板已单独展示来源信息，重复显示会浪费空间。
    body = content.split("\n", 1)[1] if "\n" in content else content
    body = " ".join(_MD_NOISE.sub(" ", " ".join(body.split())).split())
    return body[:limit] + ("…" if len(body) > limit else "")
