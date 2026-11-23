"""Nacos 服务注册与心跳保活。

启动时把 ai-service 注册进 Nacos（供网关 ``lb://ai-service`` 发现），并在后台线程
持续发送心跳；心跳发现实例丢失时自动重新注册。注册失败**不阻断启动**——服务照常对外
提供能力，只是网关可能发现不到它。

为什么有两套注册实现：``nacos-sdk-python`` 是可选依赖（未装则 import 失败），
而心跳保活又必须可靠，因此 SDK 不可用时直接走 HTTP OpenAPI，功能等价。
"""

import logging
import socket
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
# 自检回连超时（秒）：只为区分「注册地址可达 / 不可达」，不必等太久。
_SELF_CHECK_TIMEOUT_SECONDS = 1.5
# 通配地址不能作为注册地址：消费方拿到 0.0.0.0 无法连接，按「未配置」处理。
_WILDCARD_IPS = frozenset({"0.0.0.0", "::", "[::]"})


def _detect_local_ip() -> str:
    """探测本机对外网卡地址。

    用 UDP ``connect`` 让内核按路由表选出出接口并填好源地址——不会真的发包，
    因此不要求目标可达，也不产生任何流量。

    探测顺序：先试 Nacos 地址（选出的正是到注册中心那条路上的网卡），再试公网地址。
    Nacos 部署在本机时（``NACOS_SERVER_ADDR=localhost``）必须跳过，否则会探回
    ``127.0.0.1``，等于什么都没做——这正是把注册地址写死成回环的那种退化。

    Returns:
        点分十进制 IPv4 地址；没有可用路由时回退 ``127.0.0.1``。
    """
    # NACOS_SERVER_ADDR 允许带 http:// 前缀与端口，这里只取主机名用于探测。
    nacos_host = (settings.NACOS_SERVER_ADDR.split("//")[-1]
                  .split("/")[0].split(":")[0].strip() or "")
    targets = []
    if nacos_host and not _is_loopback_host(nacos_host):
        targets.append((nacos_host, 8848))
    targets.append(("8.8.8.8", 80))

    for target in targets:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(target)
            ip = sock.getsockname()[0]
            # 目标解析到回环时源地址也会是回环，这种结果没有意义，继续试下一个。
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            continue
        finally:
            sock.close()

    # 兜底：主机名解析（多网卡机器上可能不是默认路由网卡，总比回环强）
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    return "127.0.0.1"


def _is_loopback_host(host: str) -> bool:
    """判断主机名是否指向本机回环。

    Args:
        host: 主机名或 IP 字面量（可能为空）。

    Returns:
        True 表示 ``localhost`` / ``127.x`` / ``::1`` 这类回环目标。
    """
    lowered = (host or "").strip().lower()
    if not lowered:
        return True
    if lowered in ("localhost", "::1", "[::1]"):
        return True
    try:
        return socket.gethostbyname(lowered).startswith("127.")
    except OSError:
        return False


def _self_reachable(ip: str, port: int) -> bool:
    """用注册地址回连自己，确认这个地址真的能建立连接。

    Returns:
        True 表示能连上；False 表示不可达（端口未监听 / 只绑了回环 / 网卡被拦）。
    """
    try:
        with socket.create_connection((ip, port), timeout=_SELF_CHECK_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _resolve_advertise_ip(configured_ip: str, port: int, bind_host: str = "") -> str:
    """确定最终写入注册中心的 IP。

    做法是「候选列表 + 回连自检取第一个可达的」，而不是单点判断——因为配置、监听地址、
    探测结果三者都可能不一致（典型场景：配置文件写 `AI_SERVICE_HOST=127.0.0.1`，
    实际用 `uvicorn --host 0.0.0.0` 启动）。只有真能连上的地址才值得注册出去。

    候选顺序：
        1. ``AI_SERVICE_IP`` 显式配置（若它本身是回环、而监听地址不是，会被忽略并告警）；
        2. 自动探测（沿到 Nacos 的路由选网卡）；
        3. ``AI_SERVICE_HOST`` 具体监听地址（只听某张网卡时，探测可能选到另一张）；
        4. 回环地址兜底。

    Args:
        configured_ip: ``AI_SERVICE_IP`` 配置值，空串或通配地址表示跳过。
        bind_host: ``AI_SERVICE_HOST``，即 uvicorn 实际绑定的地址。

    Returns:
        最终注册用的 IPv4 地址；全部不可达时退回 ``127.0.0.1`` 并告警。
    """
    configured = (configured_ip or "").strip()
    bind = (bind_host or "").strip()
    bind_is_loopback = _is_loopback_host(bind)

    # 显式配了回环、但监听的是对外地址：两个意图自相矛盾。
    # 回环地址的回连自检必然通过，放任它参与竞争就会「永远注册 127.0.0.1 且不报任何错」，
    # 所以这里直接忽略这个配置项——多机部署最容易被这条坑到。
    if configured and _is_loopback_host(configured) and not bind_is_loopback:
        logger.warning(
            "AI_SERVICE_IP=%s 是回环地址，与监听地址 %s（对外提供服务）矛盾，已忽略该配置；"
            "多机部署请把它留空，交给程序自动挑一个内网地址", configured, bind)
        configured = ""

    candidates = []
    if configured and configured not in _WILDCARD_IPS:
        candidates.append((configured, "配置 AI_SERVICE_IP"))
    candidates.append((_detect_local_ip(), "自动探测"))
    if bind and bind not in _WILDCARD_IPS:
        candidates.append((bind, "监听地址 AI_SERVICE_HOST"))
    candidates.append(("127.0.0.1", "回环兜底"))

    tried = set()
    for ip, source in candidates:
        if ip in tried:
            continue
        tried.add(ip)
        if _self_reachable(ip, port):
            logger.info("Nacos 注册地址确定为 %s:%s（来源：%s）", ip, port, source)
            if _is_loopback_host(ip) and not bind_is_loopback:
                logger.warning(
                    "注册地址是回环地址，只有同机消费方能访问：请确认 AI_SERVICE_HOST=0.0.0.0、"
                    "AI_SERVICE_IP 留空，且 AI 服务注册时本机已监听该端口")
            return ip

    logger.warning(
        "候选注册地址均回连失败（%s），本实例退回 127.0.0.1，仅同机消费方可访问。"
        "若要多机 / 多实例部署，请确认启动参数为 --host 0.0.0.0 且该网卡未被防火墙拦截",
        "、".join(sorted(tried)))
    return "127.0.0.1"


class NacosRegistrar:
    """Nacos 服务实例注册器。

    实例以 ``ephemeral=true``（临时实例）注册，健康状态由心跳维持——心跳停了实例会被
    自动摘除，这也是进程被 kill 后网关不再路由到它的原因。
    """

    def __init__(self, server_addr: str, service_name: str, ip: str, port: int,
                 group: str = "DEFAULT_GROUP", namespace: str = ""):
        self._server_addr = server_addr
        self._service_name = service_name
        self._ip = ip
        self._port = port
        self._group = group
        self._namespace = namespace
        self._sdk_client = None
        self._beat_thread: Optional[threading.Thread] = None
        self._running = False
        self._last_reregister_ts = 0.0


    def register(self) -> None:
        """注册实例并启动心跳线程。

        注册地址在**这里**才确定，而不是构造时：进程是先绑定端口、再触发 lifespan startup，
        只有到这一步回连自检才能反映真实的监听状态（构造时端口还没起来，自检必然失败）。

        注册失败只记日志，不发异常：服务可用性优先于服务发现的完整性。
        """
        self._ip = _resolve_advertise_ip(self._ip, self._port, settings.AI_SERVICE_HOST)
        registered = self._register_via_sdk() or self._register_via_openapi()
        if not registered:
            logger.warning("Nacos 注册失败（SDK 与 OpenAPI 均未成功），ai-service 照常启动，稍后由心跳线程继续尝试保活")
        # 即使首次注册失败也要起心跳：心跳会触发重注册，实现「Nacos 后启动也能自动补注册」。
        self._start_beat_thread()

    def deregister(self) -> None:
        """注销实例并停止心跳线程。

        先停心跳再注销，避免刚注销就被心跳线程重新注册回去。
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
        """
        try:
            import nacos
        except ImportError:
            logger.warning("nacos-sdk-python 未安装，跳过 SDK 注册，直接走 OpenAPI")
            return False
        try:
            client = nacos.NacosClient(self._server_addr, namespace=self._namespace or None)
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
        if self._namespace:
            params["namespaceId"] = self._namespace
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
        """
        url = f"http://{self._server_addr}/nacos/v1/ns/instance"
        params = {
            "serviceName": self._service_name,
            "ip": self._ip,
            "port": self._port,
            "groupName": self._group,
            "ephemeral": "true",
        }
        if self._namespace:
            params["namespaceId"] = self._namespace
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
        """
        if self._beat_thread is not None and self._beat_thread.is_alive():
            return
        self._running = True
        self._beat_thread = threading.Thread(
            target=self._beat_loop, name="nacos-beat", daemon=True)
        self._beat_thread.start()

    def _stop_beat_thread(self) -> None:
        """通知心跳线程退出并等待其结束（最多 2 秒）。
        """
        self._running = False
        if self._beat_thread is not None and self._beat_thread.is_alive():
            self._beat_thread.join(timeout=2)
        self._beat_thread = None

    def _reregister_if_needed(self, reason: str) -> None:
        """在节流窗口内重新注册实例。

        Args:
            reason: 触发原因（写入日志便于排查是「心跳失败」还是「实例被摘除」）。
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
        """
        url = f"http://{self._server_addr}/nacos/v1/ns/instance/beat"
        params = {
            "serviceName": self._service_name,
            "ip": self._ip,
            "port": self._port,
            "groupName": self._group,
        }
        if self._namespace:
            params["namespaceId"] = self._namespace
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
    group=settings.NACOS_GROUP,
    namespace=settings.NACOS_NAMESPACE,
)


def register() -> None:
    """注册实例（吞掉所有异常，保证启动流程不被注册中心问题阻断）。
    """
    try:
        registrar.register()
    except Exception:
        logger.warning("Nacos 注册过程出现未预期异常（忽略，服务照常启动）", exc_info=True)


def deregister() -> None:
    """注销实例（吞掉所有异常，保证停机流程不被注册中心问题阻断）。
    """
    try:
        registrar.deregister()
    except Exception:
        logger.warning("Nacos 反注册过程出现未预期异常（忽略）", exc_info=True)
