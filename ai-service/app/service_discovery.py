"""基于 Nacos 的下游服务发现与客户端负载均衡。

微服务模式按逻辑服务名发现实例；单体模式把所有业务请求统一定位到
``MONOLITH_SERVICE_NAME``，避免逐一发现 pet / space / shop 等不存在的服务名。
两种模式都在健康实例间轮询，因此单体多实例与微服务多实例使用同一套分流逻辑。
"""

import asyncio
import logging
import time
from typing import Dict, List, Tuple

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT_SECONDS = 5


class ServiceDiscoveryError(RuntimeError):
    """Nacos 无法返回目标服务的健康实例。"""


class NacosServiceDiscovery:
    """带短 TTL 缓存和轮询游标的 Nacos 服务发现器。

    缓存避免每次业务请求都访问 Nacos；刷新失败时优先使用上一次成功获取的实例列表，
    防止注册中心短暂抖动放大为全部业务调用失败。Nacos 明确返回空实例列表时不使用
    旧缓存，避免继续访问已经下线或不健康的实例。
    """

    def __init__(self, server_addr: str, *, group: str = "DEFAULT_GROUP",
                 namespace: str = "", deploy_mode: str = "microservice",
                 monolith_service_name: str = "petverse-single",
                 cache_ttl: float = 10.0) -> None:
        server_addr = server_addr.strip().rstrip("/")
        self._server_addr = (server_addr if server_addr.startswith(("http://", "https://"))
                             else f"http://{server_addr}")
        self._group = group
        self._namespace = namespace
        self._deploy_mode = deploy_mode
        self._monolith_service_name = monolith_service_name
        self._cache_ttl = cache_ttl
        # service -> (刷新时间, 有序实例地址列表)
        self._cache: Dict[str, Tuple[float, Tuple[str, ...]]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._cursors: Dict[str, int] = {}

    async def resolve(self, service_name: str) -> str:
        """解析逻辑服务名并轮询选择一个健康实例的 HTTP 根地址。"""
        target = self._target_service(service_name)
        instances = await self._get_instances(target)
        # 选取与游标推进之间没有 await，同一事件循环内不会交错，能保证连续请求轮换实例。
        cursor = self._cursors.get(target, 0)
        selected = instances[cursor % len(instances)]
        self._cursors[target] = cursor + 1
        logger.debug("Nacos 路由: logical=%s target=%s instance=%s", service_name, target, selected)
        return selected

    def _target_service(self, service_name: str) -> str:
        """单体部署直接路由到单体服务名，其余部署保留逻辑服务名。"""
        if self._deploy_mode == "monolith":
            return self._monolith_service_name
        return service_name

    async def _get_instances(self, service_name: str) -> Tuple[str, ...]:
        """读取实例缓存；过期后单飞刷新，网络失败时允许使用旧缓存。"""
        cached = self._cache.get(service_name)
        if cached and time.monotonic() - cached[0] < self._cache_ttl:
            return cached[1]

        # 字典写入前没有 await，同事件循环内每个服务只会拿到一把锁。
        lock = self._locks.setdefault(service_name, asyncio.Lock())
        async with lock:
            cached = self._cache.get(service_name)
            if cached and time.monotonic() - cached[0] < self._cache_ttl:
                return cached[1]
            try:
                instances = await asyncio.to_thread(self._fetch_instances, service_name)
            except Exception as exc:
                if cached:
                    logger.warning("Nacos 刷新 %s 失败，暂用旧实例列表（%s 个）", service_name,
                                   len(cached[1]))
                    return cached[1]
                raise ServiceDiscoveryError(
                    f"无法从 Nacos 获取服务 {service_name} 的实例") from exc

            if not instances:
                # 空列表是明确的注册中心状态，不是网络故障；不能继续路由到已下线实例。
                self._cache.pop(service_name, None)
                raise ServiceDiscoveryError(f"Nacos 中服务 {service_name} 没有健康可用实例")

            self._cache[service_name] = (time.monotonic(), tuple(instances))
            return tuple(instances)

    def _fetch_instances(self, service_name: str) -> List[str]:
        """通过 Nacos OpenAPI 拉取健康且启用、权重大于零的实例。"""
        params = {
            "serviceName": service_name,
            "groupName": self._group,
            "healthyOnly": "true",
        }
        if self._namespace:
            params["namespaceId"] = self._namespace
        resp = httpx.get(f"{self._server_addr}/nacos/v1/ns/instance/list",
                         params=params, timeout=_HTTP_TIMEOUT_SECONDS)
        resp.raise_for_status()
        hosts = resp.json().get("hosts") or []
        instances = []
        for host in hosts:
            if not host.get("healthy", True) or not host.get("enabled", True):
                continue
            try:
                if float(host.get("weight", 1.0)) <= 0:
                    continue
                port = int(host["port"])
            except (KeyError, TypeError, ValueError):
                continue
            ip = str(host.get("ip") or "").strip()
            if not ip:
                continue
            # IPv6 字面量放进 URL 时必须加方括号。
            host_part = f"[{ip}]" if ":" in ip and not ip.startswith("[") else ip
            instances.append(f"http://{host_part}:{port}")
        # 固定排序避免 Nacos 返回顺序变化导致轮询序列不稳定。
        return sorted(instances)


discovery = NacosServiceDiscovery(
    server_addr=settings.NACOS_SERVER_ADDR,
    group=settings.NACOS_GROUP,
    namespace=settings.NACOS_NAMESPACE,
    deploy_mode=settings.SERVICE_DEPLOY_MODE,
    monolith_service_name=settings.MONOLITH_SERVICE_NAME,
    cache_ttl=settings.SERVICE_DISCOVERY_CACHE_TTL,
)
