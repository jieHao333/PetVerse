"""BizClient 通过服务发现访问下游的单元测试。"""

import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.clients import BizClient


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
                patch.object(client, "_get_client", return_value=http_client):
            pets = await client.get_my_pets(user_id=42)

        self.assertEqual(pets, [{"id": 1, "name": "豆豆"}])
        resolver.assert_awaited_once_with("pet-service")
        http_client.get.assert_awaited_once_with(
            "http://10.0.0.8:8081/pet/my-list",
            params=None,
            headers={"X-User-Id": "42"},
        )

    async def test_write_uses_discovered_instance_and_preserves_business_error(self):
        http_client = Mock()
        response = Mock(status_code=200)
        response.json.return_value = {"code": 400, "msg": "库存不足", "data": None}
        http_client.request = AsyncMock(return_value=response)
        resolver = AsyncMock(return_value="http://10.0.0.9:8087")
        client = BizClient()

        with patch("app.clients.discovery.resolve", new=resolver), \
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
            headers={"X-User-Id": "42"},
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


if __name__ == "__main__":
    unittest.main()
