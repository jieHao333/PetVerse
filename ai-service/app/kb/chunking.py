"""知识片段切分（小节感知 + 上下文标题前缀）

RAG 召回质量的第一个决定因素是「片段是否自带完整语义」。本项目原先直接用
RecursiveCharacterTextSplitter 按长度硬切整篇文档，会出现两类漏召：

  1. 片段丢失上下文——「幼猫在 8-9 周龄接种第一针」里没有文档名和小节标题，
     向量与关键词都难以和「两个月小猫什么时候打疫苗」对上；
  2. 结构被切断——表格的表头与数据行分家、一条 FAQ 被切成两段。

因此本模块以「小节」为单位切分，并在每个片段前拼接上下文标题行
（文档标题 · 小节全路径），表格切分时重复表头，过短小节与相邻小节合并。

片段长度按中文字符数计（1 个汉字 = 1）：目标 600，允许上限 900，
低于 80 的孤立小节合并到同父级相邻片段。
"""
import re
from dataclasses import dataclass, field
from typing import List

from app.kb.documents import ParsedDocument, Section

# 句子边界（中文标点优先，兼顾英文标点与换行）
_SENT_SPLIT = re.compile(r"(?<=[。！？；?!;\n])")
# Markdown 表格行
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


@dataclass
class Chunk:
    """一个待入库的知识片段"""

    content: str            # 片段正文（已含上下文标题前缀，向量化与注入 Prompt 都用它）
    chunk_index: int        # 文档内序号（稳定排序与去重用）
    doc_id: str
    source: str             # 文件名（前端来源标签展示）
    doc_title: str          # 文档标题
    section: str            # 小节全路径（前端来源标签展示「出自哪一节」）
    category: str
    species: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)


def split_document(doc: ParsedDocument, chunk_size: int = 600,
                   chunk_min: int = 80, chunk_max: int = 900) -> List[Chunk]:
    """把一篇解析好的文档切成带上下文的片段列表"""
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
    """片段头部的上下文行：文档标题 · 分类 · 小节路径（检索与注入都靠它补足语义）"""
    parts = [doc.title]
    if doc.category:
        parts.append(doc.category)
    if section_path:
        parts.append(section_path.split(" > ")[-1])
    return "｜".join(p for p in parts if p)


def _merge_short_sections(sections: List[Section], chunk_size: int,
                          chunk_min: int) -> List[Section]:
    """把过短的小节并入前一个同级小节，避免产生没有独立语义的碎片"""
    out: List[Section] = []
    for sec in sections:
        if out and len(sec.text) < chunk_min:
            prev = out[-1]
            if len(prev.text) + len(sec.text) <= chunk_size:
                out[-1] = Section(prev.path, f"{prev.text}\n{sec.text}")
                continue
        out.append(sec)
    return out


def _split_section(section: Section, chunk_size: int, chunk_max: int) -> List[str]:
    """小节内切分：整段能放下就不切；放不下时按句子累积，表格按行切并重复表头"""
    text = section.text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    lines = text.splitlines()
    if any(_TABLE_ROW.match(l) for l in lines):
        return _split_table_block(lines, chunk_size, chunk_max)

    return _split_by_sentences(text, chunk_size, chunk_max)


def _split_by_sentences(text: str, chunk_size: int, chunk_max: int) -> List[str]:
    """按句子累积到目标长度；相邻片段保留一句重叠，避免跨片段语义断裂"""
    sentences = [s for s in _SENT_SPLIT.split(text) if s and s.strip()]
    if not sentences:
        return [text[:chunk_max]]

    pieces: List[str] = []
    current: List[str] = []
    length = 0
    for sent in sentences:
        # 单句超长（少见的无标点长句）时硬切，保证不超过上限
        if len(sent) > chunk_max:
            if current:
                pieces.append("".join(current))
                current, length = [], 0
            for start in range(0, len(sent), chunk_max):
                pieces.append(sent[start:start + chunk_max])
            continue
        if length + len(sent) > chunk_size and current:
            pieces.append("".join(current))
            # 重叠一句：下一片段以上一句开头，保留断点上下文
            current = [current[-1], sent] if len(current) > 1 else [sent]
            length = sum(len(s) for s in current)
        else:
            current.append(sent)
            length += len(sent)
    if current:
        pieces.append("".join(current))
    return [p.strip() for p in pieces if p.strip()]


def _split_table_block(lines: List[str], chunk_size: int, chunk_max: int) -> List[str]:
    """表格与小节文字混排时：非表格部分正常切，表格按数据行分组并重复表头"""
    pieces: List[str] = []
    prose: List[str] = []
    table: List[str] = []

    def flush_prose() -> None:
        if not prose:
            return
        pieces.extend(_split_section(Section("", "\n".join(prose)), chunk_size, chunk_max))
        prose.clear()

    def flush_table() -> None:
        if not table:
            return
        header, rows = _extract_table_header(table)
        chunk = list(header)
        size = sum(len(r) for r in chunk)
        for row in rows:
            if size + len(row) > chunk_max and len(chunk) > len(header):
                pieces.append("\n".join(chunk))
                chunk = list(header) + [row]
                size = sum(len(r) for r in chunk)
            else:
                chunk.append(row)
                size += len(row)
        if len(chunk) > len(header):
            pieces.append("\n".join(chunk))
        table.clear()

    for line in lines:
        if _TABLE_ROW.match(line):
            flush_prose()
            table.append(line)
        else:
            flush_table()
            prose.append(line)
    flush_table()
    flush_prose()
    return [p for p in pieces if p.strip()]


def _extract_table_header(table_lines: List[str]) -> tuple[List[str], List[str]]:
    """识别表头行与分隔线（| --- | --- |），其余为数据行"""
    header: List[str] = []
    rows: List[str] = []
    for i, line in enumerate(table_lines):
        is_divider = bool(re.match(r"^\s*\|[\s:|-]+\|\s*$", line))
        if not header and not is_divider:
            header.append(line)
            if i + 1 < len(table_lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", table_lines[i + 1]):
                header.append(table_lines[i + 1])
            continue
        if is_divider and header:
            continue
        rows.append(line)
    return header or table_lines[:1], rows or table_lines[len(header):]
