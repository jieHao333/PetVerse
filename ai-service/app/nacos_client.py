"""Nacos 服务注册与心跳保活。

职责：启动时把 ai-service 注册进 Nacos（供网关 ``lb://ai-service`` 发现），并在后台线程
持续发送心跳；心跳发现实例丢失时自动重新注册。注册失败**不阻断启动**——服务照常对外
提供能力，只是网关可能发现不到它。

主要导出：
    - ``NacosRegistrar``  注册器（SDK 优先、OpenAPI 兜底）
    - ``registrar``       进程级实例
    - ``register`` / ``deregister``  供应用生命周期钩子调用的无异常版本

外部依赖：``httpx``（OpenAPI 与心跳）、可选 ``nacos-sdk-python``、
``app.config.settings``。

使用示例：
    from app import nacos_client

    await asyncio.to_thread(nacos_client.register)     # 启动时（同步实现，放线程池）
    await asyncio.to_thread(nacos_client.deregister)   # 停机时

为什么有两套注册实现：``nacos-sdk-python`` 是可选依赖（未装则 import 失败），
而心跳保活又必须可靠，因此 SDK 不可用时直接走 HTTP OpenAPI，功能等价。
"""

import logging
import threading
import time
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_BEAT_INTERVAL_SECONDS = 5
_HTTP_TIMEOUT_SECONDS = 5
# 重注册节流：Nacos 抖动时心跳会连续失败，若每次都重注册会打成自我 DDoS。
_REREGISTER_MIN_INTERVAL_SECONDS = 30
# 连续失败多少次才判定实例已丢失（偶发一两次网络抖动不应触发重注册）。
_BEAT_FAIL_THRESHOLD = 3


class NacosRegistrar:
    """Nacos 服务实例注册器。

    实例以 ``ephemeral=true``（临时实例）注册，健康状态由心跳维持——心跳停了实例会被
    自动摘除，这也是进程被 kill 后网关不再路由到它的原因。
    """

    def __init__(self, server_addr: str, service_name: str, ip: str, port: int,
                 group: str = "DEFAULT_GROUP"):
        self._server_addr = server_addr
        self._service_name = service_name
        self._ip = ip
        self._port = port
        self._group = group
        self._sdk_client = None
        self._beat_thread: Optional[threading.Thread] = None
        self._running = False
        self._last_reregister_ts = 0.0


    def register(self) -> None:
        """注册实例并启动心跳线程。

        注册失败只记日志，不发异常：服务可用性优先于服务发现的完整性。

        Returns:
            None。

        Raises:
            无。
        """
        registered = self._register_via_sdk() or self._register_via_openapi()
        if not registered:
            logger.warning("Nacos 注册失败（SDK 与 OpenAPI 均未成功），ai-service 照常启动，稍后由心跳线程继续尝试保活")
        # 即使首次注册失败也要起心跳：心跳会触发重注册，实现「Nacos 后启动也能自动补注册」。
        self._start_beat_thread()

    def deregister(self) -> None:
        """注销实例并停止心跳线程。

        先停心跳再注销，避免刚注销就被心跳线程重新注册回去。

        Returns:
            None。

        Raises:
            无。
        """
        self._stop_beat_thread()
        done = False
        if self._sdk_client is not None:
            try:
                self._sdk_client.remove_naming_instance(
                    self._service_name, self._ip, self._port, group_name=self._group)
                logger.info("Nacos SDK 反注册成功: %s @ %s:%s", self._service_name, self._ip, self._port)
                done = True
            except Exception:
                logger.warning("Nacos SDK 反注册失败，降级 OpenAPI 反注册", exc_info=True)
        if not done:
            self._deregister_via_openapi()


    def _register_via_sdk(self) -> bool:
        """优先用官方 SDK 注册（自带心跳与重连）。

        Returns:
            True 表示注册成功；SDK 未安装或调用失败时返回 False，由调用方降级 OpenAPI。

        Raises:
            无。
        """
        try:
            import nacos
        except ImportError:
            logger.warning("nacos-sdk-python 未安装，跳过 SDK 注册，直接走 OpenAPI")
            return False
        try:
            client = nacos.NacosClient(self._server_addr)
            client.add_naming_instance(
                self._service_name, self._ip, self._port, group_name=self._group)
            self._sdk_client = client
            logger.info("Nacos SDK 注册成功: %s @ %s:%s (group=%s)",
                        self._service_name, self._ip, self._port, self._group)
            return True
        except Exception:
            logger.warning("Nacos SDK 注册失败，准备降级 OpenAPI 注册", exc_info=True)
            self._sdk_client = None
            return False

    def _register_via_openapi(self) -> bool:
        """用 HTTP OpenAPI 注册（不依赖 SDK）。

        Returns:
            True 表示注册成功（HTTP 200 且响应体含 ``ok``）。

        Raises:
            无。
        """
        url = f"http://{self._server_addr}/nacos/v1/ns/instance"
        params = {
            "serviceName": self._service_name,
            "ip": self._ip,
            "port": self._port,
            "groupName": self._group,
            # 临时实例：靠心跳存活，进程退出后自动摘除，不必手工清理。
            "ephemeral": "true",
        }
        try:
            resp = httpx.post(url, params=params, timeout=_HTTP_TIMEOUT_SECONDS)
            # Nacos v1 接口成功时返回纯文本 "ok"（大小写与前后缀不稳定，故用小写包含判断）。
            if resp.status_code == 200 and "ok" in resp.text.lower():
                logger.info("Nacos OpenAPI 注册成功: %s @ %s:%s", self._service_name, self._ip, self._port)
                return True
            logger.warning("Nacos OpenAPI 注册响应异常: status=%s body=%s", resp.status_code, resp.text)
            return False
        except Exception:
            logger.warning("Nacos OpenAPI 注册请求失败（网络不通或 Nacos 未启动）", exc_info=True)
            return False

    def _deregister_via_openapi(self) -> None:
        """用 HTTP OpenAPI 注销实例。

        Returns:
            None。

        Raises:
            无。
        """
        url = f"http://{self._server_addr}/nacos/v1/ns/instance"
        params = {
            "serviceName": self._service_name,
            "ip": self._ip,
            "port": self._port,
            "groupName": self._group,
            "ephemeral": "true",
        }
        try:
            resp = httpx.delete(url, params=params, timeout=_HTTP_TIMEOUT_SECONDS)
            if resp.status_code == 200:
                logger.info("Nacos OpenAPI 反注册成功: %s @ %s:%s", self._service_name, self._ip, self._port)
            else:
                logger.warning("Nacos OpenAPI 反注册响应异常: status=%s body=%s", resp.status_code, resp.text)
        except Exception:
            logger.warning("Nacos OpenAPI 反注册请求失败", exc_info=True)


    def _start_beat_thread(self) -> None:
        """启动守护心跳线程（已启动则跳过）。

        ``daemon=True``：主进程退出时线程不阻塞关闭流程。

        Returns:
            None。

        Raises:
            无。
        """
        if self._beat_thread is not None and self._beat_thread.is_alive():
            return
        self._running = True
        self._beat_thread = threading.Thread(
            target=self._beat_loop, name="nacos-beat", daemon=True)
        self._beat_thread.start()

    def _stop_beat_thread(self) -> None:
        """通知心跳线程退出并等待其结束（最多 2 秒）。

        Returns:
            None。

        Raises:
            无。
        """
        self._running = False
        if self._beat_thread is not None and self._beat_thread.is_alive():
            self._beat_thread.join(timeout=2)
        self._beat_thread = None

    def _reregister_if_needed(self, reason: str) -> None:
        """在节流窗口内重新注册实例。

        Args:
            reason: 触发原因（写入日志便于排查是「心跳失败」还是「实例被摘除」）。

        Returns:
            None。

        Raises:
            无。
        """
        now = time.monotonic()
        if now - self._last_reregister_ts < _REREGISTER_MIN_INTERVAL_SECONDS:
            return
        self._last_reregister_ts = now
        logger.warning("心跳检测到 Nacos 实例可能丢失（%s），尝试重新注册", reason)
        try:
            registered = self._register_via_sdk() or self._register_via_openapi()
        except Exception:
            logger.warning("Nacos 重新注册过程出现未预期异常（忽略，下次心跳继续尝试）", exc_info=True)
            return
        if registered:
            logger.info("Nacos 重新注册成功: %s @ %s:%s", self._service_name, self._ip, self._port)
        else:
            logger.warning("Nacos 重新注册失败，将在下次心跳继续尝试（受 30 秒节流约束）")

    def _beat_loop(self) -> None:
        """心跳主循环（同步线程）。

        每 5 秒发一次心跳；识别两类异常：显式「实例不存在」即刻重注册，
        其它失败累计到阈值后重注册。

        Returns:
            None（线程结束）。

        Raises:
            无——循环内捕获所有异常，单次失败不会终止线程。
        """
        url = f"http://{self._server_addr}/nacos/v1/ns/instance/beat"
        params = {
            "serviceName": self._service_name,
            "ip": self._ip,
            "port": self._port,
            "groupName": self._group,
        }
        fail_count = 0
        while self._running:
            try:
                resp = httpx.put(url, params=params, timeout=_HTTP_TIMEOUT_SECONDS)
                body = (resp.text or "").lower()
                # Nacos 对已摘除的实例返回 200 + RESOURCE_NOT_FOUND：这是明确信号，
                # 不必等失败计数攒满，立即重注册。
                if resp.status_code == 200 and (
                        "resource_not_found" in body or "not found" in body or "20404" in body):
                    fail_count = 0
                    self._reregister_if_needed("beat 返回实例不存在（RESOURCE_NOT_FOUND / not found / 20404）")
                elif resp.status_code == 200:
                    fail_count = 0
                else:
                    fail_count += 1
                    logger.warning("Nacos 心跳响应异常: status=%s body=%s", resp.status_code, resp.text)
                    if fail_count >= _BEAT_FAIL_THRESHOLD:
                        self._reregister_if_needed(f"心跳连续失败 {fail_count} 次")
                        fail_count = 0
            except Exception:
                fail_count += 1
                logger.warning("Nacos 心跳请求失败（Nacos 不可达时实例会被自动摘除）", exc_info=True)
                if fail_count >= _BEAT_FAIL_THRESHOLD:
                    self._reregister_if_needed(f"心跳连续失败 {fail_count} 次")
                    fail_count = 0
            # 分 10 次小睡而不是一次 sleep(5)：停机时最多 0.5 秒就能退出线程，
            # 避免关闭流程被心跳间隔拖住。
            for _ in range(10):
                if not self._running:
                    return
                time.sleep(_BEAT_INTERVAL_SECONDS / 10)


registrar = NacosRegistrar(
    server_addr=settings.NACOS_SERVER_ADDR,
    service_name=settings.AI_SERVICE_NAME,
    ip=settings.AI_SERVICE_IP,
    port=settings.AI_SERVICE_PORT,
)


def register() -> None:
    """注册实例（吞掉所有异常，保证启动流程不被注册中心问题阻断）。

    Returns:
        None。

    Raises:
        无。
    """
    try:
        registrar.register()
    except Exception:
        logger.warning("Nacos 注册过程出现未预期异常（忽略，服务照常启动）", exc_info=True)


def deregister() -> None:
    """注销实例（吞掉所有异常，保证停机流程不被注册中心问题阻断）。

    Returns:
        None。

    Raises:
        无。
    """
    try:
        registrar.deregister()
    except Exception:
        logger.warning("Nacos 反注册过程出现未预期异常（忽略）", exc_info=True)
