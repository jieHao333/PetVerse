"""LLM / Embedding 客户端封装

- 真实模式：langchain-openai 接任意 OpenAI 兼容服务（DeepSeek / 阿里云百炼等），
  只认 LLM_BASE_URL + LLM_MODEL + LLM_API_KEY，切换服务商无需改代码；
- Embedding：同样 OpenAI 格式，供 RAG 向量检索使用（DeepSeek 无 embedding 接口，需另配）；
- mock 模式：不依赖任何外部服务，按打字机节奏输出内置的养宠咨询回复。
"""
import asyncio
import json
import logging
from typing import AsyncIterator, Optional, Type

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

# 真实模式客户端单例（惰性创建；mock 模式下永远不会触发创建）
_client = None
_vision_client = None
_embeddings = None


def get_client():
    """获取 ChatOpenAI 客户端单例（首次调用时创建）

    langchain_openai 的 import 放在函数内部做惰性加载：
    一是 mock 模式下省去模块加载开销，二是该依赖缺失时 mock 模式依旧可用。
    """
    global _client
    if _client is None:
        from langchain_openai import ChatOpenAI

        _client = ChatOpenAI(
            base_url=settings.LLM_BASE_URL,          # OpenAI 兼容地址（DeepSeek / 阿里云百炼等）
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
            streaming=True,                          # 流式输出
            max_tokens=settings.LLM_MAX_TOKENS,
            temperature=settings.LLM_TEMPERATURE,
            timeout=60,                              # 单次请求 60 秒超时：避免长期占用并发信号量
            max_retries=1,                           # 重试上限 1 次：失败快速暴露给心跳超时兜底
        )
    return _client


def get_vision_client():
    """获取视觉理解（多模态）ChatOpenAI 客户端单例

    与文本客户端分开配置（LLM_VISION_*）：文本走 DeepSeek、图片走百炼 qwen-vl
    这类「不同服务商混搭」是常见形态；Key / BaseURL 缺省时已回落到 LLM_*。
    调用方（chat_graph.generate）保证仅在 settings.vision_enabled 时才会走到这里，
    未配置视觉模型时图片不会进入消息内容（降级为文字占位，见 app/media.py）。
    """
    global _vision_client
    if _vision_client is None:
        from langchain_openai import ChatOpenAI

        _vision_client = ChatOpenAI(
            base_url=settings.LLM_VISION_BASE_URL,
            api_key=settings.LLM_VISION_API_KEY,
            model=settings.LLM_VISION_MODEL,
            streaming=True,
            max_tokens=settings.LLM_MAX_TOKENS,
            temperature=settings.LLM_TEMPERATURE,
            timeout=90,                              # 图片上下行体积大，超时略宽于文本客户端
            max_retries=1,
        )
    return _vision_client


def get_embeddings():
    """获取 OpenAIEmbeddings 单例（RAG 向量化用）

    Embedding 未配置时抛出异常，调用方（vectorstore）据此降级为关键词检索。
    """
    global _embeddings
    if _embeddings is None:
        from langchain_openai import OpenAIEmbeddings

        _embeddings = OpenAIEmbeddings(
            base_url=settings.EMBEDDING_BASE_URL,
            api_key=settings.EMBEDDING_API_KEY,
            model=settings.EMBEDDING_MODEL,
            dimensions=settings.EMBEDDING_DIM,       # 阿里云 text-embedding-v3 支持指定维度
            check_embedding_ctx_length=False,         # 兼容非 OpenAI 官方服务（避免 tiktoken 分词报错）
        )
    return _embeddings


async def stream_chat(messages: list) -> AsyncIterator[str]:
    """真实模式：流式对话，逐 token yield 回复文本片段

    :param messages: OpenAI 消息列表 [{"role": "system"|"user"|"assistant", "content": "..."}]
    """
    async for chunk in get_client().astream(messages):
        content = chunk.content
        if content:  # 跳过空 chunk（如仅携带 usage 的尾包）
            # 部分 OpenAI 兼容服务返回 content 为分块列表（多模态格式），统一拼接为文本
            if isinstance(content, str):
                yield content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, str):
                        yield part
                    elif isinstance(part, dict) and part.get("type") == "text":
                        yield part.get("text", "")


async def ainvoke_text(system_prompt: str, user_prompt: str,
                       temperature: Optional[float] = None,
                       max_tokens: Optional[int] = None) -> str:
    """一次性（非流式）调用，返回完整文本；用于摘要 / 分类 / 意图识别等短任务"""
    llm = get_client()
    if temperature is not None or max_tokens is not None:
        llm = llm.bind(temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
                       max_tokens=max_tokens or settings.LLM_MAX_TOKENS)
    resp = await llm.ainvoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])
    content = resp.content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content
                       if isinstance(p, dict) and p.get("type") == "text")
    return content or ""


# ---------- 结构化输出（跨服务商降级） ----------

# 依次尝试的结构化输出方式：
#   json_schema      —— 最优，服务端强约束（OpenAI 等支持）
#   function_calling —— 工具调用实现（部分服务商的思考模式不支持 tool_choice）
#   json_mode        —— 仅要求返回 JSON，再在客户端按 Pydantic 校验（兼容性最好）
_STRUCTURED_METHODS = ("json_schema", "function_calling", "json_mode")
_structured_method: Optional[str] = None   # 缓存已探明可用的方式，避免每次都试错


def _json_mode_hint(schema: Type[BaseModel]) -> str:
    """json_mode 下把 JSON Schema 显式写进 System Prompt（否则模型不知道字段结构）"""
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return ("\n\n必须严格按以下 JSON Schema 输出一个 JSON 对象，只输出 JSON，不要任何额外文字：\n"
            + schema_json)


async def astructured_invoke(schema: Type[BaseModel], system_prompt: str,
                             user_prompt: str) -> BaseModel:
    """跨服务商的结构化输出调用，返回 schema 实例

    OpenAI 兼容服务对结构化输出的支持差异很大：DeepSeek 的思考模型既不支持
    json_schema，也不支持 tool_choice。这里按 json_schema → function_calling →
    json_mode 顺序尝试，并缓存首次成功的方式；json_mode 会附带显式 Schema 提示。
    全部失败时抛出最后一次异常，由调用方决定是否降级为规则逻辑。
    """
    global _structured_method
    llm = get_client()
    # 已探明方式优先，失败则回退到其余方式重新探测
    order = ([_structured_method] if _structured_method else []) + \
            [m for m in _STRUCTURED_METHODS if m != _structured_method]

    last_error: Optional[Exception] = None
    for method in order:
        try:
            if method == "json_mode":
                client = llm.with_structured_output(schema, method="json_mode")
                return await client.ainvoke([
                    {"role": "system", "content": system_prompt + _json_mode_hint(schema)},
                    {"role": "user", "content": user_prompt},
                ])
            client = llm.with_structured_output(schema, method=method)
            result = await client.ainvoke([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ])
            if result is not None:
                _structured_method = method
                return result
        except Exception as exc:
            last_error = exc
            # 缓存的方式失效则清除，后续重新探测
            if _structured_method == method:
                _structured_method = None
            logger.debug("结构化输出方式 %s 不可用：%s", method, str(exc)[:120])
    if last_error is not None:
        raise last_error
    raise RuntimeError("结构化输出失败：模型未返回有效结果")


# ---------- mock 模式 ----------

# 内置候选回复池：{name} 会被替换为宠物名字（养宠顾问口吻的中立回复，供联调使用）
_MOCK_REPLIES = [
    "收到你的问题。当前是本地模拟回复模式：关于{name}的健康与习性咨询，建议在 .env 中配置 LLM_API_KEY 后获取真实解答。",
    "这是模拟回复。一般来说，宠物出现食欲下降、精神萎靡等异常时，建议先记录症状持续时间，必要时尽快就医排查。",
    "模拟回复：关于{name}的喂养问题，建议按年龄阶段选择对应粮，定时定量，并保证充足饮水。",
    "当前为本地模拟模式，未接入大模型。请配置 LLM_API_KEY 后重试，即可获得针对性的养宠建议。",
    "模拟回复：宠物行为习惯的养成需要耐心，建议采用正向引导（奖励为主），避免惩罚式训练。",
    "这是模拟回复。若{name}出现呕吐、腹泻等症状，可先禁食观察半天，症状加重请及时咨询宠物医生。",
]


async def mock_stream(message: str, pet_name: str = "") -> AsyncIterator[str]:
    """mock 模式：按 2-4 字符为块逐段输出，模拟打字机效果的咨询回复

    :param message: 用户消息（仅用来做简单分流，让不同输入得到不同回复）
    :param pet_name: 宠物名字，会嵌入回复内容中
    """
    name = (pet_name or "").strip() or "你的宠物"
    # 依据消息长度取模挑选回复：同一输入稳定复现，不同输入有差异
    reply = _MOCK_REPLIES[len(message.strip()) % len(_MOCK_REPLIES)].format(name=name)

    pos = 0
    while pos < len(reply):
        # 块大小在 2~4 字符之间循环变化，模拟真人打字节奏
        size = 2 + ((pos // 2) % 3)
        yield reply[pos:pos + size]
        pos += size
        await asyncio.sleep(0.05)  # 每段间隔 0.05 秒，模拟打字机效果
