"""Nacos 服务发现与负载均衡的单元测试。"""

import unittest
from unittest.mock import Mock, patch

from app.service_discovery import NacosServiceDiscovery, ServiceDiscoveryError


def _nacos_response(hosts):
    """构造 Nacos 实例列表响应。"""
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"hosts": hosts}
    return response


class NacosServiceDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    """覆盖健康实例过滤、轮询、单体映射与故障兜底。"""

    @patch("app.service_discovery.httpx.get")
    async def test_round_robin_and_health_filter(self, mock_get):
        mock_get.return_value = _nacos_response([
            {"ip": "10.0.0.2", "port": 8081, "healthy": True, "enabled": True, "weight": 1},
            {"ip": "10.0.0.3", "port": 8081, "healthy": True, "enabled": True, "weight": 1},
            {"ip": "10.0.0.4", "port": 8081, "healthy": False, "enabled": True, "weight": 1},
            {"ip": "10.0.0.5", "port": 8081, "healthy": True, "enabled": False, "weight": 1},
            {"ip": "10.0.0.6", "port": 8081, "healthy": True, "enabled": True, "weight": 0},
        ])
        discovery = NacosServiceDiscovery("localhost:8848", cache_ttl=60)

        urls = [await discovery.resolve("pet-service") for _ in range(3)]

        self.assertEqual(urls, [
            "http://10.0.0.2:8081",
            "http://10.0.0.3:8081",
            "http://10.0.0.2:8081",
        ])
        # 缓存有效期内只访问一次 Nacos，不能在每次业务请求上增加注册中心开销。
        mock_get.assert_called_once()
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["serviceName"], "pet-service")
        self.assertEqual(params["groupName"], "DEFAULT_GROUP")
        self.assertEqual(params["healthyOnly"], "true")

    @patch("app.service_discovery.httpx.get")
    async def test_monolith_mode_maps_every_service_to_single_and_balances(self, mock_get):
        mock_get.return_value = _nacos_response([
            {"ip": "10.0.0.10", "port": 8080, "healthy": True, "enabled": True},
            {"ip": "10.0.0.11", "port": 8080, "healthy": True, "enabled": True},
        ])
        discovery = NacosServiceDiscovery(
            "http://localhost:8848", deploy_mode="monolith",
            monolith_service_name="petverse-single", cache_ttl=60)

        urls = [
            await discovery.resolve("pet-service"),
            await discovery.resolve("space-service"),
            await discovery.resolve("shop-service"),
        ]

        self.assertEqual(urls, [
            "http://10.0.0.10:8080",
            "http://10.0.0.11:8080",
            "http://10.0.0.10:8080",
        ])
        mock_get.assert_called_once()
        self.assertEqual(
            mock_get.call_args.kwargs["params"]["serviceName"], "petverse-single")

    async def test_network_failure_uses_stale_cache_when_available(self):
        discovery = NacosServiceDiscovery("localhost:8848", cache_ttl=0)
        discovery._fetch_instances = Mock(side_effect=[
            ["http://10.0.0.2:8081"],
            RuntimeError("nacos unavailable"),
        ])

        first = await discovery.resolve("pet-service")
        second = await discovery.resolve("pet-service")

        self.assertEqual(first, "http://10.0.0.2:8081")
        self.assertEqual(second, "http://10.0.0.2:8081")

    async def test_explicit_empty_instance_list_never_uses_stale_cache(self):
        discovery = NacosServiceDiscovery("localhost:8848", cache_ttl=0)
        discovery._fetch_instances = Mock(side_effect=[
            ["http://10.0.0.2:8081"],
            [],
        ])
        await discovery.resolve("pet-service")

        with self.assertRaises(ServiceDiscoveryError):
            await discovery.resolve("pet-service")
        self.assertNotIn("pet-service", discovery._cache)

    async def test_first_fetch_failure_without_cache_raises(self):
        discovery = NacosServiceDiscovery("localhost:8848")
        discovery._fetch_instances = Mock(side_effect=RuntimeError("nacos unavailable"))

        with self.assertRaises(ServiceDiscoveryError):
            await discovery.resolve("pet-service")

    @patch("app.service_discovery.httpx.get")
    async def test_namespace_and_ipv6(self, mock_get):
        mock_get.return_value = _nacos_response([
            {"ip": "::1", "port": 8086, "healthy": True, "enabled": True},
        ])
        discovery = NacosServiceDiscovery(
            "localhost:8848", namespace="petverse-test", cache_ttl=60)

        url = await discovery.resolve("ai-service")

        self.assertEqual(url, "http://[::1]:8086")
        params = mock_get.call_args.kwargs["params"]
        self.assertEqual(params["namespaceId"], "petverse-test")


if __name__ == "__main__":
    unittest.main()
