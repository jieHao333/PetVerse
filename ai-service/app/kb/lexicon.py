"""关键词检索通道（jieba 分词 + Okapi BM25，进程内索引）

向量检索的短板是「精确术语」：药名（伊维菌素、非泼罗尼）、疾病俗称（黑下巴、猫癣）、
品种（柯利、德文卷毛猫）这类低频词，Embedding 容易把它们平均化成「泛泛的养宠语义」，
而 BM25 按词频与逆文档频率打分，一个精确词命中就能把正确片段顶上来。

实现要点：
  - 分词：jieba（含宠物领域自定义词典 user_dict.txt，避免「黑下巴」「髌骨脱位」被切碎）；
  - 索引：常驻内存，语料为千级片段，构建一次 <1s；用 kb_meta 版本戳做失效判断，
    知识库有任何写入即在下一次检索前重建，多进程部署不会读到过期词表；
  - 打分：标准 Okapi BM25（k1=1.5, b=0.75），并返回命中的查询词数——
    融合阶段用它判断「纯关键词命中」是否足够可信（见 search.py 的召回门槛）。
"""
import logging
import math
import re
import threading
from typing import Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

# BM25 参数：k1 控制词频饱和，b 控制长度归一
_K1 = 1.5
_B = 0.75

# 参与打分的最低查询词覆盖率：低于此比例视为「偶然撞词」
_MIN_QUERY_TERM_HIT_RATIO = 0.2

# 停用词（高频虚词与疑问词）：它们没有区分度，计入分母只会稀释得分
_STOPWORDS = {
    "的", "了", "是", "在", "和", "也", "就", "都", "要", "还", "会", "可以", "什么", "怎么",
    "如何", "为什么", "为啥", "哪", "哪些", "多久", "几次", "多少", "是不是", "能不能",
    "有无", "以及", "或者", "但", "但是", "因为", "所以", "如果", "这", "那", "这个", "那个",
    "我们", "你们", "他们", "它", "他", "她", "自己", "一个", "一下", "有点", "挺", "很",
    "啊", "呀", "吧", "呢", "吗", "哦", "嗯", "，", "。", "？", "！", "、", "；", "：",
}
_PUNCT = re.compile(r"[\W_]+")

_lock = threading.Lock()
_index: "LexIndex | None" = None
_index_version = -1


class LexIndex:
    """BM25 静态索引：chunk_id -> 词表，配合 df 与文档长度统计"""

    def __init__(self, rows: Sequence[Tuple[int, str]]) -> None:
        self.postings: Dict[str, Dict[int, int]] = {}
        self.doc_len: Dict[int, int] = {}
        self.ids: List[int] = []
        for chunk_id, tokens in rows:
            terms = [t for t in tokens.split() if t]
            self.ids.append(chunk_id)
            self.doc_len[chunk_id] = len(terms) or 1
            counter: Dict[str, int] = {}
            for term in terms:
                counter[term] = counter.get(term, 0) + 1
            for term, freq in counter.items():
                self.postings.setdefault(term, {})[chunk_id] = freq
        self.total = len(self.ids)
        self.avg_len = (sum(self.doc_len.values()) / self.total) if self.total else 1.0

    def search(self, terms: Sequence[str], limit: int) -> List[Tuple[int, float, int]]:
        """返回 [(chunk_id, bm25_score, 命中的查询词数)]，按得分降序取前 limit 条"""
        if not self.total or not terms:
            return []
        query_terms = list(dict.fromkeys(terms))   # 去重，保持命中计数语义
        query_set = set(query_terms)
        scores: Dict[int, float] = {}
        matched: Dict[int, int] = {}
        for term in query_terms:
            posting = self.postings.get(term)
            if not posting:
                continue
            df = len(posting)
            # 逆文档频率（BM25+ 形式，恒为非负）
            idf = math.log(1 + (self.total - df + 0.5) / (df + 0.5))
            for chunk_id, freq in posting.items():
                length = self.doc_len[chunk_id]
                denom = freq + _K1 * (1 - _B + _B * length / self.avg_len)
                scores[chunk_id] = scores.get(chunk_id, 0.0) + idf * freq * (_K1 + 1) / denom
                matched[chunk_id] = matched.get(chunk_id, 0) + 1

        min_hits = max(1, math.ceil(len(query_terms) * _MIN_QUERY_TERM_HIT_RATIO))
        ranked = [(cid, score, matched[cid]) for cid, score in scores.items()
                  if matched[cid] >= min_hits]
        ranked.sort(key=lambda x: x[1], reverse=True)
        _ = query_set
        return ranked[:limit]


def tokenize(text: str) -> List[str]:
    """分词并清洗：jieba 精确切分 + 去停用词/标点 + 小写；结果用于建索引与查询"""
    jieba = _get_jieba()
    if jieba is None:
        return _fallback_tokenize(text)
    parts = jieba.lcut(text or "")
    out = []
    for part in parts:
        token = _PUNCT.sub("", part).strip().lower()
        if not token or token in _STOPWORDS or len(token) > 24:
            continue
        # 单字（除数字）区分度太低，交给向量通道兜语义
        if len(token) == 1 and not token.isdigit():
            continue
        out.append(token)
    return out


def index_terms(text: str, extra_terms: Sequence[str] = ()) -> str:
    """生成入库用的 tokens 字符串：正文分词 + 文档标题/标签等扩展词

    标签（如「黑下巴、毛囊炎、猫痤疮」）不在正文里出现，但要能被搜到，
    所以与正文分词结果合并进同一列。
    """
    terms = tokenize(text)
    for extra in extra_terms or ():
        terms.extend(tokenize(str(extra)))
    return " ".join(terms)


def _get_jieba():
    global _jieba
    if _jieba is None:
        try:
            import jieba
            jieba.setLogLevel(logging.ERROR)
            from pathlib import Path
            dict_path = Path(__file__).resolve().parent / "user_dict.txt"
            if dict_path.is_file():
                jieba.load_userdict(str(dict_path))
            jieba.initialize()
            _jieba = jieba
        except ImportError:
            logger.warning("未安装 jieba，关键词检索通道退化为字符二元组切分")
            _jieba = False
    return _jieba if _jieba else None


_jieba = None


def _fallback_tokenize(text: str) -> List[str]:
    """无 jieba 时的兜底：中文按相邻双字、英文数字按词切（召回可用，质量略低）"""
    cleaned = _PUNCT.sub(" ", (text or "").lower())
    out: List[str] = []
    for token in cleaned.split():
        if re.search(r"[\u4e00-\u9fff]", token):
            if len(token) == 1:
                continue
            out.extend(token[i:i + 2] for i in range(len(token) - 1))
        elif len(token) > 1:
            out.append(token)
    return [t for t in out if t not in _STOPWORDS]


async def get_index():
    """取当前 BM25 索引；知识库版本变化时在线程池中重建（构建期间不阻塞事件循环）"""
    global _index, _index_version
    from app.kb import store

    current = await store.version()
    if _index is not None and _index_version == current:
        return _index
    with _lock:
        if _index is not None and _index_version == current:
            return _index
        rows = await store.lexicon_rows()
        _index = LexIndex(rows)
        _index_version = current
        logger.info("BM25 关键词索引已构建：片段 %s 条，词表 %s 项",
                    _index.total, len(_index.postings))
    return _index


def invalidate() -> None:
    """主动失效（导入/删除后立即生效，不必等下一次版本比对）"""
    global _index, _index_version
    _index = None
    _index_version = -1
