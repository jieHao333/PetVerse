"""知识库管理路由（APIRouter prefix="/ai"，路径 /ai/kb/*）

网关 StripPrefix=1：前端请求 /api/ai/kb/documents，本服务收到 /ai/kb/documents。

能力：
  1. GET    /ai/kb/documents                  文档列表（服务端分页 + 关键字过滤 + 全库统计）
  2. GET    /ai/kb/documents/{docId}/preview  在线预览（正文重解析原文，附入库片段清单）
  3. GET    /ai/kb/documents/{docId}/file     回读原文文件（PDF 供浏览器内置查看器渲染）
  4. POST   /ai/kb/documents                  上传文档（md / pdf / docx / doc）→ 解析切分 → 入库
  5. DELETE /ai/kb/documents/{docId}          删除文档（片段级联删除 + 清理留存原文，BM25 索引失效重建）
  6. POST   /ai/kb/reindex                    用仓库语料目录全量重建知识库

权限设计（重要）：知识库是**全用户共享**的检索语料，能改它就能向所有人的回答里注入内容，
因此本路由下的四个接口一律限管理员：

  1. 首选凭证是网关注入的 `X-User-Role`——网关会先剥离客户端自带的同名头，
     值取自 JWT 签发时刻的角色声明，浏览器侧只有 ADMIN 能进；
  2. `KB_ADMIN_TOKEN`（请求头 `X-Kb-Admin`）是给「不经网关直连本服务跑运维脚本」
     留的备选凭证。没有这条通道时，任何能直接访问 8086 的进程伪造一个
     `X-User-Role: ADMIN` 就能改写知识库；两者任一通过即可放行。

上传文档与仓库文档走同一套解析/切分/向量化逻辑（app/kb/ingest.py），检索行为一致；
按 doc_id（文件名主干）覆盖，重复上传同名文件等于替换该文档。
"""
import asyncio
import logging
import urllib.parse
from pathlib import Path as FsPath   # 文件系统路径；fastapi 的 Path 是路径参数声明，两者不能同名
from typing import Optional

from fastapi import APIRouter, File, Form, Header, Path, Query, Response, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.kb import documents, ingest, lexicon, store
from app.kb.documents import DocParseError, SUPPORTED_SUFFIXES, normalize_species
from app.schemas import fail, ok

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai")

# 全量重建互斥：两个人同时重建会让后完成的一方覆盖前一方，且 Embedding 调用翻倍
_reindex_lock = asyncio.Lock()

_ERROR_MSG = "知识库服务暂时不可用，请稍后再试"

# 原文回读时的 MIME：PDF 要给对类型浏览器内置查看器才会渲染
_MIME_BY_SUFFIX = {
    ".md": "text/markdown; charset=utf-8",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
}


def _inside_allowed_dirs(target: FsPath) -> bool:
    """文件路径是否落在语料目录或原文留存目录内（防 file_path 被改成目录外路径）"""
    try:
        resolved = target.resolve()
    except OSError:
        return False
    for root in (settings.knowledge_dir, settings.kb_storage_dir):
        try:
            if resolved.is_relative_to(root.resolve()):
                return True
        except OSError:
            continue
    return False


def _parse_uid(raw: Optional[str]) -> Optional[int]:
    """解析网关注入的 X-User-Id 请求头；缺失或非法时返回 None（调用方返回 401）"""
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
    """知识库管理鉴权：通过返回 None，否则返回对应的 401 / 403 报文

    管理员角色（网关注入的 X-User-Role）与运维令牌（X-Kb-Admin == KB_ADMIN_TOKEN）
    任一命中即放行；带令牌的请求视为运维通道，不要求 X-User-Id。
    """
    token = (settings.KB_ADMIN_TOKEN or "").strip()
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
    """知识库文档列表（管理员限定，服务端分页）

    返回 {items, total, page, size, pages} + 全库规模统计：分页只作用于 items，
    totalDocuments / totalChunks 始终是整库统计，管理页顶部的统计卡不随翻页变化。
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
    """文档在线预览（管理员限定）

    正文优先重解析原文文件（排版完整、无切分重叠），原文缺失时回退为按顺序拼接
    入库片段——回退内容里会看到相邻片段的那句重叠，属预期，界面用 source 字段标注。
    同时返回片段清单，让管理员直接看到「这篇文档被切成了哪些块、每块多少字与词」，
    这才是排查召回问题时真正要看的东西。
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
        if file_path and _inside_allowed_dirs(FsPath(file_path)) and FsPath(file_path).is_file():
            try:
                fmt, content = documents.preview_text(FsPath(file_path), title=doc["name"])
                source = "file"
            except (DocParseError, OSError):
                # 原文坏了（被删、格式不支持、解析异常）不报错，退回片段拼接
                logger.warning("预览重解析原文失败，回退片段拼接: doc_id=%s", doc_id, exc_info=True)
        if not content:
            content = "\n\n".join(c["content"] for c in chunks)
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
    """回源文档原文件（管理员限定）

    PDF 靠浏览器内置查看器渲染，必须能拿到真实字节；前端用带 Authorization 头的
    请求取 blob 再喂给 iframe，因此这里不放开跨域也不接受查询参数传令牌。
    路径必须落在语料目录或原文留存目录内：file_path 虽然由服务端写入，
    但仍要防历史脏数据或人工改库把路径指到 /etc/passwd 这类位置。
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
    if not _inside_allowed_dirs(target) or not target.is_file():
        logger.warning("拒绝越出知识库目录的文件读取: doc_id=%s path=%s", doc_id, file_path)
        return JSONResponse(status_code=200, content=fail(403, "文档文件路径不可用"))
    try:
        data = target.read_bytes()
    except OSError:
        logger.warning("读取文档原文失败: %s", target, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, "文档文件读取失败"))

    disposition = "inline" if inline else "attachment"
    # 中文文件名按 RFC 5987 编码，同时给一份 ASCII 回退名
    quoted = urllib.parse.quote(target.name)
    return Response(
        content=data,
        media_type=_MIME_BY_SUFFIX.get(target.suffix.lower(), "application/octet-stream"),
        headers={
            "Content-Disposition": f"{disposition}; filename=\"document{target.suffix.lower()}\"; "
                                   f"filename*=UTF-8''{quoted}",
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
    """上传一篇知识文档并即时入库（管理权限）

    同名文档（文件名主干相同）按覆盖处理：先删旧片段再写新片段，单事务完成。
    """
    denied = _admin_denied(x_user_id, x_user_role, x_kb_admin)
    if denied is not None:
        return denied

    filename = file.filename or ""
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
        # 解析/向量化失败属于用户可直接修正的问题（文档坏、格式不对、Embedding 未配），
        # 原样回传比「服务不可用」有用，故不做兜底吞掉
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
    """删除一篇文档及其全部知识片段（管理权限）"""
    denied = _admin_denied(x_user_id, x_user_role, x_kb_admin)
    if denied is not None:
        return denied
    try:
        doc = await store.get_document(doc_id)
        deleted = await store.delete_document(doc_id)
    except Exception:
        logger.warning("删除知识库文档失败: doc_id=%s", doc_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if not deleted:
        return JSONResponse(status_code=200, content=fail(404, "文档不存在或已删除"))
    # 片段已随外键级联删除，上传文档的留存原文也要一起清掉，否则目录里会攒孤儿文件
    if doc:
        ingest.remove_original(doc)
    lexicon.invalidate()
    return JSONResponse(status_code=200, content=ok({"docId": doc_id}))


@router.post("/kb/reindex")
async def reindex(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """用仓库语料目录全量重建知识库（管理权限）

    会清空当前知识库后重新导入 settings.knowledge_dir 下的全部文档，
    耗时随文档数量线性增长（每批 10 条片段一次 Embedding 调用）；
    同一时刻只允许一个重建任务在跑，重复请求直接返回「正在重建」。
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
            summary = await ingest.ingest_directory(directory, force=True)
        except Exception:
            logger.warning("知识库全量重建失败", exc_info=True)
            return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    return JSONResponse(status_code=200, content=ok(summary))
