"""知识文档解析（md / docx / pdf / doc）。

职责：把不同格式的文档统一解析成 ``ParsedDocument``——一个带标题层级的小节序列。
后续的切分、向量化都只依赖这个中间结构，因此新增格式只需在这里加一个解析器。

主要导出：
    - ``SUPPORTED_SUFFIXES``    支持的扩展名
    - ``DocParseError``         解析失败（格式不支持 / 文件损坏 / 无正文）
    - ``Section`` / ``ParsedDocument``  中间结构
    - ``parse_file`` / ``parse_bytes``  两种输入形态的解析入口
    - ``normalize_species`` / ``species_compatible``  物种归一化与兼容判断
    - ``document_text`` / ``preview_text``  纯文本导出与预览
    - ``_decode`` / ``_safe_doc_id``  编码与 ID 处理

外部依赖：``python-docx``（docx）、``PyMuPDF``（pdf）、系统命令 ``antiword`` / ``catdoc``（doc）。
后三者均为**可选**：缺失时抛 ``DocParseError`` 并给出安装提示，不做静默降级——
产出空文档比报错更难排查。

使用示例：
    from app.kb import documents

    doc = documents.parse_file(Path("app/knowledge/犬猫疫苗与免疫程序.md"))
    print(doc.title, doc.category, len(doc.sections))
"""

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

SUPPORTED_SUFFIXES = (".md", ".pdf", ".docx", ".doc")

# 物种别名归并表：文档里的写法五花八门（「犬」「dog」「幼猫」），
# 统一收敛成「猫/狗/通用/其他」四类，检索时的过滤规则才可用。
_SPECIES_ALIASES = {
    "猫": "猫", "家猫": "猫", "cat": "猫", "kitten": "猫", "幼猫": "猫", "成猫": "猫",
    "狗": "狗", "犬": "狗", "dog": "狗", "puppy": "狗", "幼犬": "狗", "成犬": "狗",
    "通用": "通用", "犬猫": "通用", "both": "通用", "all": "通用",
    "other": "其他", "其他": "其他",
}

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# YAML front matter：文件开头的 --- ... --- 块，用于声明 title / category / species / tags。
_FRONT_MATTER = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*\n?", re.S)
_META_LINE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
# docx 标题样式名，兼容中文版 Word 的「标题 1」。
_DOCX_HEADING = re.compile(r"(?:heading|标题)\s*([1-6])\s*$", re.I)


class DocParseError(Exception):
    """文档解析失败。

    携带面向用户的可读原因（格式不支持、依赖缺失、正文为空等），
    路由层会把它原样返回给管理员，便于修文档后重传。
    """

@dataclass
class Section:
    """文档小节：标题路径 + 正文。

    ``path`` 是 ``章 > 节 > 小节`` 形式的层级字符串，既用于片段上下文前缀，
    也便于在预览里还原结构。
    """

    path: str
    text: str


@dataclass
class ParsedDocument:
    """解析后的文档（切分与入库的统一输入）。"""

    doc_id: str
    source: str
    kind: str
    title: str
    category: str = ""
    species: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    sections: List[Section] = field(default_factory=list)
    sha256: str = ""

    @property
    def char_count(self) -> int:
        """正文字符总数（不含标题路径），用于列表页展示与规模统计。

        Returns:
            所有小节文本长度之和。

        Raises:
            无。
        """
        return sum(len(s.text) for s in self.sections)


def parse_file(path: Path, doc_id: Optional[str] = None,
               title: Optional[str] = None, category: Optional[str] = None,
               species: Optional[List[str]] = None) -> ParsedDocument:
    """解析磁盘上的文档文件。

    Args:
        path: 文件路径。
        doc_id: 覆盖自动推导的文档 ID（默认取文件名主干）。
        title: 覆盖文档标题（默认取 front matter / 文档元信息 / 文件名）。
        category: 覆盖分类。
        species: 覆盖适用物种（会先做归一化）。

    Returns:
        解析结果，``sha256`` 已按文件原始字节计算。

    Raises:
        DocParseError: 文件不存在、格式不支持或没有可入库正文。
    """
    path = Path(path)
    if not path.is_file():
        raise DocParseError(f"文件不存在：{path}")
    suffix = path.suffix.lower()
    # 先判格式再读文件：避免把大文件读进内存后才发现不支持。
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocParseError(
            f"不支持的文档格式 {suffix or '(无后缀)'}，目前支持 md / pdf / docx / doc")

    raw = path.read_bytes()
    # 哈希基于原始字节：增量导入靠它判断「内容是否真的变了」，
    # 用解析后的文本计算会被排版差异干扰。
    digest = hashlib.sha256(raw).hexdigest()
    stem = _safe_doc_id(path.stem)

    if suffix == ".md":
        doc = _parse_markdown(raw, path.name, stem)
    elif suffix == ".docx":
        doc = _parse_docx(raw, path.name, stem)
    elif suffix == ".pdf":
        doc = _parse_pdf(raw, path.name, stem)
    else:
        doc = _parse_legacy_doc(raw, path.name, stem)

    # 显式入参优先于文档内声明的元信息：管理员在页面上填的标题/分类应当覆盖文件内容。
    if doc_id:
        doc.doc_id = _safe_doc_id(doc_id)
    if title:
        doc.title = title.strip()
    if category:
        doc.category = category.strip()
    if species:
        doc.species = normalize_species(species)
    if not doc.sections:
        raise DocParseError(f"文档没有可入库的正文内容：{path.name}")
    doc.sha256 = digest
    return doc


def parse_bytes(filename: str, data: bytes, **kwargs) -> ParsedDocument:
    """解析上传的字节内容（无磁盘文件）。

    Args:
        filename: 原始文件名，用于推断格式与默认 doc_id。
        data: 文件字节。
        **kwargs: 覆盖字段（doc_id / title / category / species）。

    Returns:
        解析结果，``source`` 固定为上传文件名。

    Raises:
        DocParseError: 格式不支持或没有可入库正文。
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocParseError(
            f"不支持的文档格式 {suffix or '(无后缀)'}，目前支持 md / pdf / docx / doc")
    stem = _safe_doc_id(Path(filename).stem)
    digest = hashlib.sha256(data).hexdigest()

    if suffix == ".md":
        doc = _parse_markdown(data, filename, stem)
    elif suffix == ".docx":
        doc = _parse_docx(data, filename, stem)
    elif suffix == ".pdf":
        doc = _parse_pdf(data, filename, stem)
    else:
        doc = _parse_legacy_doc(data, filename, stem)
    if not doc.sections:
        raise DocParseError(f"文档没有可入库的正文内容：{filename}")
    doc.sha256 = digest
    doc.source = filename
    for k, v in kwargs.items():
        if v:
            # species 需要归一化成标准值，其它字段按字符串处理。
            setattr(doc, k, normalize_species(v) if k == "species" else str(v).strip())
    if kwargs.get("doc_id"):
        # doc_id 在上面被 str.strip() 处理过，这里再走一次安全化（去标点、保中文）。
        doc.doc_id = _safe_doc_id(kwargs["doc_id"])
    return doc


def normalize_species(values) -> List[str]:
    """把各种写法的物种归一成标准取值。

    Args:
        values: 字符串（逗号/顿号分隔）或序列；None 视为空。

    Returns:
        去重后的标准物种列表，取值限定在 猫 / 狗 / 通用 / 其他。

    Raises:
        无。
    """
    if values is None:
        return []
    if isinstance(values, str):
        # 同时支持中英文分隔符与方括号：前端表单可能直接传 "[猫, 狗]"。
        values = re.split(r"[,，、;；\[\]]", values)
    out: List[str] = []
    for v in values or []:
        token = str(v).strip().strip("\"'").lower()
        if not token:
            continue
        mapped = _SPECIES_ALIASES.get(token)
        if mapped is None:
            # 别名表未覆盖时的启发式归属：宁可归到「猫/狗」，也不要轻易落到「其他」
            # （「其他」在检索里几乎不会被任何物种匹配到）。
            mapped = "猫" if "猫" in token else ("狗" if ("狗" in token or "犬" in token) else "其他")
        if mapped not in out:
            out.append(mapped)
    return out


def species_compatible(doc_species: List[str], target: List[str]) -> bool:
    """判断文档物种是否可用于目标物种的提问。

    Args:
        doc_species: 文档标注的物种。
        target: 提问方物种。

    Returns:
        True 表示兼容。任一方为空、或含「通用 / 其他」时不设限——
        通用文档对所有物种都适用，不能因为标注不全被过滤掉。

    Raises:
        无。
    """
    if not doc_species or not target:
        return True
    if "通用" in doc_species or "其他" in doc_species:
        return True
    if "通用" in target or "其他" in target:
        return True
    return bool(set(doc_species) & set(target))


def document_text(doc: ParsedDocument) -> str:
    """把文档还原成纯文本（小节标题 + 正文）。

    Args:
        doc: 解析结果。

    Returns:
        纯文本；小节标题只保留末级。

    Raises:
        无。
    """
    lines = []
    for s in doc.sections:
        if s.path:
            lines.append(s.path.split(" > ")[-1])
        lines.append(s.text)
    return "\n".join(lines).strip()


def preview_text(path: Path, title: str = "") -> Tuple[str, str]:
    """生成管理页预览内容。

    Args:
        path: 原文路径（重新解析，保证排版完整、无切分重叠）。
        title: 覆盖标题；为空时用文档自身标题。

    Returns:
        ``(格式, 正文)``：格式为 ``markdown`` 或 ``text``，前端据此决定是否按 Markdown 渲染。

    Raises:
        DocParseError: 解析失败（调用方会回退到片段拼接）。
    """
    doc = parse_file(path)
    heading = (title or "").strip() or doc.title
    if doc.kind != "md":
        # 非 markdown 一律按纯文本：把 docx/pdf 的正文当 markdown 渲染会出现意外的加粗/列表。
        return "text", f"{heading}\n\n{document_text(doc)}".strip()
    # markdown 重新拼成「一级标题 + 各小节二级标题 + 正文」，还原可读的层级外观。
    lines = [f"# {heading}", ""]
    for section in doc.sections:
        leaf = section.path.split(" > ")[-1] if section.path else ""
        if leaf:
            lines += [f"## {leaf}", ""]
        lines += [section.text, ""]
    return "markdown", "\n".join(lines).strip()


def _parse_markdown(raw: bytes, filename: str, stem: str) -> ParsedDocument:
    """解析 Markdown：front matter + 标题层级切分。

    Args:
        raw: 文件字节。
        filename: 原始文件名（作为 ``source``）。
        stem: 默认 doc_id。

    Returns:
        解析结果。

    Raises:
        无（解析问题以「无 sections」体现，由上层统一报错）。
    """
    text = _decode(raw)
    meta, body = _split_front_matter(text)

    title = (meta.get("title") or "").strip()
    category = (meta.get("category") or "").strip()
    species = normalize_species(_split_list(meta.get("species", "")))
    # aliases 与 tags 合并进同一个词表：两者都是「正文里可能没出现的同义词」，
    # 对 BM25 召回的价值相同。
    tags = _split_list(meta.get("tags", "")) + _split_list(meta.get("aliases", ""))

    doc = ParsedDocument(doc_id=stem, source=filename, kind="md",
                         title=title or stem, category=category,
                         species=species, tags=[t for t in tags if t])

    lines = body.splitlines()
    heading_stack: List[Tuple[int, str]] = []
    buffer: List[str] = []
    fence = False

    def flush(path_headings: List[Tuple[int, str]]) -> None:
        """把缓冲区内容作为一个小节落盘（空内容不产生小节）。"""
        content = "\n".join(buffer).strip()
        buffer.clear()
        if content:
            doc.sections.append(Section(_heading_path(path_headings), content))

    for line in lines:
        # 代码块内的 # 不是标题，用 fence 状态跳过，避免把注释行当成章节切点。
        if line.strip().startswith("```"):
            fence = not fence
            buffer.append(line)
            continue
        matched = None if fence else _MD_HEADING.match(line)
        if matched:
            level = len(matched.group(1))
            heading = matched.group(2).strip().rstrip("#").strip()
            flush(heading_stack)
            # 保留层级更浅的标题：遇到二级标题时，一级标题仍属于当前路径。
            heading_stack = [h for h in heading_stack if h[0] < level]
            # 一级标题若与文档标题相同则不入路径，避免片段前缀出现重复的文档名。
            if not (level == 1 and heading and heading == doc.title):
                heading_stack.append((level, heading))
        else:
            buffer.append(line)
    flush(heading_stack)

    if not title and doc.sections:
        # 没声明 title 时用第一个小节的顶级标题兜底，比文件名更可读。
        first = doc.sections[0].path.split(" > ")[0]
        if first:
            doc.title = first
    return doc


def _split_front_matter(text: str) -> Tuple[dict, str]:
    """分离 YAML front matter 与正文。

    Args:
        text: 全文。

    Returns:
        ``(元信息字典, 正文)``；没有 front matter 时返回 ``({}, 原文)``。

    Raises:
        无。
    """
    matched = _FRONT_MATTER.match(text)
    if not matched:
        return {}, text
    meta: dict = {}
    for line in matched.group(1).splitlines():
        kv = _META_LINE.match(line.strip())
        if kv:
            # 只取「键: 值」简单结构，不引入 yaml 依赖；数组值放在同一行用逗号分隔。
            meta[kv.group(1).strip().lower()] = kv.group(2).strip()
    return meta, text[matched.end():]


def _split_list(value: str) -> List[str]:
    """按中英文分隔符拆分列表型元信息。

    Args:
        value: 原始字符串（可能带 ``[]`` / ``()`` 包裹）。

    Returns:
        去空白后的字符串列表。

    Raises:
        无。
    """
    if not value:
        return []
    return [v.strip() for v in re.split(r"[,，、;；]+", value.strip().strip("[]()")) if v.strip()]


def _heading_path(stack: List[Tuple[int, str]]) -> str:
    """把标题栈拼成路径字符串。

    Args:
        stack: ``(层级, 标题)`` 列表。

    Returns:
        ``章 > 节 > 小节`` 形式；空栈返回空串。

    Raises:
        无。
    """
    return " > ".join(h for _, h in stack if h)


def _parse_docx(raw: bytes, filename: str, stem: str) -> ParsedDocument:
    """解析 Word（.docx）：按段落样式还原标题层级，表格转成文本。

    Args:
        raw: 文件字节。
        filename: 原始文件名。
        stem: 默认 doc_id。

    Returns:
        解析结果。

    Raises:
        DocParseError: 缺少 python-docx 依赖或文件损坏（常见于把 .doc 改名成 .docx）。
    """
    import io

    try:
        import docx
    except ImportError as exc:
        raise DocParseError("解析 docx 需要依赖 python-docx，请先 pip install python-docx") from exc

    try:
        document = docx.Document(io.BytesIO(raw))
    except Exception as exc:
        raise DocParseError(f"docx 解析失败（文件可能已损坏或实为旧版 .doc）：{filename}，{exc}") from exc

    doc = ParsedDocument(doc_id=stem, source=filename, kind="docx", title=stem)
    core_title = (getattr(document.core_properties, "title", "") or "").strip()
    if core_title:
        doc.title = core_title

    stack: List[Tuple[int, str]] = []
    buffer: List[str] = []

    def flush() -> None:
        """把缓冲区内容作为一个小节落盘。"""
        content = "\n".join(buffer).strip()
        buffer.clear()
        if content:
            doc.sections.append(Section(_heading_path(stack), content))

    first_paragraph_heading = True
    for para in document.paragraphs:
        text = (para.text or "").strip()
        if not text:
            # 空段落不参与判断：Word 文档常有大量空行，不能让它们干扰「首段是否标题」。
            continue
        style_name = (para.style.name or "") if para.style is not None else ""
        level_match = _DOCX_HEADING.search(style_name)
        if level_match:
            level = int(level_match.group(1))
            flush()
            stack = [h for h in stack if h[0] < level]
            if not (level == 1 and text == doc.title):
                stack.append((level, text))
            elif level == 1 and first_paragraph_heading and text != doc.title:
                doc.title = text
        else:
            # 没有标题样式时，把「首段短文本」当作文档标题（中文文档的常见写法）。
            if first_paragraph_heading and len(text) <= 40:
                doc.title = text
            buffer.append(text)
        first_paragraph_heading = False

    for table in document.tables:
        rows = [[(c.text or "").strip() for c in r.cells] for r in table.rows]
        # 全空行没有信息量，直接剔除。
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        header = rows[0]
        lines = []
        for row in rows[1:]:
            # 把每行转成「表头：值；表头：值」的句子：向量化与关键词检索都只认连续文本，
            # 保留原始表格结构反而会让内容变成一堆竖线。
            pairs = [f"{h}：{v}" for h, v in zip(header, row) if v]
            if pairs:
                lines.append("；".join(pairs))
        if lines:
            buffer.append("\n".join(lines))
    flush()

    return doc


def _parse_pdf(raw: bytes, filename: str, stem: str) -> ParsedDocument:
    """解析 PDF：逐页抽取文本，页码作为小节路径。

    Args:
        raw: 文件字节。
        filename: 原始文件名。
        stem: 默认 doc_id。

    Returns:
        解析结果。

    Raises:
        DocParseError: 缺少 PyMuPDF 依赖或文件损坏。
    """
    try:
        import fitz
    except ImportError as exc:
        raise DocParseError("解析 pdf 需要依赖 PyMuPDF，请先 pip install pymupdf") from exc

    try:
        pdf = fitz.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise DocParseError(f"pdf 解析失败（文件可能已损坏）：{filename}，{exc}") from exc

    doc = ParsedDocument(doc_id=stem, source=filename, kind="pdf", title=stem)
    meta_title = (pdf.metadata or {}).get("title") or ""
    if meta_title.strip():
        doc.title = meta_title.strip()

    # 用 with 确保文件句柄释放：PDF 句柄泄漏会拖住 Windows 上的临时文件清理。
    with pdf:
        for page_no, page in enumerate(pdf, start=1):
            # sort=True 按阅读顺序输出，避免多栏排版被抽成错行文本。
            text = (page.get_text("text", sort=True) or "").strip()
            if text:
                doc.sections.append(Section(f"第 {page_no} 页", text))

    if doc.title == stem and doc.sections:
        # PDF 元信息常为空，退而取正文首行（短且像标题时才采用）。
        first_line = doc.sections[0].text.splitlines()[0].strip()
        if 0 < len(first_line) <= 40:
            doc.title = first_line
    return doc


def _parse_legacy_doc(raw: bytes, filename: str, stem: str) -> ParsedDocument:
    """解析旧版 Word（.doc，二进制格式）。

    Args:
        raw: 文件字节。
        filename: 原始文件名。
        stem: 默认 doc_id。

    Returns:
        解析结果（整篇作为「正文」一个小节）。

    Raises:
        DocParseError: 系统未安装 antiword / catdoc，或抽取失败 / 超时。
    """
    # .doc 是二进制复合文档，Python 侧没有可靠的纯 Python 解析器，只能借外部命令。
    tool = shutil.which("antiword") or shutil.which("catdoc")
    if not tool:
        raise DocParseError(
            f"旧版 .doc（{filename}）需要系统安装 antiword 或 catdoc 才能抽取文本，"
            "请用 Word 另存为 .docx 后重新导入")
    with _TempFile(raw, suffix=".doc") as tmp_path:
        # antiword 需要 -m UTF-8.txt 才能正确输出中文；catdoc 默认即 UTF-8。
        cmd = ([tool, "-m", "UTF-8.txt", str(tmp_path)] if "antiword" in tool
               else [tool, str(tmp_path)])
        try:
            # 60s 超时：文档损坏时工具可能卡住，不能让导入流程无限等待。
            result = subprocess.run(cmd, capture_output=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise DocParseError(f"{filename} 文本抽取失败：{exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise DocParseError(f"{tool} 抽取 {filename} 失败：{detail or result.returncode}")
    text = _decode(result.stdout)
    # 旧版 doc 没有结构信息，按空行切段即可。
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    doc = ParsedDocument(doc_id=stem, source=filename, kind="doc", title=stem)
    if paragraphs and len(paragraphs[0]) <= 40:
        doc.title = paragraphs[0]
        paragraphs = paragraphs[1:]
    body = "\n\n".join(paragraphs).strip()
    if body:
        doc.sections.append(Section("正文", body))
    return doc


class _TempFile:
    """把内存字节落到临时文件，供只接受路径的外部命令使用。

    退出时删除临时文件；删除失败（如被外部进程占用）只忽略，不抛异常。
    """

    def __init__(self, data: bytes, suffix: str = "") -> None:
        self._data = data
        self._suffix = suffix
        self._path: Optional[Path] = None

    def __enter__(self) -> Path:
        """写出临时文件。

        Returns:
            临时文件路径。

        Raises:
            OSError: 磁盘不可写时抛出。
        """
        from tempfile import NamedTemporaryFile

        # delete=False：Windows 下无法在文件仍被打开时交给别的进程读取。
        tmp = NamedTemporaryFile(suffix=self._suffix, delete=False)
        tmp.write(self._data)
        tmp.close()
        self._path = Path(tmp.name)
        return self._path

    def __exit__(self, *exc) -> None:
        """删除临时文件（失败忽略）。

        Returns:
            None。

        Raises:
            无。
        """
        if self._path is not None:
            try:
                self._path.unlink()
            except OSError:
                pass


def _decode(raw: bytes) -> str:
    """按常见编码依次尝试解码。

    Args:
        raw: 原始字节。

    Returns:
        解码后的文本；全部失败时用 utf-8 + replace 兜底，保证不抛异常（宁可乱码也不中断导入）。
        先试 utf-8-sig 是为了吃掉 BOM，否则首个标题会带不可见字符而匹配不上。

    Raises:
        无。
    """
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _safe_doc_id(stem: str) -> str:
    """把文件名转成安全的 doc_id。

    Args:
        stem: 文件名主干。

    Returns:
        仅保留字母数字、下划线与中文的 ID；清洗后为空时返回 ``doc``（保证 doc_id 非空）。

    Raises:
        无。
    """
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", stem.strip()).strip("_")
    return cleaned or "doc"
