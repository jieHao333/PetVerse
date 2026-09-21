"""阿里云 OSS 对象存储（多模态附件持久化）

与 Java 侧 petverse-common 的 OssService 保持同一套配置语义
（endpoint / accessKeyId / accessKeySecret / bucketName / domain，共用同一个桶），
聊天附件以 ai-chat/{userId}/{yyyyMMdd}/{uuid}.ext 上传后返回公网访问 URL：
  - 视觉模型服务商可直接回源拉取该 URL，请求体不携带图片内容；
  - userId 进对象前缀：服务端读取（音频转写）只接受本人命名空间的对象 key，
    即便 key 泄漏也无法跨用户取文件；
  - oss2 为同步 SDK，调用统一经 asyncio.to_thread 代理（与 checkpoint / memstore
    的 Windows ProactorEventLoop 约束处理一致）；
  - 配置缺失时抛 OssError 并由路由层报错（不静默降级，避免附件悄悄丢失）。
"""
import asyncio
import logging
import threading
import time
from typing import Optional
from uuid import uuid4

import oss2

from app.config import settings

logger = logging.getLogger(__name__)

# 聊天附件对象前缀（ai-chat/{userId}/{yyyyMMdd}/{uuid}.{ext}）
KEY_ROOT = "ai-chat"

# 上传失败重试次数（首次之外再试 1 次，网络抖动自愈）
_UPLOAD_ATTEMPTS = 2


class OssError(Exception):
    """OSS 操作失败（存储未配置 / 上传失败），message 可直接作为业务提示"""


_bucket_client: Optional[oss2.Bucket] = None
_lock = threading.Lock()


def configured() -> bool:
    """OSS 配置是否齐全（四项必填）"""
    return bool(settings.OSS_ENDPOINT.strip() and settings.OSS_ACCESS_KEY_ID.strip()
                and settings.OSS_ACCESS_KEY_SECRET.strip() and settings.OSS_BUCKET_NAME.strip())


def chat_key(user_id: int, ext: str) -> str:
    """聊天附件对象 key（含用户命名空间与日期分区，便于按用户排查与生命周期管理）"""
    return f"{KEY_ROOT}/{user_id}/{time.strftime('%Y%m%d')}/{uuid4().hex}{ext}"


def is_own_key(key: str, user_id: int) -> bool:
    """对象 key 是否属于该用户的命名空间（服务端读取前的准入校验）"""
    return bool(key) and key.startswith(f"{KEY_ROOT}/{user_id}/")


def _build_url(key: str) -> str:
    """拼接公网访问 URL：优先自定义域名，否则按 桶名.地域节点 默认域名（与 Java OssService 一致）"""
    domain = settings.OSS_DOMAIN.strip()
    if domain:
        return f"{domain.rstrip('/')}/{key}"
    endpoint = settings.OSS_ENDPOINT.strip().replace("https://", "").replace("http://", "")
    return f"https://{settings.OSS_BUCKET_NAME}.{endpoint}/{key}"


def _bucket() -> oss2.Bucket:
    """惰性创建 oss2 Bucket（同步对象、无网络请求）"""
    global _bucket_client
    if _bucket_client is None:
        with _lock:
            if _bucket_client is None:
                if not configured():
                    raise OssError("附件存储未配置，请先在 ai-service/.env 中填写 OSS_* 参数")
                auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
                _bucket_client = oss2.Bucket(auth, settings.OSS_ENDPOINT, settings.OSS_BUCKET_NAME)
    return _bucket_client


async def upload(key: str, data: bytes, content_type: str) -> str:
    """上传对象并返回公网 URL；抖动重试后仍失败抛 OssError（由路由层转业务错误）"""
    bucket = _bucket()
    last_exc: Optional[Exception] = None
    for attempt in range(_UPLOAD_ATTEMPTS):
        try:
            await asyncio.to_thread(
                bucket.put_object, key, data,
                headers={"Content-Type": content_type or "application/octet-stream"})
            return _build_url(key)
        except Exception as exc:
            last_exc = exc
            logger.warning("上传附件到 OSS 失败（第 %s/%s 次）: key=%s",
                           attempt + 1, _UPLOAD_ATTEMPTS, key, exc_info=True)
            if attempt + 1 < _UPLOAD_ATTEMPTS:
                await asyncio.sleep(0.3 * (attempt + 1))
    raise OssError("附件上传失败，请稍后再试") from last_exc


async def get_bytes(key: str) -> Optional[bytes]:
    """下载对象字节（音频转写用）；失败返回 None，调用方降级为文字占位"""
    try:
        bucket = _bucket()
        return await asyncio.to_thread(lambda: bucket.get_object(key).read())
    except Exception:
        logger.warning("读取 OSS 附件失败（降级为文字占位）: key=%s", key, exc_info=True)
        return None
