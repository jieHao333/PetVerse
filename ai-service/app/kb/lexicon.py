"""中文分词与进程内 BM25 索引。

职责：为知识库片段建一份内存倒排索引，支持关键词（BM25）召回。索引按需惰性构建，
并以 ``kb_meta.version`` 判断是否过期——数据一变就整份重建，避免增量维护的复杂度。

主要导出：
    - ``LexIndex``       倒排索引与 BM25 打分
    - ``tokenize``       中文分词（jieba，缺失时退化为二元组切分）
    - ``index_terms``    生成片段的索引词串（入库时写入 ``kb_chunk.tokens``）
    - ``get_index``      取得（并按版本重建）索引
    - ``invalidate``     主动失效（删文档 / 重建后调用）

外部依赖：``jieba``（可选）、``app.kb.store``（读取片段 tokens 与版本号）。

使用示例：
    from app.kb import lexicon

    index = await lexicon.get_index()
    hits = index.search(lexicon.tokenize("猫耳朵里有咖啡渣"), limit=20)
    # [(chunk_id, bm25_score, matched_term_count), ...]

为什么索引放在进程内：知识库规模在千级片段量级，全量构建只需毫秒级；改成数据库全文检索
会引入额外的表与迁移成本，收益不明显。
"""

import logging
import math
import re
import threading
from typing import Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

# BM25 标准参数：K1 控制词频饱和速度，B 控制文档长度归一化强度，取值沿用经典默认。
_K1 = 1.5
_B = 0.75

# 查询词命中比例下限：长问句不该要求「所有词都命中」，否则几乎召回不到；
# 取 20% 并向上取整，且至少命中 1 个词。
_MIN_QUERY_TERM_HIT_RATIO = 0.2

# 停用词表：中文虚词与常见疑问词。它们几乎出现在任何问句里，
# 保留只会让所有片段都被「命中」，反而稀释了区分度。
_STOPWORDS = {
    "的", "了", "是", "在", "和", "也", "就", "都", "要", "还", "会", "可以", "什么", "怎么",
    "如何", "为什么", "为啥", "哪", "哪些", "多久", "几次", "多少", "是不是", "能不能",
    "有无", "以及", "或者", "但", "但是", "因为", "所以", "如果", "这", "那", "这个", "那个",
    "我们", "你们", "他们", "它", "他", "她", "自己", "一个", "一下", "有点", "挺", "很",
    "啊", "呀", "吧", "呢", "吗", "哦", "嗯", "，", "。", "？", "！", "、", "；", "：",
}
_PUNCT = re.compile(r"[\W_]+")

# 索引是进程级共享对象，重建时用锁串行化，避免并发请求各建一份。
_lock = threading.Lock()
_index: "LexIndex | None" = None
_index_version = -1


class LexIndex:
    """片段级 BM25 倒排索引。

    ``postings[term][chunk_id] = 词频``，配合 ``doc_len`` 与平均长度即可计算 BM25 分数。
    """

    def __init__(self, rows: Sequence[Tuple[int, str]]) -> None:
        """构建索引。

        Args:
            rows: ``(chunk_id, tokens)`` 序列，``tokens`` 是空格分隔的词串。

        Raises:
            无。
        """
        self.postings: Dict[str, Dict[int, int]] = {}
        self.doc_len: Dict[int, int] = {}
        self.ids: List[int] = []
        for chunk_id, tokens in rows:
            terms = [t for t in tokens.split() if t]
            self.ids.append(chunk_id)
            # 长度为 0 时记为 1：后面要做除法，不能出现 0 长度导致除零或分数爆炸。
            self.doc_len[chunk_id] = len(terms) or 1
            counter: Dict[str, int] = {}
            for term in terms:
                counter[term] = counter.get(term, 0) + 1
            for term, freq in counter.items():
                self.postings.setdefault(term, {})[chunk_id] = freq
        self.total = len(self.ids)
        self.avg_len = (sum(self.doc_len.values()) / self.total) if self.total else 1.0

    def search(self, terms: Sequence[str], limit: int) -> List[Tuple[int, float, int]]:
        """按 BM25 打分检索。

        Args:
            terms: 查询词列表（通常来自 ``tokenize``）。
            limit: 返回条数。

        Returns:
            ``(chunk_id, 分数, 命中词数)`` 列表，按分数降序。命中词数供上层做「双门槛」
            判断——它比分数更直观，也不受索引规模影响。

        Raises:
            无。
        """
        if not self.total or not terms:
            return []
        # 查询词去重：重复词不该重复累加分数（"猫猫猫咪"与"猫咪"应等价）。
        query_terms = list(dict.fromkeys(terms))
        query_set = set(query_terms)
        scores: Dict[int, float] = {}
        matched: Dict[int, int] = {}
        for term in query_terms:
            posting = self.postings.get(term)
            if not posting:
                continue
            df = len(posting)
            # BM25 的 idf：df 越小（词越罕见）权重越高；加 0.5 平滑避免除零与小样本抖动。
            idf = math.log(1 + (self.total - df + 0.5) / (df + 0.5))
            for chunk_id, freq in posting.items():
                length = self.doc_len[chunk_id]
                # 长度归一化：长片段天然更容易命中词，需要按平均长度做惩罚。
                denom = freq + _K1 * (1 - _B + _B * length / self.avg_len)
                scores[chunk_id] = scores.get(chunk_id, 0.0) + idf * freq * (_K1 + 1) / denom
                matched[chunk_id] = matched.get(chunk_id, 0) + 1

        min_hits = max(1, math.ceil(len(query_terms) * _MIN_QUERY_TERM_HIT_RATIO))
        ranked = [(cid, score, matched[cid]) for cid, score in scores.items()
                  if matched[cid] >= min_hits]
        ranked.sort(key=lambda x: x[1], reverse=True)
        # TODO(待确认)：query_set 构造后仅在此处赋给占位变量、未参与任何计算，
        # 疑为同义词扩展或去重逻辑的预留；行为保持不变，待人工确认后再决定清理用途。
        _ = query_set
        return ranked[:limit]


def tokenize(text: str) -> List[str]:
    """中文分词并过滤停用词 / 单字。

    Args:
        text: 原始文本。

    Returns:
        词列表（已小写、去标点、去停用词、去单字）。

    Raises:
        无。
    """
    jieba = _get_jieba()
    if jieba is None:
        return _fallback_tokenize(text)
    parts = jieba.lcut(text or "")
    out = []
    for part in parts:
        # 用正则剥掉标点而不是只去首尾：分词结果常带全角符号。
        token = _PUNCT.sub("", part).strip().lower()
        if not token or token in _STOPWORDS or len(token) > 24:
            continue
        # 单字（除数字）丢弃：「猫」「狗」这类单字区分度太低，几乎命中所有片段。
        if len(token) == 1 and not token.isdigit():
            continue
        out.append(token)
    return out


def index_terms(text: str, extra_terms: Sequence[str] = ()) -> str:
    """生成片段的索引词串（写入 ``kb_chunk.tokens``）。

    Args:
        text: 片段正文。
        extra_terms: 额外词（文档标题 / 分类 / 标签 / 小节）。标签尤其重要：
            「黑下巴」「猫痤疮」这类俗称往往只写在标签里，正文没有原词，
            不补充进来就永远无法被关键词召回。

    Returns:
        空格分隔的词串。

    Raises:
        无。
    """
    terms = tokenize(text)
    for extra in extra_terms or ():
        terms.extend(tokenize(str(extra)))
    return " ".join(terms)


def _get_jieba():
    """惰性加载并初始化 jieba（只执行一次）。

    Returns:
        jieba 模块；未安装时返回 None（调用方退化为二元组切分）。

    Raises:
        无。
    """
    global _jieba
    if _jieba is None:
        try:
            import jieba
            # 关掉 jieba 的构建日志：它会在启动时刷屏，干扰业务日志阅读。
            jieba.setLogLevel(logging.ERROR)
            from pathlib import Path
            dict_path = Path(__file__).resolve().parent / "user_dict.txt"
            # 用户词典补充宠物领域专有词（品种、药品、症状），否则会被切碎。
            if dict_path.is_file():
                jieba.load_userdict(str(dict_path))
            jieba.initialize()
            _jieba = jieba
        except ImportError:
            logger.warning("未安装 jieba，关键词检索通道退化为字符二元组切分")
            # 用 False 而非 None 标记「已尝试且失败」，避免每次分词都重新 import 一次。
            _jieba = False
    return _jieba if _jieba else None


# 三态：None 未初始化 / False 不可用 / 模块对象 可用。
_jieba = None


def _fallback_tokenize(text: str) -> List[str]:
    """无 jieba 时的退化分词：中日韩文本按二元组切分。

    二元组无法理解词边界，但能保证「猫癣」「耳螨」这类两字词仍可被命中。

    Args:
        text: 原始文本。

    Returns:
        词列表。

    Raises:
        无。
    """
    cleaned = _PUNCT.sub(" ", (text or "").lower())
    out: List[str] = []
    for token in cleaned.split():
        if re.search(r"[\u4e00-\u9fff]", token):
            # 单个汉字没有二元组可切，直接丢弃（与 jieba 路径的去单字策略保持一致）。
            if len(token) == 1:
                continue
            out.extend(token[i:i + 2] for i in range(len(token) - 1))
        elif len(token) > 1:
            out.append(token)
    return [t for t in out if t not in _STOPWORDS]


async def get_index():
    """取得当前有效的 BM25 索引（版本不一致时重建）。

    采用双重检查：先用版本号无锁判断，命中就返回；不确定时才进锁复查并重建。

    Returns:
        ``LexIndex`` 实例；无数据时是一个空索引（``total == 0``）。

    Raises:
        Exception: 读取片段数据失败时透传（由检索层转成 RagUnavailable）。
    """
    global _index, _index_version
    from app.kb import store

    current = await store.version()
    if _index is not None and _index_version == current:
        return _index
    with _lock:
        # 等锁期间别的协程可能已完成重建，复查一次避免重复构建。
        if _index is not None and _index_version == current:
            return _index
        rows = await store.lexicon_rows()
        _index = LexIndex(rows)
        _index_version = current
        logger.info("BM25 关键词索引已构建：片段 %s 条，词表 %s 项",
                    _index.total, len(_index.postings))
    return _index


def invalidate() -> None:
    """标记索引失效（下次 ``get_index`` 时重建）。

    写操作后调用；不直接置空数据，而是把版本号设为 -1，使下一次调用必然重建。

    Returns:
        None。

    Raises:
        无。
    """
    global _index, _index_version
    _index = None
    _index_version = -1
