"""BizClient 通过服务发现访问下游的单元测试。"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.clients import BizClient, _identity_headers
from app.config import settings


class BizClientRoutingTest(unittest.IsolatedAsyncioTestCase):
    """确认业务调用使用解析后的实例地址，而不是固定 URL。"""

    async def test_get_uses_discovered_instance_and_injects_identity(self):
        http_client = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {"code": 200, "data": [{"id": 1, "name": "豆豆"}]}
        http_client.get = AsyncMock(return_value=response)
        resolver = AsyncMock(return_value="http://10.0.0.8:8081")
        client = BizClient()

        with patch("app.clients.discovery.resolve", new=resolver), \
                patch.object(settings, "INTERNAL_TOKEN", "unit-test-token"), \
                patch.object(client, "_get_client", return_value=http_client):
            pets = await client.get_my_pets(user_id=42)

        self.assertEqual(pets, [{"id": 1, "name": "豆豆"}])
        resolver.assert_awaited_once_with("pet-service")
        # 身份必须成对下发：内部令牌证明调用方可信，X-User-Id 说明查的是谁
        http_client.get.assert_awaited_once_with(
            "http://10.0.0.8:8081/pet/my-list",
            params=None,
            headers={"X-Internal-Token": "unit-test-token", "X-User-Id": "42"},
        )

    async def test_write_uses_discovered_instance_and_preserves_business_error(self):
        http_client = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {"code": 400, "msg": "库存不足", "data": None}
        http_client.request = AsyncMock(return_value=response)
        resolver = AsyncMock(return_value="http://10.0.0.9:8087")
        client = BizClient()

        with patch("app.clients.discovery.resolve", new=resolver), \
                patch.object(settings, "INTERNAL_TOKEN", "unit-test-token"), \
                patch.object(client, "_get_client", return_value=http_client):
            result = await client.request(
                "POST", "shop-service", "/shop/cart",
                json={"productId": 9, "quantity": 2}, user_id=42)

        self.assertEqual(result, {
            "ok": False, "code": 400, "msg": "库存不足", "data": None,
        })
        resolver.assert_awaited_once_with("shop-service")
        http_client.request.assert_awaited_once_with(
            "POST", "http://10.0.0.9:8087/shop/cart",
            json={"productId": 9, "quantity": 2}, params=None,
            headers={"X-Internal-Token": "unit-test-token", "X-User-Id": "42"},
        )

    async def test_discovery_failure_keeps_read_and_write_contracts(self):
        resolver = AsyncMock(side_effect=RuntimeError("no instance"))
        client = BizClient()

        with patch("app.clients.discovery.resolve", new=resolver):
            pets = await client.get_my_pets(user_id=42)
            result = await client.request(
                "POST", "shop-service", "/shop/cart", json={"productId": 9})

        self.assertEqual(pets, [])
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], 500)


class IdentityHeadersTest(unittest.TestCase):
    """身份头的拼装规则：令牌与用户相互独立，缺哪个就少哪个。"""

    def test_token_and_user_are_both_sent(self):
        with patch.object(settings, "INTERNAL_TOKEN", "  unit-test-token  "):
            headers = _identity_headers(42)
        self.assertEqual(headers, {"X-Internal-Token": "unit-test-token", "X-User-Id": "42"})

    def test_anonymous_call_keeps_only_token(self):
        # user_id=0 表示系统级取数（如商品统计），带 X-User-Id 反而会被下游判为非法
        with patch.object(settings, "INTERNAL_TOKEN", "unit-test-token"):
            self.assertEqual(_identity_headers(0), {"X-Internal-Token": "unit-test-token"})

    def test_empty_token_falls_back_without_header(self):
        # 未配置令牌时不发空头，业务侧据此退回来源 IP 的兼容通道
        with patch.object(settings, "INTERNAL_TOKEN", ""):
            self.assertEqual(_identity_headers(7), {"X-User-Id": "7"})


if __name__ == "__main__":
    unittest.main()
