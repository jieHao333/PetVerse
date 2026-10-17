"""混合检索（向量召回 + BM25 关键词召回 → RRF 融合 → 物种过滤 → 召回门槛）

替换原先「只有 pgvector 余弦 top_k=4 + 相似度阈值 0.35」的单通道检索。原方案在
基线评测里的两类系统性漏召：

  1. 精确术语问不出来——「黑下巴」「伊维菌素」「柯利犬」这类低频专有词，
     纯向量会把它们平均成泛泛的养宠语义，命中排不进前 4；
  2. 阈值一刀切——只按余弦相似度截断，任何「文字完全对上但语义距离偏大」的
     片段会被整体丢掉，而这正是关键词通道最擅长召回的。

现在两条通道各取 RAG_RECALL_K 条，按「序值加权」融合（alpha 偏向向量通道，
关键词通道负责确认与兜底，见 _fuse 的说明）；再用宠物档案与提问里识别出的物种做
降权/过滤；最后按「语义相似 或 关键词命中词数」双条件设门槛（任一满足即保留）。

对外只暴露 retrieve() 与 format_context()，返回值每项都带可展示的引用来源字段，
供 SSE refs 事件、Prompt 注入与历史回放共用同一份数据。
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

# 引用片段在 Prompt / 前端展示时的截断长度
SNIPPET_CHARS = 180

# 摘要里要抹掉的 Markdown 噪声：强调符号与列表短横线（正文本身保留）
_MD_NOISE = re.compile(r"[*`#]+|\s+-\s+")


class RagUnavailable(Exception):
    """RAG 检索链路不可用（Embedding 未配置 / PG 或 pgvector 异常 / 知识库为空）"""


async def retrieve(query: str, k: Optional[int] = None,
                   pet_species: Any = None, extra_terms: Sequence[str] = ()) -> List[dict]:
    """混合检索一篇提问，返回 [{content, source, kind, section, score, ...}]，按相关度降序

    :param query: 检索用查询（已由调用方做过对话改写）
    :param k: 最终返回片段数，缺省取 RAG_TOP_K
    :param pet_species: 宠物档案物种或提问里的物种，用于元数据过滤/降权
    :param extra_terms: 额外关键词（如改写后的同义扩展），只进 BM25 通道
    :raises RagUnavailable: 检索链路异常或知识库为空
    """
    text = (query or "").strip()
    if not text:
        return []
    top_k = k or settings.RAG_TOP_K
    recall_k = max(top_k, settings.RAG_RECALL_K)

    dense = await _dense_channel(text, recall_k)
    lexical = await _lexical_channel(text, extra_terms, recall_k)
    if not dense and not lexical:
        # 两路都空且库里有内容，说明是链路问题；库里本来为空则单独给可解释的错误
        stats = await store.stats()
        if stats["chunks"] == 0:
            raise RagUnavailable(
                "知识库为空：请先执行 scripts/ingest_knowledge.py 导入知识文档")
        return []

    fused = _fuse(dense, lexical)
    if not fused:
        return []

    # 候选量 ≤ 2*recall_k（几十条），一次性回表补齐文档名/物种，再做物种处理
    rows = await store.chunks_by_ids([item["chunk_id"] for item in fused])
    by_id = {row["chunk_id"]: row for row in rows}
    candidates = [dict(item, row=by_id[item["chunk_id"]]) for item in fused
                  if item["chunk_id"] in by_id]

    target_species = normalize_species(pet_species or []) or _species_from_query(text)
    candidates = _apply_species(candidates, target_species)

    results: List[dict] = []
    for order, item in enumerate(candidates[:top_k], start=1):
        row = item["row"]
        results.append({
            "chunk_id": row["chunk_id"],
            "doc_id": row["doc_id"],
            "content": row["content"],
            # 来源展示字段：前端「参考来源」直接用这些，不需要再查库
            "source": row["source"],
            "doc_title": row["doc_title"],
            "kind": row["kind"],
            "section": row["section"],
            "category": row["category"] or row["doc_title"],
            "species": row["species"],
            "snippet": snippet_of(row["content"]),
            "score": round(float(item["sim"]), 4),
            "matched": item["matched"],
            "matched_by": item["matched_by"],
            "rank": order,
        })
    return results


def format_context(results: List[dict]) -> str:
    """把检索结果拼成注入 System Prompt 的知识块

    每片段前缀 [序号] 与来源，并要求模型按序号标注引用——这样前端拿到回答里的
    [1][2] 就能和「参考来源」列表一一对应，用户点进去能看到具体小节。
    """
    if not results:
        return ""
    lines = []
    for r in results:
        title = r.get("doc_title") or r.get("source") or "知识库"
        section = (r.get("section") or "").split(" > ")[-1]
        where = f"{title}｜{section}" if section else title
        lines.append(f"[{r['rank']}] 来源：{where}\n{r['content']}")
    return "\n\n".join(lines)


# ---------- 通道 ----------

async def _dense_channel(query: str, recall_k: int) -> List[dict]:
    """向量召回：Embedding + pgvector 余弦距离"""
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
    """关键词召回：BM25（分词含标题/标签扩展词，见 lexicon）"""
    index = await lexicon.get_index()
    if index is None or index.total == 0:
        return []
    terms = lexicon.tokenize(query) + [t for e in extra_terms for t in lexicon.tokenize(str(e))]
    if not terms:
        return []
    hits = index.search(terms, recall_k)
    return [{"chunk_id": cid, "sim": 0.0, "matched": matched,
             "matched_by": "keyword"} for cid, _score, matched in hits]


async def _embed_query(query: str) -> List[float]:
    if not settings.embedding_enabled:
        raise RagUnavailable(
            "知识库检索不可用：请在 ai-service/.env 配置 EMBEDDING_API_KEY / EMBEDDING_MODEL")
    try:
        embeddings = get_embeddings()
    except Exception as exc:
        raise RagUnavailable("知识库检索不可用：Embedding 客户端初始化失败") from exc
    try:
        return await asyncio.to_thread(embeddings.embed_query, query)
    except Exception as exc:
        logger.warning("查询向量化失败", exc_info=True)
        raise RagUnavailable("知识库检索失败：问题向量化异常") from exc


# ---------- 融合与过滤 ----------

def _fuse(dense: List[dict], lexical: List[dict]) -> List[dict]:
    """加权序值融合 + 召回门槛

    两条通道的分值量纲不可比（余弦相似度 0~1 与 BM25 任意正值），所以各自先转成
    「名次序值」（第 1 名 = 1，第 N 名 ≈ 0），再按 RAG_FUSION_ALPHA 加权：

        score = alpha * 向量序值 + (1 - alpha) * 关键词序值

    刻意不用纯 RRF：在千级以内的小语料上，向量通道本身已经很强，而 RRF 只看名次
    不看置信度，会让「关键词偶然撞词但语义并不相关」的片段压过最佳语义命中——
    黄金集实测 RRF 版 hit@1 比纯向量低 6 个点。alpha=0.75 时向量通道占主导，
    关键词通道负责两类事：把两路都认可的片段顶到第一，以及在向量整体不可信时
    （全都不过阈值）凭字面命中兜住长尾专有名词。同分时以余弦相似度再排一次。

    门槛用「或」：向量相似度过 RAG_MIN_SIM 保留；纯关键词命中要求至少
    RAG_LEX_MIN_TERMS 个查询词命中，避免一个偶然撞词就把无关片段推给模型。
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
        entry["l_norm"] = 1.0 - (rank - 1) / depth
        entry["matched"] = item["matched"]
        if entry["sim"] > 0:
            entry["matched_by"] = "both"

    alpha = min(1.0, max(0.0, settings.RAG_FUSION_ALPHA))
    kept = []
    for entry in fused.values():
        strong_vector = entry["sim"] >= settings.RAG_MIN_SIM
        strong_keyword = entry["matched"] >= settings.RAG_LEX_MIN_TERMS
        if not (strong_vector or strong_keyword):
            continue
        entry["score"] = alpha * entry["d_norm"] + (1.0 - alpha) * entry["l_norm"]
        kept.append(entry)

    kept.sort(key=lambda e: (e["score"], e["sim"]), reverse=True)
    return kept


def _apply_species(candidates: List[dict], target_species: List[str]) -> List[dict]:
    """物种一致性处理：文档标注了物种且与目标物种冲突时降权（或直接剔除）

    降权而不是剔除是刻意的：用户问「我家狗能不能吃这个」时，一篇通用喂养文档
    仍然有用；真正该被压下去的是「明确只写猫」的片段。RAG_SPECIES_STRICT=true
    时改为硬过滤，供物种混问很少的场景使用。
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
        adjusted = dict(item)
        adjusted["score"] = item["score"] * settings.RAG_SPECIES_PENALTY
        out.append(adjusted)
    out.sort(key=lambda e: (e["score"], e["sim"]), reverse=True)
    return out


def _species_from_query(query: str) -> List[str]:
    """提问里显式提到猫/狗时以提问为准（用户可能在替朋友的猫问，而不是当前宠物）"""
    found: List[str] = []
    lowered = query.lower()
    if any(w in query for w in ("猫", "喵", "cat")) or "cat" in lowered:
        found.append("猫")
    if any(w in query for w in ("狗", "犬", "汪")) or "dog" in lowered:
        found.append("狗")
    return found


def snippet_of(content: str, limit: int = SNIPPET_CHARS) -> str:
    """引用片段摘要（去掉上下文标题行与 Markdown 强调符号，前端展开时只看正文开头）"""
    body = content.split("\n", 1)[1] if "\n" in content else content
    body = " ".join(_MD_NOISE.sub(" ", " ".join(body.split())).split())
    return body[:limit] + ("…" if len(body) > limit else "")
