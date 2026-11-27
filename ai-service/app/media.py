"""对话图片附件处理（类型识别、限流上传、提示词描述）。

对话能力已收敛为「文本 + 图片」：音频与视频不再支持，也就没有转写链路。
本模块只负责把用户上传的图片校验、落 OSS 拿到 URL，并在模型看不了图时生成一句
给模型的文字说明（引导用户补充描述），不做任何服务端二次处理。
"""

import logging
from pathlib import Path
from typing import Optional

from app import oss
from app.config import settings

logger = logging.getLogger(__name__)

KIND_IMAGE = "image"

# MIME 缺失或不规范时（部分客户端会传 application/octet-stream）退化为按后缀判断。
_KIND_BY_EXT = {
    ".jpg": KIND_IMAGE, ".jpeg": KIND_IMAGE, ".png": KIND_IMAGE,
    ".webp": KIND_IMAGE, ".gif": KIND_IMAGE, ".bmp": KIND_IMAGE,
}

# 允许上传的图片 MIME 白名单：不用「image/*」前缀判断，
# 否则 image/svg+xml 这类可携带脚本的格式也会被放进来。
_IMAGE_MIMES = {
    "image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp",
}


class UploadError(Exception):
    """附件不符合要求（类型不支持、大小超限）。

    与 ``oss.OssError`` 区分：本类属于「用户输入问题」，路由层应返回 400 并把原因原样告知；
    OSS 错误属于「服务端问题」，返回 500 + 通用提示。
    """


def detect_kind(mime: str, filename: str) -> Optional[str]:
    """识别附件类型。

    Args:
        mime: 客户端上报的 content_type（可能带 ``;charset=`` 等参数，或为空）。
        filename: 原始文件名，用于兜底按后缀判断。

    Returns:
        ``image``；不是受支持的图片格式时返回 None。
    """
    major = (mime or "").split(";")[0].strip().lower()
    if major in _IMAGE_MIMES:
        return KIND_IMAGE
    ext = Path(filename or "").suffix.lower()
    kind = _KIND_BY_EXT.get(ext)
    if kind:
        return kind
    # 明确是图片但不在白名单（如 svg / tiff）：拒绝，避免误放到 OSS 后浏览器渲染异常。
    return None


def size_limit(kind: str) -> int:
    """取该类附件的大小上限（字节）。

    Returns:
        上限字节数；未知类型返回 0（配合调用方判断，等价于「不允许上传」）。
    """
    mb = {KIND_IMAGE: settings.MEDIA_MAX_IMAGE_MB}.get(kind, 0)
    return mb * 1024 * 1024


def kind_label(kind: str) -> str:
    """把附件类型转成中文名，用于错误提示。"""
    return {KIND_IMAGE: "图片"}.get(kind, "附件")


async def save_upload(file_name: str, content_type: str, read_bytes, user_id: int) -> dict:
    """接收上传流、校验大小并落 OSS。

    采用「边读边累加、超限立刻中断」而不是先读全量再判断：先读完再拒会让异常请求
    轻易打满内存。

    Args:
        content_type: 客户端上报的 MIME 类型。
        read_bytes: 异步读取函数（如 ``UploadFile.read``），每次返回一个分片，读完返回空。
        user_id: 上传者用户 ID，用于生成带命名空间的 OSS key。

    Returns:
        附件字典 ``{type, url, key, mime, name, size}``，可直接作为
        ``schemas.AttachmentInfo`` 的字段来源。

    Raises:
        UploadError: 类型不是图片，或超过大小上限。
        oss.OssError: OSS 未配置或上传失败。
    """
    kind = detect_kind(content_type, file_name)
    if kind is None:
        raise UploadError("仅支持上传图片（jpg / png / webp / gif / bmp）")

    limit = size_limit(kind)
    # 后缀截断到 8 字符：防止构造超长后缀拼出异常 key。
    ext = Path(file_name or "").suffix.lower()[:8]
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime not in _IMAGE_MIMES:
        # content_type 缺失或与后缀冲突（例如把 zip 改名成 .png）时以后缀为准，
        # 否则 OSS 上会挂一个错误的 Content-Type，浏览器预览直接失败。
        mime = ""

    buffered = bytearray()
    chunk = await read_bytes()
    while chunk:
        buffered.extend(chunk)
        # 提前在累计阶段判超限，避免把大文件完整读进内存后才拒绝。
        if len(buffered) > limit:
            raise UploadError(f"{kind_label(kind)}超过 {limit // (1024 * 1024)}MB 大小限制")
        chunk = await read_bytes()

    key = oss.chat_key(user_id, ext)
    # MIME 缺失时按后缀补一个：OSS 上的 Content-Type 决定浏览器是预览还是下载。
    url = await oss.upload(key, bytes(buffered), mime or mime_by_ext(ext))
    return {"type": kind, "url": url, "key": key, "mime": mime,
            "name": file_name or key.rsplit("/", 1)[-1], "size": len(buffered)}


def mime_by_ext(ext: str) -> str:
    """按后缀推断图片 MIME 类型。

    Args:
        ext: 小写后缀（含点号）。

    Returns:
        对应 MIME；未知后缀返回 ``application/octet-stream``。
    """
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    }.get(ext, "application/octet-stream")


def attachment_summary(attachments: list) -> str:
    """概括附件构成，形如「用户发送了 2 张图片」。

    用途有二：作为纯附件消息的会话标题 / 历史文案；拼进 Prompt 让模型知道用户发了几张图。

    Returns:
        概括文本；无附件时返回空串。
    """
    if not attachments:
        return ""
    count = sum(1 for att in attachments if (att.get("type") or KIND_IMAGE) == KIND_IMAGE)
    if not count:
        return f"用户发送了 {len(attachments)} 个附件"
    return f"用户发送了 {count} 张图片"


def describe_for_prompt(attachments: list, vision_ready: bool) -> str:
    """生成「当前模型看不了这些图」的说明，让模型主动引导用户补充信息。

    只在能力缺失时才输出——有能力看图时图片会以多模态内容块直接给模型，
    这里再描述一遍会造成重复。

    Returns:
        逐行说明文本；无需说明时返回空串。
    """
    if vision_ready:
        return ""
    lines: list = []
    for att in attachments:
        if (att.get("type") or KIND_IMAGE) != KIND_IMAGE:
            continue
        name = att.get("name") or "未命名"
        # 明确告诉模型「你看不到」而不是让它猜：否则它会基于文件名编造图片内容。
        lines.append(f"- 用户上传了图片「{name}」，但当前对话模型暂不支持看图，"
                     "请引导用户用文字描述照片内容（如症状、部位、外观等）。")
    return "\n".join(lines)
