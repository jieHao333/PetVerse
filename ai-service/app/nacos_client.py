"""Nacos 服务注册封装（全项目唯一允许 import nacos SDK 的文件）

注册策略（双保险）：
1. 优先走 nacos-sdk-python 1.0.0 的 NacosClient.add_naming_instance；
2. SDK 不可用 / 注册失败时，降级为裸调 Nacos OpenAPI（POST /nacos/v1/ns/instance）；
3. 无论走哪条路径，都额外启动一个 daemon 心跳线程，每 5 秒调一次
   PUT /nacos/v1/ns/instance/beat 保活（SDK 自身心跳行为不可靠，自建心跳兜底，双保险无害）；
   心跳若发现实例已丢失（Nacos 重启等原因，beat 返回 RESOURCE_NOT_FOUND / 20404 或连续失败），
   会自动重新注册（带 30 秒节流），兑现「由心跳线程继续尝试保活」的承诺。

注意：nacos SDK 是同步库，本模块只能被 main.py 在启动 / 停机阶段
（经由 asyncio.to_thread 放入后台线程）调用，绝不能进入请求处理路径。
"""
import logging
import threading
import time
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 心跳线程间隔（秒）
_BEAT_INTERVAL_SECONDS = 5
# 所有 OpenAPI 请求的超时（秒）
_HTTP_TIMEOUT_SECONDS = 5
# 重注册最小间隔（秒）：实例丢失后若不节流，每 5 秒一次的心跳都会触发注册请求打爆 Nacos
_REREGISTER_MIN_INTERVAL_SECONDS = 30
# 心跳连续失败多少次后判定实例可能丢失（Nacos 不可达 / 返回异常），触发一次重注册
_BEAT_FAIL_THRESHOLD = 3


class NacosRegistrar:
    """Nacos 注册器：SDK 优先注册，OpenAPI 降级，自带心跳保活线程"""

    def __init__(self, server_addr: str, service_name: str, ip: str, port: int,
                 group: str = "DEFAULT_GROUP"):
        self._server_addr = server_addr    # 形如 localhost:8848
        self._service_name = service_name
        self._ip = ip
        self._port = port
        self._group = group
        self._sdk_client = None            # nacos SDK 客户端句柄（SDK 注册成功后保留，供反注册用）
        self._beat_thread: Optional[threading.Thread] = None
        self._running = False              # 心跳线程运行开关
        self._last_reregister_ts = 0.0     # 上次自动重注册的时间戳（time.monotonic，用于节流）

    # ---------- 对外主入口 ----------

    def register(self) -> None:
        """注册服务实例（同步阻塞方法，务必在后台线程或启动阶段调用）"""
        registered = self._register_via_sdk() or self._register_via_openapi()
        if not registered:
            # 注册失败只打日志，绝不阻断服务启动
            logger.warning("Nacos 注册失败（SDK 与 OpenAPI 均未成功），ai-service 照常启动，稍后由心跳线程继续尝试保活")
        # 无论注册走哪条路径，都启动自建心跳线程（SDK 心跳不可靠，自建心跳兜底）
        self._start_beat_thread()

    def deregister(self) -> None:
        """停机反注册：SDK remove 优先，失败或无 SDK 句柄时降级裸 DELETE"""
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

    # ---------- 注册：SDK 与 OpenAPI 两条路径 ----------

    def _register_via_sdk(self) -> bool:
        """尝试用 nacos-sdk-python 注册，成功返回 True"""
        try:
            import nacos  # 延迟导入：SDK 未安装时自动走 OpenAPI 降级路径
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
        """降级路径：裸调 Nacos v1 OpenAPI 注册临时实例"""
        url = f"http://{self._server_addr}/nacos/v1/ns/instance"
        params = {
            "serviceName": self._service_name,
            "ip": self._ip,
            "port": self._port,
            "groupName": self._group,
            "ephemeral": "true",   # 临时实例：靠心跳保活，服务下线自动摘除
        }
        try:
            resp = httpx.post(url, params=params, timeout=_HTTP_TIMEOUT_SECONDS)
            if resp.status_code == 200 and "ok" in resp.text.lower():
                logger.info("Nacos OpenAPI 注册成功: %s @ %s:%s", self._service_name, self._ip, self._port)
                return True
            logger.warning("Nacos OpenAPI 注册响应异常: status=%s body=%s", resp.status_code, resp.text)
            return False
        except Exception:
            logger.warning("Nacos OpenAPI 注册请求失败（网络不通或 Nacos 未启动）", exc_info=True)
            return False

    def _deregister_via_openapi(self) -> None:
        """降级路径：裸调 Nacos v1 OpenAPI 反注册"""
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

    # ---------- 自建心跳线程 ----------

    def _start_beat_thread(self) -> None:
        """启动 daemon 心跳线程（重复调用安全）"""
        if self._beat_thread is not None and self._beat_thread.is_alive():
            return
        self._running = True
        self._beat_thread = threading.Thread(
            target=self._beat_loop, name="nacos-beat", daemon=True)
        self._beat_thread.start()

    def _stop_beat_thread(self) -> None:
        """停止心跳线程（最多等待 2 秒）"""
        self._running = False
        if self._beat_thread is not None and self._beat_thread.is_alive():
            self._beat_thread.join(timeout=2)
        self._beat_thread = None

    def _reregister_if_needed(self, reason: str) -> None:
        """实例疑似丢失后的自动重注册（带节流，两次重注册至少间隔 30 秒）

        复用注册双保险路径（SDK 优先、OpenAPI 降级）；节流是为了避免
        心跳每 5 秒触发一次注册请求打爆 Nacos。线程内串行调用，无需加锁。
        """
        now = time.monotonic()
        if now - self._last_reregister_ts < _REREGISTER_MIN_INTERVAL_SECONDS:
            return  # 节流窗口内：跳过，等下一次心跳再试
        self._last_reregister_ts = now
        logger.warning("心跳检测到 Nacos 实例可能丢失（%s），尝试重新注册", reason)
        try:
            registered = self._register_via_sdk() or self._register_via_openapi()
        except Exception:
            # 双注册路径内部已各自捕获异常，这里再兜一层，绝不让心跳线程崩溃
            logger.warning("Nacos 重新注册过程出现未预期异常（忽略，下次心跳继续尝试）", exc_info=True)
            return
        if registered:
            logger.info("Nacos 重新注册成功: %s @ %s:%s", self._service_name, self._ip, self._port)
        else:
            logger.warning("Nacos 重新注册失败，将在下次心跳继续尝试（受 30 秒节流约束）")

    def _beat_loop(self) -> None:
        """心跳循环：每 5 秒 PUT 一次 beat 接口为实例保活

        实例可能因 Nacos 重启等原因丢失，此时对不存在的实例继续发 beat 无效，
        因此心跳中顺带检测实例丢失信号并自动重新注册（见 _reregister_if_needed）：
        - 显式信号：beat 响应体含 RESOURCE_NOT_FOUND / not found / 20404
          （20404 是 Nacos 2.x 对不存在实例的 beat 响应错误码，随 HTTP 200 + JSON body 返回）；
        - 兜底信号：beat 请求异常或响应非 200 连续达到 _BEAT_FAIL_THRESHOLD 次。
        """
        url = f"http://{self._server_addr}/nacos/v1/ns/instance/beat"
        params = {
            "serviceName": self._service_name,
            "ip": self._ip,
            "port": self._port,
            "groupName": self._group,
        }
        fail_count = 0  # 心跳连续失败计数（任何一次成功即清零）
        while self._running:
            try:
                resp = httpx.put(url, params=params, timeout=_HTTP_TIMEOUT_SECONDS)
                body = (resp.text or "").lower()
                if resp.status_code == 200 and (
                        "resource_not_found" in body or "not found" in body or "20404" in body):
                    # Nacos 明确告知实例 / 服务不存在：实例已丢失，立即重注册（受节流约束）。
                    # 注意：Nacos 2.x（如 2.4.3）对不存在的实例，beat 接口返回
                    # HTTP 200 + JSON {"clientBeatInterval":5000,"code":20404,"lightBeatEnabled":true}，
                    # 响应文本里没有 "resource_not_found"/"not found" 字样，必须同时匹配数字错误码
                    # 20404（字符串包含匹配；20404 足够特殊，正常响应 code=0/10200，误报概率极低）。
                    fail_count = 0
                    self._reregister_if_needed("beat 返回实例不存在（RESOURCE_NOT_FOUND / not found / 20404）")
                elif resp.status_code == 200:
                    # 常见成功体为 "ok"，部分版本返回 JSON，这里只做宽松判定
                    fail_count = 0
                else:
                    fail_count += 1
                    logger.warning("Nacos 心跳响应异常: status=%s body=%s", resp.status_code, resp.text)
                    if fail_count >= _BEAT_FAIL_THRESHOLD:
                        self._reregister_if_needed(f"心跳连续失败 {fail_count} 次")
                        fail_count = 0  # 触发过一次重注册后重置计数，避免每轮心跳都触发
            except Exception:
                fail_count += 1
                logger.warning("Nacos 心跳请求失败（Nacos 不可达时实例会被自动摘除）", exc_info=True)
                if fail_count >= _BEAT_FAIL_THRESHOLD:
                    self._reregister_if_needed(f"心跳连续失败 {fail_count} 次")
                    fail_count = 0
            # 分片 sleep，保证停机时能及时退出
            for _ in range(10):
                if not self._running:
                    return
                time.sleep(_BEAT_INTERVAL_SECONDS / 10)


# 模块级注册器单例：按配置初始化，main.py 的 lifespan 负责调用
registrar = NacosRegistrar(
    server_addr=settings.NACOS_SERVER_ADDR,
    service_name=settings.AI_SERVICE_NAME,
    ip=settings.AI_SERVICE_IP,
    port=settings.AI_SERVICE_PORT,
)


def register() -> None:
    """注册服务（供 main.py 启动时经后台线程调用）；内部再兜一层，确保绝不向外抛异常"""
    try:
        registrar.register()
    except Exception:
        logger.warning("Nacos 注册过程出现未预期异常（忽略，服务照常启动）", exc_info=True)


def deregister() -> None:
    """反注册（供 main.py 停机时经后台线程调用）"""
    try:
        registrar.deregister()
    except Exception:
        logger.warning("Nacos 反注册过程出现未预期异常（忽略）", exc_info=True)
