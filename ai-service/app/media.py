"""多模态附件（图片 / 音频 / 视频）：上传 OSS、元信息组装与语音转写

设计要点：
  1. 附件直传阿里云 OSS（配置与客户端见 app/oss.py），返回公网 URL——
     浏览器回放与视觉模型回源拉取都用该 URL，不经本服务转发；
  2. 对象 key 用 uuid4（不可猜测），形如 ai-chat/{userId}/{yyyyMMdd}/{uuid}.ext；
  3. 类型与大小双重校验：按 MIME 主类型判类，MIME 不可信时按扩展名兜底；
     超限 / 类型不支持直接拒绝（UploadError → 路由层转业务错误）；
  4. 语音转写走 OpenAI 兼容 /audio/transcriptions（如百炼 paraformer-v2），
     未配置 ASR 时调用方降级为文字占位提示，本模块不感知降级逻辑；
     读取附件字节（转写用）只接受本人命名空间的对象 key，防跨用户取文件。
"""
import logging
from pathlib import Path
from typing import Optional

import httpx

from app import oss
from app.config import settings

logger = logging.getLogger(__name__)

# 附件大类（与前端展示、模型内容分片一一对应）
KIND_IMAGE = "image"
KIND_AUDIO = "audio"
KIND_VIDEO = "video"

# MIME 主类型 -> 附件大类
_KIND_BY_MAJOR = {
    "image": KIND_IMAGE,
    "audio": KIND_AUDIO,
    "video": KIND_VIDEO,
}

# 扩展名兜底映射（MIME 缺失 / 伪造时使用）
_KIND_BY_EXT = {
    ".jpg": KIND_IMAGE, ".jpeg": KIND_IMAGE, ".png": KIND_IMAGE, ".webp": KIND_IMAGE, ".gif": KIND_IMAGE, ".bmp": KIND_IMAGE,
    ".mp3": KIND_AUDIO, ".wav": KIND_AUDIO, ".m4a": KIND_AUDIO, ".aac": KIND_AUDIO, ".ogg": KIND_AUDIO, ".flac": KIND_AUDIO,
    ".mp4": KIND_VIDEO, ".webm": KIND_VIDEO, ".mov": KIND_VIDEO, ".m4v": KIND_VIDEO, ".avi": KIND_VIDEO, ".mkv": KIND_VIDEO,
}


class UploadError(Exception):
    """附件上传业务错误（类型不支持 / 超限），message 直接作为业务 msg"""


def detect_kind(mime: str, filename: str) -> Optional[str]:
    """按 MIME 主类型（扩展名兜底）判定附件大类；无法判定返回 None"""
    major = (mime or "").split(";")[0].strip().lower()
    kind = _KIND_BY_MAJOR.get(major.split("/")[0]) if "/" in major else None
    if kind:
        return kind
    ext = Path(filename or "").suffix.lower()
    return _KIND_BY_EXT.get(ext)


def size_limit(kind: str) -> int:
    """单附件大小上限（字节）"""
    mb = {
        KIND_IMAGE: settings.MEDIA_MAX_IMAGE_MB,
        KIND_AUDIO: settings.MEDIA_MAX_AUDIO_MB,
        KIND_VIDEO: settings.MEDIA_MAX_VIDEO_MB,
    }.get(kind, 0)
    return mb * 1024 * 1024


def kind_label(kind: str) -> str:
    return {KIND_IMAGE: "图片", KIND_AUDIO: "音频", KIND_VIDEO: "视频"}.get(kind, "附件")


async def save_upload(file_name: str, content_type: str, read_bytes, user_id: int) -> dict:
    """保存一个上传附件，返回附件信息 dict（type/url/key/mime/name/size）

    附件直传 OSS，url 为公网地址（浏览器回放与视觉模型回源拉取共用）。

    :param file_name: 原始文件名（仅用于类型判定与回显）
    :param content_type: 上传 Content-Type
    :param read_bytes: 无参异步函数，返回文件字节（分块读取由调用方决定）
    :param user_id: 上传用户 ID（进 OSS 对象命名空间，服务端读取时校验归属性）
    :raises UploadError: 类型不支持 / 超过大小上限
    :raises oss.OssError: 存储未配置 / 上传失败
    """
    kind = detect_kind(content_type, file_name)
    if kind is None:
        raise UploadError("仅支持图片、音频或视频文件")

    limit = size_limit(kind)
    ext = Path(file_name or "").suffix.lower()[:8]
    mime = (content_type or "").split(";")[0].strip().lower()

    # 边读边累计大小：超限立即中断，不产生上传流量
    buffered = bytearray()
    chunk = await read_bytes()
    while chunk:
        buffered.extend(chunk)
        if len(buffered) > limit:
            raise UploadError(f"{kind_label(kind)}超过 {limit // (1024 * 1024)}MB 大小限制")
        chunk = await read_bytes()

    key = oss.chat_key(user_id, ext)
    url = await oss.upload(key, bytes(buffered), mime or mime_by_ext(ext))
    return {"type": kind, "url": url, "key": key, "mime": mime,
            "name": file_name or key.rsplit("/", 1)[-1], "size": len(buffered)}


def mime_by_ext(ext: str) -> str:
    """扩展名 -> MIME（上传对象的 Content-Type 与前端预览用）"""
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
        ".m4v": "video/mp4", ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
    }.get(ext, "application/octet-stream")


async def read_attachment_bytes(att: dict, user_id: int) -> Optional[bytes]:
    """读取附件字节（音频转写用）；失败返回 None，调用方降级为文字占位

    客户端可伪造 attachments，故只按对象 key 取文件（不按任何 URL 去抓取，避免
    SSRF），且校验 key 落在本人命名空间内（避免跨用户读取）。
    """
    key = (att.get("key") or "").strip()
    if not key:
        return None
    if not oss.is_own_key(key, user_id):
        logger.warning("拒绝读取非本人命名空间的附件: user_id=%s key=%s", user_id, key)
        return None
    return await oss.get_bytes(key)


async def transcribe_audio(att: dict, user_id: int) -> Optional[str]:
    """音频附件 -> 文字（OpenAI 兼容 /audio/transcriptions）

    未配置 ASR / 调用失败 / 返回为空时返回 None，调用方降级为文字占位提示，
    绝不让转写失败阻断对话主流程。转写结果会写回附件 dict 的 transcript 字段。
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
    """附件列表 -> 中文摘要（供消息文本占位 / 会话标题 / 意图兜底用）

    例：「用户发送了 2 张图片和 1 段音频」；含转写文本的音频会附上转写内容。
    """
    if not attachments:
        return ""
    counts = {}
    for att in attachments:
        kind = att.get("type") or "附件"
        counts[kind] = counts.get(kind, 0) + 1
    parts = []
    for kind in (KIND_IMAGE, KIND_AUDIO, KIND_VIDEO):
        if counts.get(kind):
            unit = {"image": "张图片", "audio": "段音频", "video": "个视频"}[kind]
            parts.append(f"{counts[kind]} {unit}")
    if not parts:  # 未知类型兜底
        return f"用户发送了 {len(attachments)} 个附件"
    transcripts = [att.get("transcript") for att in attachments
                   if att.get("type") == KIND_AUDIO and att.get("transcript")]
    summary = f"用户发送了 {'、'.join(parts)}"
    if transcripts:
        summary += "，音频内容为：" + "；".join(transcripts)
    return summary


def describe_for_prompt(attachments: list, vision_ready: bool, asr_ready: bool) -> str:
    """附件 -> 发给模型的文字描述块（图片走视觉分片时不重复描述）

    - 图片 + 视觉可用：不生成文字描述（图片以 image_url 分片直发模型）
    - 图片 + 视觉不可用：提示模型当前无法看图，引导用户文字描述
    - 音频已转写：转写文本已并入 query，无需占位
    - 音频未转写 / 视频：占位提示模型附件类型与文件名
    """
    lines: list = []
    for att in attachments:
        kind = att.get("type")
        name = att.get("name") or "未命名"
        if kind == KIND_IMAGE:
            if not vision_ready:
                lines.append(f"- 用户上传了图片「{name}」，但当前对话模型暂不支持看图，"
                             "请引导用户用文字描述照片内容（如症状、部位、外观等）。")
        elif kind == KIND_AUDIO:
            if not asr_ready:
                lines.append(f"- 用户上传了音频「{name}」（当前未配置语音转写，无法获取音频内容），"
                             "请引导用户用文字补充说明。")
        elif kind == KIND_VIDEO:
            lines.append(f"- 用户上传了视频「{name}」，当前对话模型暂不支持直接观看视频，"
                         "请结合用户的文字描述回答，必要时引导用户截取关键画面以图片发送。")
    return "\n".join(lines)
