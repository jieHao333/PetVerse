"""Nacos 注册地址解析的单元测试（不启动 Nacos、不发真实网络请求）。"""

import unittest
from unittest.mock import patch

from app import nacos_client


class AdvertiseIpTest(unittest.TestCase):
    """注册地址的选取规则：配置优先、探测兜底、不可达时退回回环。"""

    def test_configured_ip_wins(self):
        with patch.object(nacos_client, "_self_reachable", return_value=True), \
                patch.object(nacos_client, "_detect_local_ip", return_value="10.0.0.9"):
            self.assertEqual(
                nacos_client._resolve_advertise_ip("10.0.0.8", 8086), "10.0.0.8")

    def test_empty_config_falls_back_to_detection(self):
        with patch.object(nacos_client, "_self_reachable", return_value=True), \
                patch.object(nacos_client, "_detect_local_ip", return_value="10.0.0.9"):
            self.assertEqual(
                nacos_client._resolve_advertise_ip("", 8086), "10.0.0.9")

    def test_wildcard_is_treated_as_auto(self):
        # 0.0.0.0 对消费方没有意义，按「未配置」处理而不是原样注册
        with patch.object(nacos_client, "_self_reachable", return_value=True), \
                patch.object(nacos_client, "_detect_local_ip", return_value="10.0.0.9"):
            self.assertEqual(
                nacos_client._resolve_advertise_ip("0.0.0.0", 8086), "10.0.0.9")

    def test_unreachable_address_degrades_to_loopback(self):
        # 只绑了回环却注册网卡地址时，宁可退回 127.0.0.1 也不要注册一个连不上的地址
        with patch.object(nacos_client, "_self_reachable", return_value=False), \
                patch.object(nacos_client, "_detect_local_ip", return_value="10.0.0.9"):
            self.assertEqual(
                nacos_client._resolve_advertise_ip("", 8086), "127.0.0.1")

    def test_bind_host_used_when_detected_unreachable(self):
        # 只听某一张网卡时，探测可能选到另一张；只有真能连上的地址才注册出去
        with patch.object(nacos_client, "_self_reachable",
                          side_effect=lambda ip, port: ip == "10.0.0.8"), \
                patch.object(nacos_client, "_detect_local_ip", return_value="10.0.0.9"):
            self.assertEqual(
                nacos_client._resolve_advertise_ip("", 8086, "10.0.0.8"), "10.0.0.8")

    def test_wildcard_bind_host_is_skipped(self):
        # 监听 0.0.0.0 时不构成候选，交给探测决定
        with patch.object(nacos_client, "_self_reachable", return_value=True), \
                patch.object(nacos_client, "_detect_local_ip", return_value="10.0.0.9"):
            self.assertEqual(
                nacos_client._resolve_advertise_ip("", 8086, "0.0.0.0"), "10.0.0.9")

    def test_configured_loopback_ignored_when_bound_publicly(self):
        # 残留的 AI_SERVICE_IP=127.0.0.1 + 已改成 --host 0.0.0.0：回环自检必然通过，
        # 若不主动忽略就会「一直注册 127.0.0.1 却看不到任何报错」
        with patch.object(nacos_client, "_self_reachable", return_value=True), \
                patch.object(nacos_client, "_detect_local_ip", return_value="10.0.0.9"):
            self.assertEqual(
                nacos_client._resolve_advertise_ip("127.0.0.1", 8086, "0.0.0.0"), "10.0.0.9")

    def test_configured_loopback_kept_for_local_run(self):
        # 本机自测：监听回环、注册回环，属正常组合
        with patch.object(nacos_client, "_self_reachable", return_value=True), \
                patch.object(nacos_client, "_detect_local_ip", return_value="10.0.0.9"):
            self.assertEqual(
                nacos_client._resolve_advertise_ip("127.0.0.1", 8086, "127.0.0.1"), "127.0.0.1")

    def test_loopback_host_detection(self):
        self.assertTrue(nacos_client._is_loopback_host("localhost"))
        self.assertTrue(nacos_client._is_loopback_host("127.0.0.1"))
        self.assertTrue(nacos_client._is_loopback_host(""))
        self.assertFalse(nacos_client._is_loopback_host("10.1.0.14"))


if __name__ == "__main__":
    unittest.main()
