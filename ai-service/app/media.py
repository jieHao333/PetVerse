"""多模态附件（图片 / 音频 / 视频）：上传落盘、回放与语音转写

设计要点：
  1. 附件直接存本服务（data/uploads，可用 CHAT_UPLOAD_DIR 覆盖），不经 OSS /
     其他微服务——多模态对话是 ai-service 自己的能力，闭环在本服务内最简单；
  2. 文件名用 uuid4 + 原扩展名（不可猜测），回放路径 /ai/chat/media/{name}
     走网关白名单（<img>/<video> 标签无法携带 Authorization 头）；
  3. 类型与大小双重校验：按 MIME 主类型判类，MIME 不可信时按扩展名兜底；
     超限 / 类型不支持直接拒绝（UploadError → 路由层转业务错误）；
  4. 语音转写走 OpenAI 兼容 /audio/transcriptions（如百炼 paraformer-v2），
     未配置 ASR 时调用方降级为文字占位提示，本模块不感知降级逻辑。
  5. 图片发给视觉模型时转 base64 data URL——本服务附件 URL 只在内网可达，
     服务商（百炼等）无法回源拉取，必须把图片内容直接放进请求体。
"""
import base64
import logging
import re
from pathlib import Path
from typing import Optional, Tuple
from uuid import uuid4

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 回放 URL 前缀（与 chat.py 的 GET /ai/chat/media/{name} 路由对应）
MEDIA_URL_PREFIX = "/ai/chat/media"

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

# 文件名安全校验：仅允许 uuid4 + 白名单扩展名（防路径穿越与任意文件读取）
_SAFE_NAME = re.compile(r"^[0-9a-f]{32}(\.[a-z0-9]{2,5})$")


class UploadError(Exception):
    """附件上传业务错误（类型不支持 / 超限 / 读文件失败），message 直接作为业务 msg"""


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


async def save_upload(file_name: str, content_type: str, read_bytes) -> dict:
    """保存一个上传附件，返回附件信息 dict（type/url/mime/name/size）

    :param file_name: 原始文件名（仅用于类型判定与回显）
    :param content_type: 上传 Content-Type
    :param read_bytes: 无参异步函数，返回文件字节（分块读取由调用方决定）
    :raises UploadError: 类型不支持 / 超过大小上限
    """
    kind = detect_kind(content_type, file_name)
    if kind is None:
        raise UploadError("仅支持图片、音频或视频文件")

    limit = size_limit(kind)
    ext = Path(file_name or "").suffix.lower()[:8]
    stored_name = f"{uuid4().hex}{ext}"
    path = settings.upload_dir / stored_name

    size = 0
    with open(path, "wb") as out:
        data = await read_bytes()
        while data:
            size += len(data)
            if size > limit:
                out.close()
                path.unlink(missing_ok=True)
                raise UploadError(f"{kind_label(kind)}超过 {limit // (1024 * 1024)}MB 大小限制")
            out.write(data)
            data = await read_bytes()

    return {
        "type": kind,
        "url": f"{MEDIA_URL_PREFIX}/{stored_name}",
        "mime": (content_type or "").split(";")[0].strip().lower(),
        "name": file_name or stored_name,
        "size": size,
    }


def media_path(name: str) -> Optional[Path]:
    """回放文件名 -> 本地路径；不合法（路径穿越 / 非白名单扩展名）返回 None"""
    if not _SAFE_NAME.match(name or ""):
        return None
    path = settings.upload_dir / name
    return path if path.is_file() else None


def load_attachment(url: str) -> Optional[dict]:
    """按附件 URL（/ai/chat/media/{name}）加载附件元信息（存在时）"""
    name = (url or "").rsplit("/", 1)[-1]
    path = media_path(name)
    if path is None:
        return None
    kind = _KIND_BY_EXT.get(path.suffix.lower()) or detect_kind("", name)
    return {"path": path, "type": kind, "mime": mime_by_ext(path.suffix.lower())}


def mime_by_ext(ext: str) -> str:
    """扩展名 -> MIME（回放响应 Content-Type 与 data URL 用）"""
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
        ".m4v": "video/mp4", ".avi": "video/x-msvideo", ".mkv": "video/x-matroska",
    }.get(ext, "application/octet-stream")


def image_data_url(att: dict) -> Optional[str]:
    """图片附件 -> base64 data URL（发给视觉模型用）

    附件文件缺失（被清理 / 跨实例无共享盘）时返回 None，调用方降级为文字占位。
    """
    info = load_attachment(att.get("url", ""))
    if info is None or info["type"] != KIND_IMAGE:
        return None
    try:
        raw = info["path"].read_bytes()
    except OSError:
        logger.warning("读取图片附件失败: %s", att.get("url"), exc_info=True)
        return None
    mime = att.get("mime") or info["mime"]
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


async def transcribe_audio(att: dict) -> Optional[str]:
    """音频附件 -> 文字（OpenAI 兼容 /audio/transcriptions）

    未配置 ASR / 调用失败 / 返回为空时返回 None，调用方降级为文字占位提示，
    绝不让转写失败阻断对话主流程。转写结果会写回附件 dict 的 transcript 字段。
    """
    if not settings.asr_enabled:
        return None
    info = load_attachment(att.get("url", ""))
    if info is None or info["type"] != KIND_AUDIO:
        return None
    url = settings.ASR_BASE_URL.rstrip("/") + "/audio/transcriptions"
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            with open(info["path"], "rb") as f:
                resp = await client.post(
                    url,
                    files={"file": (att.get("name") or info["path"].name, f,
                                    att.get("mime") or info["mime"])},
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
