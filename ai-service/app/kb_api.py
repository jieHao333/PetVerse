"""知识库管理接口（管理员限定）。

职责：为后台管理页提供知识库的增删查与重建能力——文档列表、在线预览、原文下载、上传入库、
删除、全量重建。四个写操作与全部读操作都受同一套管理员校验保护。

主要导出：
    - ``router``  FastAPI 路由（前缀 ``/ai``）

外部依赖：``app.kb.*``（解析 / 入库 / 存储 / 词表）、``app.config.settings``、``app.schemas``。

使用示例：
    GET    /ai/kb/documents?page=1&size=20&keyword=疫苗
    GET    /ai/kb/documents/{docId}/preview
    GET    /ai/kb/documents/{docId}/file?inline=true
    POST   /ai/kb/documents         (multipart: file + title/category/species)
    DELETE /ai/kb/documents/{docId}
    POST   /ai/kb/reindex

鉴权说明：管理员身份取网关鉴权后注入的 ``X-User-Role``（网关会先剥离客户端同名头）。
``X-Kb-Admin`` + ``KB_ADMIN_TOKEN`` 是留给「不经网关直连本服务跑运维」的备选通道——
没有这条通道时，任何能直连 8086 端口的进程伪造一个 ``X-User-Role: ADMIN`` 就能改写
所有用户共享的检索语料。
"""

import asyncio
import logging
import urllib.parse
from pathlib import Path as FsPath
from typing import Optional

from fastapi import APIRouter, File, Form, Header, Path, Query, Response, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.kb import documents, ingest, lexicon, store
from app.kb.documents import DocParseError, SUPPORTED_SUFFIXES, normalize_species
from app.schemas import fail, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai")

# 重建是重操作（清库 + 全量向量化），用锁保证同一时刻只有一个在跑；
# 用 locked() 先行判断，让第二个请求立刻拿到「正在重建」而不是排队等到天荒地老。
_reindex_lock = asyncio.Lock()

_ERROR_MSG = "知识库服务暂时不可用，请稍后再试"

# 原文下载时的 MIME 映射：PDF 必须给正确的 content-type，浏览器内置查看器才会渲染而不是下载。
_MIME_BY_SUFFIX = {
    ".md": "text/markdown; charset=utf-8",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}


def _inside_allowed_dirs(target: FsPath) -> bool:
    """判断路径是否落在允许读取的目录内（防路径穿越）。

    ``file_path`` 来自数据库，而数据库里的值可能来自上传 / 迁移 / 手工改库，
    不能假定它一定安全，读取前必须用 realpath 做一次归属校验。

    Args:
        target: 待校验的路径。

    Returns:
        True 表示位于语料目录或上传留存目录内，可以读取。

    Raises:
        无——路径非法（如含非法字符）时返回 False。
    """
    try:
        resolved = target.resolve()
    except OSError:
        return False
    for root in (settings.knowledge_dir, settings.kb_storage_dir):
        try:
            # is_relative_to 基于已解析路径比较，可识别 ``../`` 与符号链接穿越。
            if resolved.is_relative_to(root.resolve()):
                return True
        except OSError:
            continue
    return False


def _parse_uid(raw: Optional[str]) -> Optional[int]:
    """解析请求头里的用户 ID。

    Args:
        raw: 请求头原始值。

    Returns:
        正整数用户 ID；缺失或非法时返回 None。

    Raises:
        无。
    """
    if not raw:
        return None
    try:
        user_id = int(raw)
    except (TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


ROLE_ADMIN = "ADMIN"


def _admin_denied(x_user_id: Optional[str], x_user_role: Optional[str],
                  x_kb_admin: Optional[str]) -> Optional[JSONResponse]:
    """管理员校验。

    两条通道任一通过即可：备选令牌（直连运维）或网关鉴权后的 ADMIN 角色。

    Args:
        x_user_id: 用户 ID 请求头（用于区分「未登录」与「无权限」）。
        x_user_role: 网关注入的角色。
        x_kb_admin: 备选令牌请求头。

    Returns:
        None 表示校验通过；否则返回可直接下发的拒绝报文（401 未登录 / 403 无权限）。

    Raises:
        无。
    """
    token = (settings.KB_ADMIN_TOKEN or "").strip()
    # 令牌必须非空才启用该通道：否则「配置为空 + 请求头为空」会直接放行所有人。
    if token and (x_kb_admin or "").strip() == token:
        return None
    if _parse_uid(x_user_id) is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    if (x_user_role or "").strip().upper() == ROLE_ADMIN:
        return None
    return JSONResponse(status_code=200, content=fail(403, "仅管理员可管理知识库"))


@router.get("/kb/documents")
async def list_documents(
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    size: int = Query(default=20, ge=1, le=100, description="每页条数"),
    keyword: str = Query(default="", description="按文档名 / 文件名 / 分类模糊过滤"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """分页查询知识库文档。

    Args:
        page: 页码（从 1 开始）。
        size: 每页条数（1~100）。
        keyword: 模糊过滤关键词（匹配文档名 / 来源 / 分类 / doc_id）。
        x_user_id / x_user_role / x_kb_admin: 鉴权请求头。

    Returns:
        ``ok({items, total, page, size, pages, totalDocuments, totalChunks, totalChars,
        supportedKinds})``；非管理员返回 403。

    Raises:
        无。
    """
    denied = _admin_denied(x_user_id, x_user_role, x_kb_admin)
    if denied is not None:
        return denied
    try:
        items, total = await store.list_documents(page=page, size=size, keyword=keyword)
        stats = await store.stats()
    except Exception:
        logger.warning("查询知识库文档列表失败", exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    # 向上取整算总页数，供前端分页器直接使用。
    pages = (total + size - 1) // size if size else 1
    return JSONResponse(status_code=200, content=ok({
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "pages": pages,
        "totalDocuments": stats["documents"],
        "totalChunks": stats["chunks"],
        "totalChars": stats["chars"],
        "supportedKinds": sorted(SUPPORTED_SUFFIXES),
    }))


@router.get("/kb/documents/{doc_id}/preview")
async def preview_document(
    doc_id: str = Path(..., description="文档 ID（文件名主干）"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """文档在线预览。

    优先重解析原文（排版完整、无切分重叠）；原文缺失或解析失败时回退为「按 chunk_index
    拼接入库片段」——后者能看到片段边界与上下文前缀，排查召回问题时反而更有用。

    Args:
        doc_id: 文档 ID。
        x_user_id / x_user_role / x_kb_admin: 鉴权请求头。

    Returns:
        ``ok({docId, name, source, kind, format, content, contentFrom, hasFile, chunks,
        chunkCount})``；``contentFrom`` 标明正文来源（``file`` / ``chunks``）。

    Raises:
        无。
    """
    denied = _admin_denied(x_user_id, x_user_role, x_kb_admin)
    if denied is not None:
        return denied
    try:
        doc = await store.get_document(doc_id)
        if doc is None:
            return JSONResponse(status_code=200, content=fail(404, "文档不存在或已删除"))
        chunks = await store.document_chunks(doc_id)

        content, fmt, source = "", "", ""
        file_path = (doc.get("filePath") or "").strip()
        # 三重条件缺一不可：有记录、路径在允许目录内、文件确实存在。
        if file_path and _inside_allowed_dirs(FsPath(file_path)) and FsPath(file_path).is_file():
            try:
                fmt, content = documents.preview_text(FsPath(file_path), title=doc["name"])
                source = "file"
            except (DocParseError, OSError):
                logger.warning("预览重解析原文失败，回退片段拼接: doc_id=%s", doc_id, exc_info=True)
        if not content:
            content = "\n\n".join(c["content"] for c in chunks)
            # md 片段按 markdown 渲染（前端会先经 DOMPurify 消毒），其它格式一律按纯文本。
            fmt = "markdown" if doc["kind"] == "md" else "text"
            source = "chunks"

        return JSONResponse(status_code=200, content=ok({
            "docId": doc["docId"], "name": doc["name"], "source": doc["source"],
            "kind": doc["kind"], "format": fmt, "content": content,
            "contentFrom": source, "hasFile": bool(file_path),
            "chunks": chunks, "chunkCount": len(chunks),
        }))
    except Exception:
        logger.warning("生成文档预览失败: doc_id=%s", doc_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))


@router.get("/kb/documents/{doc_id}/file")
async def download_document_file(
    doc_id: str = Path(..., description="文档 ID（文件名主干）"),
    inline: bool = Query(default=True, description="true 浏览器内打开（PDF 预览），false 作为附件下载"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """回读文档原文文件。

    Args:
        doc_id: 文档 ID。
        inline: True 走浏览器内联渲染，False 作为附件下载。
        x_user_id / x_user_role / x_kb_admin: 鉴权请求头。

    Returns:
        文件字节流（带 Content-Disposition / Content-Type）；文档无留存原文时返回 404 报文。

    Raises:
        无。
    """
    denied = _admin_denied(x_user_id, x_user_role, x_kb_admin)
    if denied is not None:
        return denied
    doc = await store.get_document(doc_id)
    if doc is None:
        return JSONResponse(status_code=200, content=fail(404, "文档不存在或已删除"))
    file_path = (doc.get("filePath") or "").strip()
    if not file_path:
        return JSONResponse(status_code=200, content=fail(404, "该文档没有留存的原文文件"))
    target = FsPath(file_path)
    # 越界访问在这里拦掉并留下告警日志：属于异常行为，不能只是静默 404。
    if not _inside_allowed_dirs(target) or not target.is_file():
        logger.warning("拒绝越出知识库目录的文件读取: doc_id=%s path=%s", doc_id, file_path)
        return JSONResponse(status_code=200, content=fail(403, "文档文件路径不可用"))
    try:
        data = target.read_bytes()
    except OSError:
        logger.warning("读取文档原文失败: %s", target, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, "文档文件读取失败"))

    disposition = "inline" if inline else "attachment"
    # 中文文件名必须走 RFC 5987 的 filename*（UTF-8 百分号编码），
    # 同时保留 ASCII 兜底名，兼容不认 filename* 的老客户端。
    quoted = urllib.parse.quote(target.name)
    return Response(
        content=data,
        media_type=_MIME_BY_SUFFIX.get(target.suffix.lower(), "application/octet-stream"),
        headers={
            "Content-Disposition": f"{disposition}; filename=\"document{target.suffix.lower()}\"; "
                                   f"filename*=UTF-8''{quoted}",
            # 原文可能被同名覆盖更新，禁止缓存以免看到旧版本。
            "Cache-Control": "no-store",
            "Content-Length": str(len(data)),
        },
    )


@router.post("/kb/documents")
async def upload_document(
    file: UploadFile = File(..., description="知识文档（md / pdf / docx / doc）"),
    title: Optional[str] = Form(default=None, description="文档标题（缺省取文件名或文档内标题）"),
    category: Optional[str] = Form(default=None, description="分类（如 喂养 / 疫苗 / 急症）"),
    species: Optional[str] = Form(default=None, description="适用物种，逗号分隔，如 猫,狗"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """上传知识文档并入库（解析 → 切分 → 向量化 → 覆盖写入）。

    按 ``doc_id``（文件名主干）覆盖：同名文件重复上传视为「更新该文档」，
    旧片段在同一事务内先删后写，不会残留孤儿数据。

    Args:
        file: 上传文件。
        title: 文档标题（可选）。
        category: 分类（可选）。
        species: 适用物种，逗号分隔（可选）。
        x_user_id / x_user_role / x_kb_admin: 鉴权请求头。

    Returns:
        ``ok({docId, source, chunks, skipped, sha256})``；格式 / 大小 / 解析错误返回 400。

    Raises:
        无。
    """
    denied = _admin_denied(x_user_id, x_user_role, x_kb_admin)
    if denied is not None:
        return denied

    filename = file.filename or ""
    # 先按后缀挡掉不支持的格式：后面要落地存盘、调外部解析器，越早拒绝越安全。
    if not filename.lower().endswith(SUPPORTED_SUFFIXES):
        return JSONResponse(status_code=200, content=fail(
            400, f"不支持的文档格式，目前支持：{'、'.join(SUPPORTED_SUFFIXES)}"))

    max_bytes = settings.KB_MAX_UPLOAD_MB * 1024 * 1024
    data = await file.read()
    if not data:
        return JSONResponse(status_code=200, content=fail(400, "文件内容为空"))
    if len(data) > max_bytes:
        return JSONResponse(status_code=200, content=fail(
            400, f"文件超过 {settings.KB_MAX_UPLOAD_MB}MB 上限"))

    try:
        result = await ingest.ingest_content(
            filename, data, origin="upload", title=title, category=category,
            species=normalize_species(species) if species else None)
    except (DocParseError, ingest.IngestError) as exc:
        # 解析 / 向量化失败属于「这份文件有问题」，把具体原因告诉管理员才能修文档后重传。
        return JSONResponse(status_code=200, content=fail(400, str(exc)))
    except Exception:
        logger.warning("知识库文档入库失败: %s", filename, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))

    logger.info("知识库文档已入库: %s（doc_id=%s, 片段=%s, 操作者 user_id=%s）",
                filename, result["docId"], result["chunks"], x_user_id)
    return JSONResponse(status_code=200, content=ok(result))


@router.delete("/kb/documents/{doc_id}")
async def delete_document(
    doc_id: str = Path(..., description="文档 ID（文件名主干）"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """删除知识文档。

    ``kb_chunk`` 通过外键 ``ON DELETE CASCADE`` 一并删除（含向量），随后让 BM25 索引失效；
    上传文档的留存原文会清理，**仓库语料源文件不动**——重新全量重建会把它带回来。

    Args:
        doc_id: 文档 ID。
        x_user_id / x_user_role / x_kb_admin: 鉴权请求头。

    Returns:
        ``ok({"docId": doc_id})``；文档不存在返回 404。

    Raises:
        无。
    """
    denied = _admin_denied(x_user_id, x_user_role, x_kb_admin)
    if denied is not None:
        return denied
    try:
        # 先取元信息再删：删完之后就拿不到 filePath，原文清理会失效。
        doc = await store.get_document(doc_id)
        deleted = await store.delete_document(doc_id)
    except Exception:
        logger.warning("删除知识库文档失败: doc_id=%s", doc_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if not deleted:
        return JSONResponse(status_code=200, content=fail(404, "文档不存在或已删除"))
    if doc:
        ingest.remove_original(doc)
    # 关键词索引是进程内缓存，删库后必须显式失效，否则会继续召回已删除片段。
    lexicon.invalidate()
    return JSONResponse(status_code=200, content=ok({"docId": doc_id}))


@router.post("/kb/reindex")
async def reindex(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """用仓库语料目录全量重建知识库。

    语义是「清空后重建」：上传的文档也会被清掉，因此该操作应视为「恢复出厂语料」。

    Args:
        x_user_id / x_user_role / x_kb_admin: 鉴权请求头。

    Returns:
        ``ok(summary)``；已有重建任务在跑时返回 409。

    Raises:
        无。
    """
    denied = _admin_denied(x_user_id, x_user_role, x_kb_admin)
    if denied is not None:
        return denied
    if _reindex_lock.locked():
        return JSONResponse(status_code=200, content=fail(409, "知识库正在重建中，请稍后再试"))

    directory = settings.knowledge_dir
    if not directory.is_dir():
        return JSONResponse(status_code=200, content=fail(400, f"语料目录不存在：{directory}"))

    async with _reindex_lock:
        try:
            await store.clear_all()
            # force=True：清空后必须重新向量化，否则 sha256 未变会被跳过，库反而变空。
            summary = await ingest.ingest_directory(directory, force=True)
        except Exception:
            logger.warning("知识库全量重建失败", exc_info=True)
            return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    return JSONResponse(status_code=200, content=ok(summary))
