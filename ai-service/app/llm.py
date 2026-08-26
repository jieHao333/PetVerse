"""LLM 客户端封装

- 真实模式：langchain-openai 接 DeepSeek（OpenAI 兼容 API），流式逐 token 输出
- mock 模式：不依赖任何外部服务，按打字机节奏输出内置可爱回复
"""
import asyncio
from typing import AsyncIterator

from app.config import settings

# 真实模式客户端单例（惰性创建；mock 模式下永远不会触发创建）
_client = None


def get_client():
    """获取 ChatOpenAI 客户端单例（首次调用时创建）

    langchain_openai 的 import 放在函数内部做惰性加载：
    一是 mock 模式下省去模块加载开销，二是该依赖缺失时 mock 模式依旧可用。
    """
    global _client
    if _client is None:
        from langchain_openai import ChatOpenAI

        _client = ChatOpenAI(
            base_url=settings.DEEPSEEK_BASE_URL,   # DeepSeek 的 OpenAI 兼容地址
            api_key=settings.DEEPSEEK_API_KEY,
            model=settings.DEEPSEEK_MODEL,
            streaming=True,                        # 流式输出
            max_tokens=settings.LLM_MAX_TOKENS,
            temperature=settings.LLM_TEMPERATURE,
            timeout=60,                            # 单次请求 60 秒超时：DeepSeek 挂起时避免长期占用并发信号量（默认无超时最长可挂 30 分钟）
            max_retries=1,                         # 重试上限 1 次：失败快速暴露给心跳超时兜底，不放大等待时间
        )
    return _client


async def stream_chat(messages: list) -> AsyncIterator[str]:
    """真实模式：流式对话，逐 token yield 回复文本片段

    :param messages: OpenAI 消息列表 [{"role": "system"|"user"|"assistant", "content": "..."}]
    """
    async for chunk in get_client().astream(messages):
        content = chunk.content
        if content:  # 跳过空 chunk（如仅携带 usage 的尾包）
            yield content


# ---------- mock 模式 ----------

# 内置候选回复池：{name} 会被替换为宠物名字
_MOCK_REPLIES = [
    "喵呜~{name}在这里呀！蹭蹭你的手心，最喜欢你陪我玩啦 (≧▽≦)",
    "汪汪！{name}今天精神满满，要不要一起去院子里晒太阳呀？",
    "{name}歪着头想了想……主人说的话好深奥，但是蹭蹭你准没错！(=^･ω･^=)",
    "呼噜呼噜~被主人摸头的{name}最幸福了，我们永远是好朋友哦！",
    "{name}的等级又悄悄涨了一点点！都是托主人的福，继续加油鸭~ (๑•̀ㅂ•́)و",
    "唔……{name}有点困了，主人不忙的时候要多陪陪我嘛，呜呜~",
]


async def mock_stream(message: str, pet_name: str = "") -> AsyncIterator[str]:
    """mock 模式：按 2-4 字符为块逐段输出，模拟打字机效果的可爱回复

    :param message: 用户消息（仅用来做简单分流，让不同输入得到不同回复）
    :param pet_name: 宠物名字，会嵌入回复内容中
    """
    name = (pet_name or "").strip() or "小宠物"
    # 依据消息长度取模挑选回复：同一输入稳定复现，不同输入有差异
    reply = _MOCK_REPLIES[len(message.strip()) % len(_MOCK_REPLIES)].format(name=name)

    pos = 0
    while pos < len(reply):
        # 块大小在 2~4 字符之间循环变化，模拟真人打字节奏
        size = 2 + ((pos // 2) % 3)
        yield reply[pos:pos + size]
        pos += size
        await asyncio.sleep(0.05)  # 每段间隔 0.05 秒，模拟打字机效果
