"""模型注册表的纯逻辑测试（不联网、不连库）。

覆盖三处最容易回归的地方：Base URL 规整（两家 SDK 语义不同）、Key 脱敏，
以及「数据库生效模型 → .env 兜底」的解析顺序。
"""

import asyncio
import unittest
from unittest.mock import patch

from app import ext_store
from app import model_registry as mr
from app.config import settings


def _run(coro):
    """在独立事件循环里跑一次协程。"""
    return asyncio.run(coro)


class NormalizeBaseUrlTest(unittest.TestCase):
    """地址规整：填错 /v1 时不该让人对着 404 猜。"""

    def test_openai_keeps_version_prefix(self):
        self.assertEqual(
            mr.normalize_base_url("openai", "https://api.deepseek.com/v1/"),
            "https://api.deepseek.com/v1")

    def test_openai_strips_full_endpoint(self):
        self.assertEqual(
            mr.normalize_base_url("openai", "https://api.deepseek.com/v1/chat/completions"),
            "https://api.deepseek.com/v1")

    def test_anthropic_strips_version_prefix(self):
        self.assertEqual(
            mr.normalize_base_url("anthropic", "https://api.anthropic.com/v1"),
            "https://api.anthropic.com")

    def test_anthropic_strips_message_endpoint(self):
        self.assertEqual(
            mr.normalize_base_url("anthropic", "https://api.anthropic.com/v1/messages"),
            "https://api.anthropic.com")

    def test_gateway_path_is_preserved(self):
        # 智谱一类网关的 Anthropic 路径本身带 /anthropic，不能被误删
        self.assertEqual(
            mr.normalize_base_url("anthropic", "https://open.bigmodel.cn/api/anthropic/"),
            "https://open.bigmodel.cn/api/anthropic")

    def test_empty(self):
        self.assertEqual(mr.normalize_base_url("openai", "  "), "")


class MaskKeyTest(unittest.TestCase):
    """Key 只回显尾号，避免凭证进浏览器与日志。"""

    def test_mask_long_key(self):
        self.assertEqual(mr.mask_key("sk-1234567890abcdef"), "sk-…cdef")

    def test_mask_short_key(self):
        self.assertEqual(mr.mask_key("short"), "s***")

    def test_mask_empty(self):
        self.assertEqual(mr.mask_key(""), "")


class ResolveTest(unittest.TestCase):
    """解析顺序：生效模型可用就用它，任何一步不成立都回落 .env。"""

    def setUp(self):
        mr._cache = None

    def tearDown(self):
        mr._cache = None

    def _provider(self, **overrides):
        data = {
            "id": 7, "name": "DeepSeek", "protocol": "openai",
            "baseUrl": "https://api.deepseek.com/v1/", "apiKey": "sk-live",
            "models": [{"id": "deepseek-chat", "label": "Chat", "vision": True, "maxTokens": 2048}],
            "enabled": True,
        }
        data.update(overrides)
        return data

    def _resolve_with(self, provider, active=(7, "deepseek-chat")):
        async def fake_active():
            return active

        async def fake_provider(_pid):
            return provider

        with patch.object(ext_store, "available", return_value=True), \
                patch.object(ext_store, "get_active_model", fake_active), \
                patch.object(ext_store, "get_model_provider", fake_provider):
            return _run(mr.current())

    def test_uses_db_model(self):
        cfg = self._resolve_with(self._provider())
        self.assertEqual(cfg.source, "db")
        self.assertEqual(cfg.model, "deepseek-chat")
        self.assertEqual(cfg.max_tokens, 2048)
        self.assertTrue(cfg.vision)
        # 地址在解析阶段就规整好，构造客户端时不再二次处理
        self.assertEqual(cfg.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(cfg.describe()["providerName"], "DeepSeek")
        self.assertNotIn("apiKey", cfg.describe())

    def test_falls_back_when_provider_disabled(self):
        cfg = self._resolve_with(self._provider(enabled=False))
        self.assertEqual(cfg.source, "env")
        self.assertIn("已停用", cfg.note)

    def test_falls_back_when_model_removed(self):
        cfg = self._resolve_with(self._provider(), active=(7, "gone"))
        self.assertEqual(cfg.source, "env")
        self.assertIn("已被移除", cfg.note)

    def test_falls_back_without_active_selection(self):
        cfg = self._resolve_with(self._provider(), active=(0, ""))
        self.assertEqual(cfg.source, "env")
        self.assertEqual(cfg.model, settings.LLM_MODEL)

    def test_falls_back_when_store_unavailable(self):
        with patch.object(ext_store, "available", return_value=False):
            cfg = _run(mr.current())
        self.assertEqual(cfg.source, "env")
        self.assertIn("PostgreSQL", cfg.note)

    def test_vision_follows_active_model(self):
        """选了不支持图片的模型时，不能用 .env 的视觉模型「顶替」——否则行为不可预期。"""
        self._resolve_with(self._provider(
            models=[{"id": "deepseek-chat", "label": "Chat", "vision": False, "maxTokens": 0}]))
        with patch.object(settings, "LLM_VISION_MODEL", "qwen-vl"), \
                patch.object(settings, "LLM_VISION_API_KEY", "k"), \
                patch.object(settings, "LLM_VISION_BASE_URL", "https://x"):
            self.assertFalse(_run(mr.vision_ready()))

    def test_vision_uses_env_on_fallback(self):
        """兜底到 .env 时，图片走 .env 的视觉模型（老部署行为不变）。"""
        self._resolve_with(self._provider(), active=(0, ""))
        with patch.object(settings, "LLM_VISION_MODEL", "qwen-vl"), \
                patch.object(settings, "LLM_VISION_API_KEY", "k"), \
                patch.object(settings, "LLM_VISION_BASE_URL", "https://x"):
            self.assertTrue(_run(mr.vision_ready()))


class IsConfiguredTest(unittest.TestCase):
    """``settings.is_mock`` 依赖它判断「有没有可用的模型」。"""

    def setUp(self):
        mr._cache = None

    def tearDown(self):
        mr._cache = None

    def test_env_credentials_count(self):
        with patch.object(settings, "LLM_API_KEY", "sk-x"), patch.object(settings, "LLM_MODEL", "m"):
            self.assertTrue(mr.is_configured())

    def test_no_credentials_is_mock(self):
        with patch.object(settings, "LLM_API_KEY", ""), patch.object(settings, "LLM_MODEL", ""):
            self.assertFalse(mr.is_configured())
            self.assertTrue(settings.is_mock)


if __name__ == "__main__":
    unittest.main()
