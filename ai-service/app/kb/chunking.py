"""文档片段切分。

职责：把解析后的 ``ParsedDocument``（按标题层级组织的小节）切成适合向量化与检索的片段，
并在每个片段前补一行「文档｜分类｜小节」上下文，使其脱离原文后仍能自我说明。

主要导出：
    - ``Chunk``            片段数据结构
    - ``split_document``   切分主入口

外部依赖：``app.kb.documents``（``ParsedDocument`` / ``Section``）。

使用示例：
    from app.kb.chunking import split_document

    chunks = split_document(doc, chunk_size=600, chunk_min=80, chunk_max=900)
    for c in chunks:
        print(c.chunk_index, len(c.content), c.section)

三档长度参数的配合：
    ``chunk_size`` 目标长度（尽量不超过）、``chunk_min`` 合并下限（更短的小节并入上一节）、
    ``chunk_max`` 硬上限（超长单句只能强制截断）。
"""

import re
from dataclasses import dataclass, field
from typing import List

from app.kb.documents import ParsedDocument, Section

# 按句切分：在句末标点后断开且保留标点（后向断言），英文句号/问号/感叹号与换行同样处理。
_SENT_SPLIT = re.compile(r"(?<=[。！？；?!;\n])")
# Markdown 表格行：以 | 开头并以 | 结尾。
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


@dataclass
class Chunk:
    """单个知识片段。

    ``content`` 已包含上下文前缀行；其余字段最终会落库到 ``kb_chunk``。
    """

    content: str
    chunk_index: int
    doc_id: str
    source: str
    doc_title: str
    section: str
    category: str
    species: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


def split_document(doc: ParsedDocument, chunk_size: int = 600,
                   chunk_min: int = 80, chunk_max: int = 900) -> List[Chunk]:
    """把文档切成片段列表。

    Args:
        doc: 已解析的文档。
        chunk_size: 目标片段长度（字符数）。
        chunk_min: 小节长度下限，短于此值尝试并入前一节。
        chunk_max: 片段硬上限，超长内容强制按此长度断开。

    Returns:
        片段列表，``chunk_index`` 从 0 连续递增。

    Raises:
        无。
    """
    # 先合并过短小节：一二级标题下只有一两句话时，单独成片段会缺乏上下文且浪费向量。
    merged = _merge_short_sections(doc.sections, chunk_size, chunk_min)
    chunks: List[Chunk] = []
    index = 0
    for section in merged:
        pieces = _split_section(section, chunk_size, chunk_max)
        for piece in pieces:
            prefix = _context_prefix(doc, section.path)
            body = piece.strip()
            if not body:
                continue
            chunks.append(Chunk(
                content=f"{prefix}\n{body}" if prefix else body,
                chunk_index=index,
                doc_id=doc.doc_id,
                source=doc.source,
                doc_title=doc.title,
                section=section.path,
                category=doc.category,
                species=list(doc.species),
                tags=list(doc.tags),
            ))
            index += 1
    return chunks


def _context_prefix(doc: ParsedDocument, section_path: str) -> str:
    """构造片段首行的上下文前缀。

    Args:
        doc: 文档。
        section_path: 小节路径（``章 > 节``）。

    Returns:
        ``文档标题｜分类｜小节`` 形式的字符串（缺项自动省略）。

    Raises:
        无。
    """
    parts = [doc.title]
    if doc.category:
        parts.append(doc.category)
    if section_path:
        # 只取末级小节名：完整路径太长，而检索时最有区分度的通常是叶子标题。
        parts.append(section_path.split(" > ")[-1])
    return "｜".join(p for p in parts if p)


def _merge_short_sections(sections: List[Section], chunk_size: int,
                          chunk_min: int) -> List[Section]:
    """把过短小节并入前一节。

    只与「紧邻的上一节」合并，不做全局聚合：跨章节合并会混淆语义，
    宁可让短小节独立存在。

    Args:
        sections: 原始小节列表。
        chunk_size: 合并后的长度上限。
        chunk_min: 触发合并的长度阈值。

    Returns:
        合并后的小节列表。

    Raises:
        无。
    """
    out: List[Section] = []
    for sec in sections:
        if out and len(sec.text) < chunk_min:
            prev = out[-1]
            # 合并后仍不能超过目标长度，否则会把一个短节塞进已满的上一节。
            if len(prev.text) + len(sec.text) <= chunk_size:
                # 用新 Section 覆盖而不是原地改 prev：避免修改解析结果的原始对象。
                out[-1] = Section(prev.path, f"{prev.text}\n{sec.text}")
                continue
        out.append(sec)
    return out


def _split_section(section: Section, chunk_size: int, chunk_max: int) -> List[str]:
    """切分单个小节。

    Args:
        section: 小节。
        chunk_size: 目标长度。
        chunk_max: 硬上限。

    Returns:
        文本片段列表。

    Raises:
        无。
    """
    text = section.text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    lines = text.splitlines()
    # 含表格的小节走表格专用切分：按句切会切断表格行，破坏「表头 + 数据行」的结构。
    if any(_TABLE_ROW.match(l) for l in lines):
        return _split_table_block(lines, chunk_size, chunk_max)

    return _split_by_sentences(text, chunk_size, chunk_max)


def _split_by_sentences(text: str, chunk_size: int, chunk_max: int) -> List[str]:
    """按句切分，并在片段间保留一句重叠。

    Args:
        text: 正文。
        chunk_size: 目标长度。
        chunk_max: 硬上限（超长单句按此长度硬切）。

    Returns:
        片段列表（已 strip 并剔除空白项）。

    Raises:
        无。
    """
    sentences = [s for s in _SENT_SPLIT.split(text) if s and s.strip()]
    if not sentences:
        # 没有任何句末标点（如纯列表、无标点长段落），只能按硬上限切。
        return [text[:chunk_max]]

    pieces: List[str] = []
    current: List[str] = []
    length = 0
    for sent in sentences:
        if len(sent) > chunk_max:
            # 单句超过硬上限：先收尾当前片段，再把长句按 chunk_max 均分，
            # 否则这个片段会突破向量化接口的长度限制。
            if current:
                pieces.append("".join(current))
                current, length = [], 0
            for start in range(0, len(sent), chunk_max):
                pieces.append(sent[start:start + chunk_max])
            continue
        if length + len(sent) > chunk_size and current:
            pieces.append("".join(current))
            # 重叠一句：把上一片段的最后一句带到下一片段开头，
            # 避免「因为…」和「所以…」被切到两个片段后语义断裂。
            current = [current[-1], sent] if len(current) > 1 else [sent]
            length = sum(len(s) for s in current)
        else:
            current.append(sent)
            length += len(sent)
    if current:
        pieces.append("".join(current))
    return [p.strip() for p in pieces if p.strip()]


def _split_table_block(lines: List[str], chunk_size: int, chunk_max: int) -> List[str]:
    """切分含表格的小节：普通文本按句切，表格按行切并在每片重复表头。

    Args:
        lines: 小节的所有行。
        chunk_size: 目标长度。
        chunk_max: 硬上限。

    Returns:
        片段列表。

    Raises:
        无。
    """
    pieces: List[str] = []
    prose: List[str] = []
    table: List[str] = []

    def flush_prose() -> None:
        """把累积的普通文本按句切分后加入结果。"""
        if not prose:
            return
        # 递归调用 _split_section：普通文本段落不含表格，会走按句切分分支。
        pieces.extend(_split_section(Section("", "\n".join(prose)), chunk_size, chunk_max))
        prose.clear()

    def flush_table() -> None:
        """把累积的表格按行切分后加入结果。"""
        if not table:
            return
        header, rows = _extract_table_header(table)
        # 每个表格片段都从表头开始：否则后续片段只有数据行，
        # 「| 2 | 4.5kg |」脱离表头后就失去了「这列是什么」的信息。
        chunk = list(header)
        size = sum(len(r) for r in chunk)
        for row in rows:
            # 只有「已经装了数据行」时才允许切分；否则单行超长会导致空转。
            if size + len(row) > chunk_max and len(chunk) > len(header):
                pieces.append("\n".join(chunk))
                chunk = list(header) + [row]
                size = sum(len(r) for r in chunk)
            else:
                chunk.append(row)
                size += len(row)
        # 只剩表头说明没有数据行，不必产出「空表」片段。
        if len(chunk) > len(header):
            pieces.append("\n".join(chunk))
        table.clear()

    for line in lines:
        if _TABLE_ROW.match(line):
            # 进入表格区：先收尾前面的普通文本，保证片段里不混排两种结构。
            flush_prose()
            table.append(line)
        else:
            # 离开表格区：先收尾表格，再累积文本。
            flush_table()
            prose.append(line)
    # 文件结尾可能停在任一状态，两种都要收尾。
    flush_table()
    flush_prose()
    return [p for p in pieces if p.strip()]


def _extract_table_header(table_lines: List[str]) -> tuple[List[str], List[str]]:
    """从表格行里分离表头（含分隔行）与数据行。

    Markdown 表格的结构是「表头行 + 分隔行（|---|）+ 数据行」，只有把分隔行也归入表头，
    每个片段才是一张语法完整的表格。

    Args:
        table_lines: 连续的表格行。

    Returns:
        ``(表头行列表, 数据行列表)``。

    Raises:
        无。
    """
    header: List[str] = []
    rows: List[str] = []
    for i, line in enumerate(table_lines):
        is_divider = bool(re.match(r"^\s*\|[\s:|-]+\|\s*$", line))
        if not header and not is_divider:
            header.append(line)
            # 表头行的下一行若是分隔行，一并归入表头。
            if i + 1 < len(table_lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", table_lines[i + 1]):
                header.append(table_lines[i + 1])
            continue
        if is_divider and header:
            # 已在表头里收录过的分隔行不重复计入数据行。
            continue
        rows.append(line)
    # 极端情况兜底：表格没有表头 / 全是分隔行时，仍要保证数据不丢。
    return header or table_lines[:1], rows or table_lines[len(header):]
