"""对象存储（阿里云 OSS）适配层。

职责：把对话附件（图片 / 音频 / 视频）上传到 OSS 并回传公网地址，同时提供「按 key 读回字节」
给服务端二次处理（语音转写、视觉理解）。

主要导出：
    - ``OssError``   存储层异常（配置缺失 / 上传失败）
    - ``configured`` 四项配置是否齐备
    - ``chat_key``   生成带用户命名空间的对象 key
    - ``is_own_key`` 校验 key 是否属于该用户（越权读取的第一道闸）
    - ``upload``     上传字节并返回公网 URL
    - ``get_bytes``  按 key 读回字节（失败降级为 None）

外部依赖：``oss2``（阿里云 SDK，纯同步库，故统一经线程池调用）、``app.config.settings``。

使用示例：
    from app import oss

    key = oss.chat_key(user_id=42, ext=".png")
    url = await oss.upload(key, data, "image/png")
    raw = await oss.get_bytes(key)
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

# 所有 AI 附件统一放在该前缀下，便于按用户目录做权限校验与生命周期清理。
KEY_ROOT = "ai-chat"

# 上传重试次数：OSS 偶发 5xx 很常见，重试一次即可覆盖大部分瞬时抖动，
# 次数再多会拖长用户等待（附件上传是对话的前置阻塞步骤）。
_UPLOAD_ATTEMPTS = 2


class OssError(Exception):
    """对象存储相关错误（配置缺失、上传失败）。

    单独定义而非复用 ``RuntimeError``：路由层要据此返回可读提示（500 + 中文原因），
    与参数类错误（400）区分开。
    """


# oss2.Bucket 内部持有连接池，构造一次复用；_lock 防止并发首次调用重复初始化。
_bucket_client: Optional[oss2.Bucket] = None
_lock = threading.Lock()


def configured() -> bool:
    """检查 OSS 四项必填配置是否齐备。

    Returns:
        True 表示可以调用 OSS；False 表示缺少 endpoint / ak / sk / bucket 之一。

    Raises:
        无。
    """
    return bool(settings.OSS_ENDPOINT.strip() and settings.OSS_ACCESS_KEY_ID.strip()
                and settings.OSS_ACCESS_KEY_SECRET.strip() and settings.OSS_BUCKET_NAME.strip())


def chat_key(user_id: int, ext: str) -> str:
    """生成对话附件的对象 key。

    路径按 ``{根}/{用户}/{日期}/{uuid}{后缀}`` 组织：用户段用于越权校验，日期段便于
    按时间清理历史附件，uuid 保证并发上传不撞名。

    Args:
        user_id: 上传者用户 ID。
        ext: 文件后缀（含点号，如 ``.png``）。

    Returns:
        OSS 对象 key。

    Raises:
        无。
    """
    return f"{KEY_ROOT}/{user_id}/{time.strftime('%Y%m%d')}/{uuid4().hex}{ext}"


def is_own_key(key: str, user_id: int) -> bool:
    """判断对象 key 是否落在该用户的命名空间内。

    Args:
        key: 待校验的对象 key。
        user_id: 当前请求的用户 ID。

    Returns:
        True 表示 key 以该用户的目录前缀开头，可以读取。

    Raises:
        无。
    """
    return bool(key) and key.startswith(f"{KEY_ROOT}/{user_id}/")


def _build_url(key: str) -> str:
    """拼出对象的公网访问地址。

    Args:
        key: 对象 key。

    Returns:
        配置了 ``OSS_DOMAIN`` 时用自定义域名（CDN），否则按 ``{bucket}.{endpoint}/{key}``
        拼默认地址。

    Raises:
        无。
    """
    domain = settings.OSS_DOMAIN.strip()
    if domain:
        return f"{domain.rstrip('/')}/{key}"
    # endpoint 可能带协议前缀，统一剥掉后自行拼 https，避免出现 https://https:// 这类错串。
    endpoint = settings.OSS_ENDPOINT.strip().replace("https://", "").replace("http://", "")
    return f"https://{settings.OSS_BUCKET_NAME}.{endpoint}/{key}"


def _bucket() -> oss2.Bucket:
    """获取（并按需创建）Bucket 客户端。

    Returns:
        可用的 ``oss2.Bucket`` 实例。

    Raises:
        OssError: 配置不完整时抛出，提示补全 ``OSS_*`` 参数。
    """
    global _bucket_client
    if _bucket_client is None:
        # 双重检查加锁：首次并发上传时只构造一个客户端。
        with _lock:
            if _bucket_client is None:
                if not configured():
                    raise OssError("附件存储未配置，请先在 ai-service/.env 中填写 OSS_* 参数")
                auth = oss2.Auth(settings.OSS_ACCESS_KEY_ID, settings.OSS_ACCESS_KEY_SECRET)
                _bucket_client = oss2.Bucket(auth, settings.OSS_ENDPOINT, settings.OSS_BUCKET_NAME)
    return _bucket_client


async def upload(key: str, data: bytes, content_type: str) -> str:
    """上传对象并返回可访问 URL。

    Args:
        key: 目标对象 key（建议由 ``chat_key`` 生成）。
        data: 文件字节。
        content_type: MIME 类型；为空时退化为 ``application/octet-stream``，
            否则浏览器会按错误类型处理。

    Returns:
        对象的公网 URL。

    Raises:
        OssError: 配置缺失或重试后仍上传失败。
    """
    bucket = _bucket()
    last_exc: Optional[Exception] = None
    for attempt in range(_UPLOAD_ATTEMPTS):
        try:
            # oss2 是同步 SDK，放线程池执行以免阻塞事件循环（附件最大 50MB，阻塞代价明显）。
            await asyncio.to_thread(
                bucket.put_object, key, data,
                headers={"Content-Type": content_type or "application/octet-stream"})
            return _build_url(key)
        except Exception as exc:
            last_exc = exc
            logger.warning("上传附件到 OSS 失败（第 %s/%s 次）: key=%s",
                           attempt + 1, _UPLOAD_ATTEMPTS, key, exc_info=True)
            # 递增退避：第 1 次失败等 0.3s，给对端一点恢复时间再试。
            if attempt + 1 < _UPLOAD_ATTEMPTS:
                await asyncio.sleep(0.3 * (attempt + 1))
    raise OssError("附件上传失败，请稍后再试") from last_exc


async def get_bytes(key: str) -> Optional[bytes]:
    """按 key 读回对象字节。

    读取失败不抛错：调用方（语音转写 / 视觉理解）拿到 None 后会降级为文字提示，
    比让整轮对话失败更合理。

    Args:
        key: 对象 key。

    Returns:
        文件字节；失败或配置缺失时返回 None。

    Raises:
        无。
    """
    try:
        bucket = _bucket()
        return await asyncio.to_thread(lambda: bucket.get_object(key).read())
    except Exception:
        logger.warning("读取 OSS 附件失败（降级为文字占位）: key=%s", key, exc_info=True)
        return None
