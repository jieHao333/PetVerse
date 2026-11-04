"""多模态附件处理（类型识别、限流上传、语音转写、提示词描述）。

职责：把用户上传的图片 / 音频 / 视频转成「对话链路能用的东西」——落 OSS 拿到 URL、
音频转成文字、并把当前模型看不了的内容转成给模型的文字说明（引导用户补充描述）。

主要导出：
    - 常量：``KIND_IMAGE`` / ``KIND_AUDIO`` / ``KIND_VIDEO``
    - ``UploadError``   附件校验失败（类型不支持、超限）
    - ``save_upload``   接收上传流并落 OSS
    - ``transcribe_audio``  音频转写（ASR，失败返回 None）
    - ``attachment_summary``  一句话概括附件构成（用于会话标题 / 历史展示）
    - ``describe_for_prompt`` 生成「模型看不了该附件」的提示语

外部依赖：``httpx``（调用 ASR 接口）、``app.oss``、``app.config.settings``。

使用示例：
    from app import media

    att = await media.save_upload(file.filename, file.content_type, file.read, user_id)
    text = await media.transcribe_audio(att, user_id)
"""

import logging
from pathlib import Path
from typing import Optional

import httpx

from app import oss
from app.config import settings

logger = logging.getLogger(__name__)

KIND_IMAGE = "image"
KIND_AUDIO = "audio"
KIND_VIDEO = "video"

# 优先按 MIME 主类型判断，浏览器给的 content_type 最可靠。
_KIND_BY_MAJOR = {
    "image": KIND_IMAGE,
    "audio": KIND_AUDIO,
    "video": KIND_VIDEO,
}

# MIME 缺失或不规范时（部分客户端会传 application/octet-stream）退化为按后缀判断。
_KIND_BY_EXT = {
    ".jpg": KIND_IMAGE, ".jpeg": KIND_IMAGE, ".png": KIND_IMAGE, ".webp": KIND_IMAGE, ".gif": KIND_IMAGE, ".bmp": KIND_IMAGE,
    ".mp3": KIND_AUDIO, ".wav": KIND_AUDIO, ".m4a": KIND_AUDIO, ".aac": KIND_AUDIO, ".ogg": KIND_AUDIO, ".flac": KIND_AUDIO,
    ".mp4": KIND_VIDEO, ".webm": KIND_VIDEO, ".mov": KIND_VIDEO, ".m4v": KIND_VIDEO, ".avi": KIND_VIDEO, ".mkv": KIND_VIDEO,
}


class UploadError(Exception):
    """附件不符合要求（类型不支持、大小超限）。

    与 ``oss.OssError`` 区分：本类属于「用户输入问题」，路由层应返回 400 并把原因原样告知；
    OSS 错误属于「服务端问题」，返回 500 + 通用提示。
    """

def detect_kind(mime: str, filename: str) -> Optional[str]:
    """识别附件大类。

    Args:
        mime: 客户端上报的 content_type（可能带 ``;charset=`` 等参数，或为空）。
        filename: 原始文件名，用于兜底按后缀判断。

    Returns:
        ``image`` / ``audio`` / ``video``；无法识别时返回 None。

    Raises:
        无。
    """
    major = (mime or "").split(";")[0].strip().lower()
    kind = _KIND_BY_MAJOR.get(major.split("/")[0]) if "/" in major else None
    if kind:
        return kind
    ext = Path(filename or "").suffix.lower()
    return _KIND_BY_EXT.get(ext)


def size_limit(kind: str) -> int:
    """取该类附件的大小上限（字节）。

    Args:
        kind: 附件大类。

    Returns:
        上限字节数；未知类型返回 0（配合调用方判断，等价于「不允许上传」）。

    Raises:
        无。
    """
    mb = {
        KIND_IMAGE: settings.MEDIA_MAX_IMAGE_MB,
        KIND_AUDIO: settings.MEDIA_MAX_AUDIO_MB,
        KIND_VIDEO: settings.MEDIA_MAX_VIDEO_MB,
    }.get(kind, 0)
    return mb * 1024 * 1024


def kind_label(kind: str) -> str:
    """把附件大类转成中文名，用于错误提示。

    Args:
        kind: 附件大类。

    Returns:
        「图片」/「音频」/「视频」，未知类型返回「附件」。

    Raises:
        无。
    """
    return {KIND_IMAGE: "图片", KIND_AUDIO: "音频", KIND_VIDEO: "视频"}.get(kind, "附件")


async def save_upload(file_name: str, content_type: str, read_bytes, user_id: int) -> dict:
    """接收上传流、校验大小并落 OSS。

    采用「边读边累加、超限立刻中断」而不是先读全量再判断：视频上限 50MB，
    先读完再拒会让恶意请求轻易打满内存。

    Args:
        file_name: 原始文件名。
        content_type: 客户端上报的 MIME 类型。
        read_bytes: 异步读取函数（如 ``UploadFile.read``），每次返回一个分片，读完返回空。
        user_id: 上传者用户 ID，用于生成带命名空间的 OSS key。

    Returns:
        附件字典 ``{type, url, key, mime, name, size}``，可直接作为
        ``schemas.AttachmentInfo`` 的字段来源。

    Raises:
        UploadError: 类型不支持或超过大小上限。
        oss.OssError: OSS 未配置或上传失败。
    """
    kind = detect_kind(content_type, file_name)
    if kind is None:
        raise UploadError("仅支持图片、音频或视频文件")

    limit = size_limit(kind)
    # 后缀截断到 8 字符：防止构造超长后缀拼出异常 key。
    ext = Path(file_name or "").suffix.lower()[:8]
    mime = (content_type or "").split(";")[0].strip().lower()

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
    """按后缀推断 MIME 类型。

    Args:
        ext: 小写后缀（含点号）。

    Returns:
        对应 MIME；未知后缀返回 ``application/octet-stream``。

    Raises:
        无。
    """
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
        ".m4v": "video/mp4", ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
    }.get(ext, "application/octet-stream")


async def read_attachment_bytes(att: dict, user_id: int) -> Optional[bytes]:
    """读取附件原始字节（服务端二次处理用）。

    Args:
        att: 附件字典，需含 ``key``。
        user_id: 当前用户 ID，用于越权校验。

    Returns:
        文件字节；无 key、非本人命名空间或读取失败时返回 None。

    Raises:
        无。
    """
    key = (att.get("key") or "").strip()
    if not key:
        return None
    # 越权防线：key 由客户端回传，必须校验它确实以当前用户的目录前缀开头，
    # 否则改一个 key 就能读到别人的附件。
    if not oss.is_own_key(key, user_id):
        logger.warning("拒绝读取非本人命名空间的附件: user_id=%s key=%s", user_id, key)
        return None
    return await oss.get_bytes(key)


async def transcribe_audio(att: dict, user_id: int) -> Optional[str]:
    """调用 ASR 接口把音频转成文字。

    Args:
        att: 附件字典（需含 ``key`` / ``mime`` / ``name``）。
        user_id: 当前用户 ID。

    Returns:
        转写文本；ASR 未配置、读取失败、调用失败或结果为空时均返回 None
        （调用方据此退回「请用文字补充说明」的引导语）。

    Raises:
        无。
    """
    if not settings.asr_enabled or att.get("type") != KIND_AUDIO:
        return None
    raw = await read_attachment_bytes(att, user_id)
    if raw is None:
        return None
    url = settings.ASR_BASE_URL.rstrip("/") + "/audio/transcriptions"
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                url,
                files={"file": (att.get("name") or "audio", raw,
                                att.get("mime") or "application/octet-stream")},
                data={"model": settings.ASR_MODEL},
                headers={"Authorization": f"Bearer {settings.ASR_API_KEY}"},
            )
        resp.raise_for_status()
        text = (resp.json() or {}).get("text", "")
    except Exception:
        logger.warning("音频转写失败（降级为文字占位提示）: %s", att.get("url"), exc_info=True)
        return None
    text = (text or "").strip()
    return text or None


def attachment_summary(attachments: list) -> str:
    """概括附件构成，形如「用户发送了 2 张图片、1 段音频」。

    用途有二：作为纯附件消息的会话标题 / 历史文案；拼进 Prompt 让模型知道用户发了几样东西。

    Args:
        attachments: 附件字典列表。

    Returns:
        概括文本；无附件时返回空串。

    Raises:
        无。
    """
    if not attachments:
        return ""
    counts = {}
    for att in attachments:
        kind = att.get("type") or "附件"
        counts[kind] = counts.get(kind, 0) + 1
    parts = []
    # 按固定顺序拼接，避免同一批附件因字典顺序不同产生不同文案（会污染缓存与历史）。
    for kind in (KIND_IMAGE, KIND_AUDIO, KIND_VIDEO):
        if counts.get(kind):
            unit = {"image": "张图片", "audio": "段音频", "video": "个视频"}[kind]
            parts.append(f"{counts[kind]} {unit}")
    if not parts:
        return f"用户发送了 {len(attachments)} 个附件"
    # 转写文本一并带上：音频在纯文字模型里唯一可用的信息就是转写结果。
    transcripts = [att.get("transcript") for att in attachments
                   if att.get("type") == KIND_AUDIO and att.get("transcript")]
    summary = f"用户发送了 {'、'.join(parts)}"
    if transcripts:
        summary += "，音频内容为：" + "；".join(transcripts)
    return summary


def describe_for_prompt(attachments: list, vision_ready: bool, asr_ready: bool) -> str:
    """生成「当前模型看不了这些附件」的说明，让模型主动引导用户补充信息。

    只在能力缺失时才输出——有能力看图时图片会以多模态消息直接给模型，
    这里再描述一遍会造成重复。

    Args:
        attachments: 附件字典列表。
        vision_ready: 视觉能力是否可用。
        asr_ready: 语音转写能力是否可用。

    Returns:
        逐行说明文本；无需说明时返回空串。

    Raises:
        无。
    """
    lines: list = []
    for att in attachments:
        kind = att.get("type")
        name = att.get("name") or "未命名"
        if kind == KIND_IMAGE:
            if not vision_ready:
                # 明确告诉模型「你看不到」而不是让它猜：否则它会基于文件名编造图片内容。
                lines.append(f"- 用户上传了图片「{name}」，但当前对话模型暂不支持看图，"
                             "请引导用户用文字描述照片内容（如症状、部位、外观等）。")
        elif kind == KIND_AUDIO:
            if not asr_ready:
                lines.append(f"- 用户上传了音频「{name}」（当前未配置语音转写，无法获取音频内容），"
                             "请引导用户用文字补充说明。")
        elif kind == KIND_VIDEO:
            # 视频无解（没有帧抽取链路），无条件提示模型改用文字 / 截图。
            lines.append(f"- 用户上传了视频「{name}」，当前对话模型暂不支持直接观看视频，"
                         "请结合用户的文字描述回答，必要时引导用户截取关键画面以图片发送。")
    return "\n".join(lines)
