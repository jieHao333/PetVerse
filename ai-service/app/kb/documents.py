"""知识库文档解析（md / pdf / docx / doc -> 统一的小节结构）

RAG 语料支持四种来源格式，解析后统一成「标题层级 + 小节正文」，供切分模块使用：

  - md   : UTF-8 文本；可选头部元数据块（key: value，--- 包裹，支持 title / category /
           species / tags / aliases）；正文按 Markdown 标题（# / ## / ###）还原层级小节；
  - docx : python-docx 逐段读取，按段落样式名（Heading 1/2/3、标题 1/2/3）还原层级小节；
  - pdf  : PyMuPDF 逐页抽取文本，页码作为小节路径（PDF 无可靠标题结构）；
  - doc  : 旧版二进制 Word（Word 97-2003），调用系统 antiword 转文本后按纯文本处理；
           antiword 不存在时抛 DocParseError，提示先转存为 docx（不做静默降级——
           导入结果必须可解释，不能让一个空文档悄悄进库）。

元数据里的 species（适用物种）与 category 是检索阶段做过滤/加权的依据，缺失时留空，
表示「通用」——不因元数据缺失而被过滤掉。
"""
import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

# 支持的知识库文件后缀（小写，含点）
SUPPORTED_SUFFIXES = (".md", ".pdf", ".docx", ".doc")

# 物种归一化：文档元数据与运行时宠物档案都先归一到这三类再比较
_SPECIES_ALIASES = {
    "猫": "猫", "家猫": "猫", "cat": "猫", "kitten": "猫", "幼猫": "猫", "成猫": "猫",
    "狗": "狗", "犬": "狗", "dog": "狗", "puppy": "狗", "幼犬": "狗", "成犬": "狗",
    "通用": "通用", "犬猫": "通用", "both": "通用", "all": "通用",
    "other": "其他", "其他": "其他",
}

# Markdown 标题行：# ~ ######
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# 文档头部元数据块：--- \n key: value ... \n ---
_FRONT_MATTER = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*\n?", re.S)
# 元数据块内的一行：key: value（仅支持单层 key，够用且无需引 YAML 依赖）
_META_LINE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
# docx 段落样式中的标题层级（英文样式名 + 中文版 Word 样式名）
_DOCX_HEADING = re.compile(r"(?:heading|标题)\s*([1-6])\s*$", re.I)


class DocParseError(Exception):
    """文档解析失败（格式不支持 / 文件损坏 / 缺少外部转换工具）"""


@dataclass
class Section:
    """一个标题下的小节正文（切分以小节为单位，保证片段语义完整）"""

    path: str                       # 小节全路径，如「科学喂养 > 幼年阶段 > 断奶」
    text: str                       # 小节正文（不含标题行）


@dataclass
class ParsedDocument:
    """解析后的知识文档"""

    doc_id: str                     # 稳定标识（文件名主干，同名的重复导入按覆盖处理）
    source: str                     # 展示用文件名，如「皮肤与耳病.md」
    kind: str                       # md / pdf / docx / doc
    title: str                      # 文档标题（元数据 title 优先，否则取文件名主干）
    category: str = ""              # 分类标签，如「喂养」
    species: List[str] = field(default_factory=list)   # 适用物种（猫/狗/通用/其他），空=通用
    tags: List[str] = field(default_factory=list)       # 关键词标签，参与 BM25 词表扩展
    sections: List[Section] = field(default_factory=list)
    sha256: str = ""

    @property
    def char_count(self) -> int:
        return sum(len(s.text) for s in self.sections)


# ---------- 对外入口 ----------

def parse_file(path: Path, doc_id: Optional[str] = None,
               title: Optional[str] = None, category: Optional[str] = None,
               species: Optional[List[str]] = None) -> ParsedDocument:
    """按后缀解析单个文档文件；doc_id/title/category/species 为导入时的显式覆盖值

    上传接口会用显式覆盖值（上传人指定分类与物种），仓库目录导入则依赖文件自身元数据。
    """
    path = Path(path)
    if not path.is_file():
        raise DocParseError(f"文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocParseError(
            f"不支持的文档格式 {suffix or '(无后缀)'}，目前支持 md / pdf / docx / doc")

    raw = path.read_bytes()
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
    """解析内存中的文件字节（上传接口用，避免先落临时文件）"""
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
            setattr(doc, k, normalize_species(v) if k == "species" else str(v).strip())
    if kwargs.get("doc_id"):
        doc.doc_id = _safe_doc_id(kwargs["doc_id"])
    return doc


def normalize_species(values) -> List[str]:
    """物种归一化：接受字符串（含分隔符）或列表，输出规范词表且去重

    无法识别的物种归到「其他」；空输入返回空列表（语义是「未标注 / 通用」）。
    """
    if values is None:
        return []
    if isinstance(values, str):
        values = re.split(r"[,，、;；\[\]]", values)
    out: List[str] = []
    for v in values or []:
        token = str(v).strip().strip("\"'").lower()
        if not token:
            continue
        mapped = _SPECIES_ALIASES.get(token)
        if mapped is None:
            # 未命中别名表：含「猫」「狗」字样的仍按字面归类，其余归「其他」
            mapped = "猫" if "猫" in token else ("狗" if ("狗" in token or "犬" in token) else "其他")
        if mapped not in out:
            out.append(mapped)
    return out


def species_compatible(doc_species: List[str], target: List[str]) -> bool:
    """文档物种与目标物种是否兼容（任一方为空/通用 视为兼容）"""
    if not doc_species or not target:
        return True
    if "通用" in doc_species or "其他" in doc_species:
        return True
    if "通用" in target or "其他" in target:
        return True
    return bool(set(doc_species) & set(target))


def document_text(doc: ParsedDocument) -> str:
    """把解析结果还原成带标题的纯文本（评测与人工核对用）"""
    lines = []
    for s in doc.sections:
        if s.path:
            lines.append(s.path.split(" > ")[-1])
        lines.append(s.text)
    return "\n".join(lines).strip()


def preview_text(path: Path, title: str = "") -> Tuple[str, str]:
    """在线预览：重解析原文，返回 (格式, 正文)

    格式取 markdown（md 文档，前端渲染成排版后的 HTML）或 text（pdf/docx/doc
    抽出来的是线性文本，前端按 pre-wrap 显示）。
    title 传入登记标题时以它为准：pdf / docx 重解析只能拿到文件名主干或文档属性里的
    标题，与上传时在管理台登记的名称可能不一致，预览首行不该和列表对不上。

    这里刻意重新解析源文件而不是把 kb_chunk 片段拼回去：切分时相邻片段带一句重叠
    （为了不漏语义），拼起来会在预览里看到重复句子；片段拼接只作为原文缺失时的兜底。
    """
    doc = parse_file(path)
    heading = (title or "").strip() or doc.title
    if doc.kind != "md":
        # 非 Markdown 走纯文本视图，标题前不加 #（那会被当成正文的一部分显示出来）
        return "text", f"{heading}\n\n{document_text(doc)}".strip()
    # Markdown 按小节层级还原成真正的标题行，否则前端只会把小节名渲染成普通段落
    lines = [f"# {heading}", ""]
    for section in doc.sections:
        leaf = section.path.split(" > ")[-1] if section.path else ""
        if leaf:
            lines += [f"## {leaf}", ""]
        lines += [section.text, ""]
    return "markdown", "\n".join(lines).strip()


# ---------- Markdown ----------

def _parse_markdown(raw: bytes, filename: str, stem: str) -> ParsedDocument:
    text = _decode(raw)
    meta, body = _split_front_matter(text)

    # 标题优先取元数据，其次取正文第一个一级标题，最后取文件名主干
    title = (meta.get("title") or "").strip()
    category = (meta.get("category") or "").strip()
    species = normalize_species(_split_list(meta.get("species", "")))
    tags = _split_list(meta.get("tags", "")) + _split_list(meta.get("aliases", ""))

    doc = ParsedDocument(doc_id=stem, source=filename, kind="md",
                         title=title or stem, category=category,
                         species=species, tags=[t for t in tags if t])

    lines = body.splitlines()
    heading_stack: List[Tuple[int, str]] = []   # (层级, 标题文本)，用于拼小节路径
    buffer: List[str] = []
    fence = False                                # 是否处于 ``` 代码块内（块内 # 不是标题）

    def flush(path_headings: List[Tuple[int, str]]) -> None:
        content = "\n".join(buffer).strip()
        buffer.clear()
        if content:
            doc.sections.append(Section(_heading_path(path_headings), content))

    for line in lines:
        if line.strip().startswith("```"):
            fence = not fence
            buffer.append(line)
            continue
        matched = None if fence else _MD_HEADING.match(line)
        if matched:
            level = len(matched.group(1))
            heading = matched.group(2).strip().rstrip("#").strip()
            flush(heading_stack)
            heading_stack = [h for h in heading_stack if h[0] < level]
            # 一级标题若与文档标题相同，则不作为小节路径（避免每条片段重复文档名）
            if not (level == 1 and heading and heading == doc.title):
                heading_stack.append((level, heading))
        else:
            buffer.append(line)
    flush(heading_stack)

    # 没有元数据标题时，用第一个一级标题兜底
    if not title and doc.sections:
        first = doc.sections[0].path.split(" > ")[0]
        if first:
            doc.title = first
    return doc


def _split_front_matter(text: str) -> Tuple[dict, str]:
    """拆出头部元数据块与正文；无元数据块时返回空字典"""
    matched = _FRONT_MATTER.match(text)
    if not matched:
        return {}, text
    meta: dict = {}
    for line in matched.group(1).splitlines():
        kv = _META_LINE.match(line.strip())
        if kv:
            meta[kv.group(1).strip().lower()] = kv.group(2).strip()
    return meta, text[matched.end():]


def _split_list(value: str) -> List[str]:
    """把「[a, b]」「a、b」「a,b」这类字符串拆成列表（元素去空白）"""
    if not value:
        return []
    return [v.strip() for v in re.split(r"[,，、;；]+", value.strip().strip("[]()")) if v.strip()]


def _heading_path(stack: List[Tuple[int, str]]) -> str:
    return " > ".join(h for _, h in stack if h)


# ---------- DOCX ----------

def _parse_docx(raw: bytes, filename: str, stem: str) -> ParsedDocument:
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
    # 文档核心属性里通常有标题，优先用它
    core_title = (getattr(document.core_properties, "title", "") or "").strip()
    if core_title:
        doc.title = core_title

    stack: List[Tuple[int, str]] = []
    buffer: List[str] = []

    def flush() -> None:
        content = "\n".join(buffer).strip()
        buffer.clear()
        if content:
            doc.sections.append(Section(_heading_path(stack), content))

    first_paragraph_heading = True
    for para in document.paragraphs:
        text = (para.text or "").strip()
        if not text:
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
            # 无样式层级时，文档首个非标题段落作为标题兜底（多数内部规范文档第一段是标题）
            if first_paragraph_heading and len(text) <= 40:
                doc.title = text
            buffer.append(text)
        first_paragraph_heading = False

    # 表格：docx 表格不在 paragraphs 里，逐表转成「表头: 值」文本，避免内容丢失
    for table in document.tables:
        rows = [[(c.text or "").strip() for c in r.cells] for r in table.rows]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        header = rows[0]
        lines = []
        for row in rows[1:]:
            pairs = [f"{h}：{v}" for h, v in zip(header, row) if v]
            if pairs:
                lines.append("；".join(pairs))
        if lines:
            buffer.append("\n".join(lines))
    flush()

    # 元数据（分类/物种）无法从 docx 结构推断，由导入接口的显式参数指定
    return doc


# ---------- PDF ----------

def _parse_pdf(raw: bytes, filename: str, stem: str) -> ParsedDocument:
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

    with pdf:
        for page_no, page in enumerate(pdf, start=1):
            # sort=True 按阅读顺序还原文本块，避免双栏/错位排版打乱语义
            text = (page.get_text("text", sort=True) or "").strip()
            if text:
                doc.sections.append(Section(f"第 {page_no} 页", text))

    # 首页若整体就是标题行（很短），取它作为文档标题兜底
    if doc.title == stem and doc.sections:
        first_line = doc.sections[0].text.splitlines()[0].strip()
        if 0 < len(first_line) <= 40:
            doc.title = first_line
    return doc


# ---------- 旧版 .doc ----------

def _parse_legacy_doc(raw: bytes, filename: str, stem: str) -> ParsedDocument:
    """Word 97-2003 二进制 .doc：优先用系统 antiword 抽文本

    antiword 对部分中文 .doc 会丢排版，只能拿到线性文本，因此按段落聚合后交给切分，
    小节路径统一记为「正文」；工具缺失时明确报错，让导入方知道要转存 docx。
    """
    tool = shutil.which("antiword") or shutil.which("catdoc")
    if not tool:
        raise DocParseError(
            f"旧版 .doc（{filename}）需要系统安装 antiword 或 catdoc 才能抽取文本，"
            "请用 Word 另存为 .docx 后重新导入")
    with _TempFile(raw, suffix=".doc") as tmp_path:
        cmd = ([tool, "-m", "UTF-8.txt", str(tmp_path)] if "antiword" in tool
               else [tool, str(tmp_path)])
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise DocParseError(f"{filename} 文本抽取失败：{exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise DocParseError(f"{tool} 抽取 {filename} 失败：{detail or result.returncode}")
    text = _decode(result.stdout)
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
    """把字节写到同名临时文件，退出即删（.doc 抽取需要文件路径，不接受 stdin）"""

    def __init__(self, data: bytes, suffix: str = "") -> None:
        self._data = data
        self._suffix = suffix
        self._path: Optional[Path] = None

    def __enter__(self) -> Path:
        from tempfile import NamedTemporaryFile

        tmp = NamedTemporaryFile(suffix=self._suffix, delete=False)
        tmp.write(self._data)
        tmp.close()
        self._path = Path(tmp.name)
        return self._path

    def __exit__(self, *exc) -> None:
        if self._path is not None:
            try:
                self._path.unlink()
            except OSError:
                pass


def _decode(raw: bytes) -> str:
    """文本解码：UTF-8（含 BOM）优先，其次 GB18030（中文 Windows 常见编码）"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _safe_doc_id(stem: str) -> str:
    """文件名主干转成稳定 doc_id：保留中日韩文字与字母数字，其余折叠为下划线"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", stem.strip()).strip("_")
    return cleaned or "doc"
