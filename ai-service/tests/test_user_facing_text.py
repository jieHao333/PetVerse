"""用户可见文案的「不泄漏管理端信息」测试。

模型切换、供应商、Base URL、API Key、部署参数都属于管理端概念：普通用户只是在用
AI 对话，服务不可用或走 mock 兜底时，看到的每一句话都不能带这些信息。
本测试把这条约束固化下来，避免以后改文案时又把管理端口径写回用户侧。
"""

import asyncio
import unittest
from unittest.mock import patch

from app import chat, llm
from app.agent import graph
from app.kb.search import RagUnavailable

# 管理端 / 运维口径：出现在用户可见文案里即视为泄漏
FORBIDDEN_TERMS = (
    "模拟", "mock", "MOCK", "LLM", ".env", "API Key", "api key",
    "管理员", "后台", "管理页", "配置", "供应商", "模型", "环境变量", "部署", "OSS",
)


def _assert_clean(case: unittest.TestCase, text: str, label: str) -> None:
    for term in FORBIDDEN_TERMS:
        case.assertNotIn(term, text, f"{label}出现了管理端口径「{term}」：{text}")


class MockReplyTest(unittest.TestCase):
    """mock 兜底回复必须读起来像正常养护建议，而不是部署提示。"""

    def test_replies_are_user_facing(self):
        self.assertTrue(llm._MOCK_REPLIES)
        for reply in llm._MOCK_REPLIES:
            _assert_clean(self, reply.replace("{name}", "豆豆"), "mock 回复")

    def test_stream_output_is_clean(self):
        # 真正走一遍 mock 流，确认拼出来的完整回复同样干净（片段拼接不会引入额外文案）
        async def collect():
            return "".join([chunk async for chunk in llm.mock_stream("最近有点挑食怎么办", "豆豆")])

        _assert_clean(self, asyncio.run(collect()), "mock 流输出")


class ChatMessageTest(unittest.TestCase):
    """对话链路的固定提示语同样不能带管理端信息。"""

    def test_fixed_messages_are_clean(self):
        for label, text in (
            ("错误提示", chat._ERROR_MSG),
            ("繁忙提示", chat._USER_BUSY_MSG),
            ("上传失败提示", chat._UPLOAD_ERROR_MSG),
        ):
            _assert_clean(self, text, label)


class ToolErrorLeakTest(unittest.TestCase):
    """工具异常默认会被 ToolNode 原文回填给模型，模型可能复述给用户。

    ``ToolNode`` 依赖 Pregel 运行时，无法脱离图直接跑；这里测两件事：
    处理器本身的行为，以及 ``run_tools`` 确实把它挂到了 ToolNode 上。
    """

    def test_internal_detail_is_not_passed_to_model(self):
        message = graph._tool_error_message(
            ValueError("连接 http://10.0.0.9:8080/internal 失败（token=sk-secret）"))
        for term in ("10.0.0.9", "sk-secret", "internal", "token"):
            self.assertNotIn(term, message)
        _assert_clean(self, message, "工具错误回填")

    def test_rag_unavailable_is_raised_not_swallowed(self):
        # 知识检索不可用必须整轮失败（路由层下发通用错误帧），不能被模型「自由发挥」
        with self.assertRaises(RagUnavailable):
            graph._tool_error_message(RagUnavailable("知识库暂时没有可用的内容"))

    def test_sanitizer_is_wired_into_tool_node(self):
        captured = {}

        class FakeNode:
            """替身：只记录构造参数，验证装配关系。"""

            def __init__(self, tools, handle_tool_errors=None):
                captured["handler"] = handle_tool_errors

            async def ainvoke(self, state, config):
                return {"messages": []}

        async def fake_build_tools(_ctx):
            return [object()]

        with patch.object(graph, "build_tools_async", fake_build_tools), \
                patch.object(graph, "ToolNode", FakeNode):
            asyncio.run(graph.run_tools({"user_id": 1, "step": 0}, {}))

        self.assertIs(captured.get("handler"), graph._tool_error_message)


if __name__ == "__main__":
    unittest.main()
