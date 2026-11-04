"""LLM / Embedding 客户端封装。

职责：把 LangChain 的模型客户端收敛为「按需创建 + 进程内复用」的三个惰性单例，并在其上提供
三类调用方式——流式对话、纯文本调用、结构化（Pydantic）输出；同时内置结构化输出的
多方式自动降级与无 Key 环境下的模拟流。

主要导出：
    - ``get_client``        对话模型客户端（OpenAI 兼容，支持任意服务商）
    - ``get_vision_client`` 视觉模型客户端（多模态看图）
    - ``get_embeddings``    Embedding 客户端（RAG 向量化）
    - ``stream_chat``       流式产出增量文本
    - ``ainvoke_text``      一次性取回完整文本（可临时覆盖温度 / 长度）
    - ``astructured_invoke`` 让模型按 Pydantic schema 输出结构化结果
    - ``mock_stream``       模拟回复流（MOCK 模式或无 Key 时使用）

外部依赖：``langchain-openai``（延迟 import，避免未装依赖时整个服务起不来）、
``langchain_core.messages``、``app.config.settings``。

使用示例：
    from app.llm import astructured_invoke, stream_chat
    from app.schemas import ReviewSummary

    async for delta in stream_chat(messages):
        print(delta, end="")

    summary = await astructured_invoke(ReviewSummary, system_prompt, user_prompt)
"""

import asyncio
import json
import logging
from typing import AsyncIterator, List, Optional, Type

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

# 三个客户端均为「首次使用时创建」：模块 import 阶段不建立连接，
# 这样既能让无凭证环境正常启动，也避免配置错误在 import 时就抛异常导致进程退不出。
_client = None
_vision_client = None
_embeddings = None


def get_client():
    """获取（并缓存）对话模型客户端。

    客户端内部自带连接池，重复创建会浪费连接并丢失重试状态，因此按进程复用。

    Returns:
        ``langchain_openai.ChatOpenAI`` 实例。``streaming=True`` 是硬性要求——对话链路以 SSE
        逐字下发，走非流式接口会导致首字延迟等于整段生成时间。

    Raises:
        ImportError: 未安装 ``langchain-openai`` 时抛出。
    """
    global _client
    if _client is None:
        from langchain_openai import ChatOpenAI

        _client = ChatOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
            streaming=True,
            max_tokens=settings.LLM_MAX_TOKENS,
            temperature=settings.LLM_TEMPERATURE,
            # 单次请求 60s 上限 + 仅重试 1 次：对话场景宁可快速失败走首帧重试逻辑，
            # 也不要在服务端长时间挂住占用并发名额。
            timeout=60,
            max_retries=1,
        )
    return _client


def get_vision_client():
    """获取（并缓存）视觉模型客户端。

    与对话客户端分开配置（base_url / key / model 均独立），因为多数服务商的视觉模型
    是另一个型号。

    Returns:
        ``ChatOpenAI`` 实例。超时放宽到 90s——图片上传与视觉推理明显慢于纯文本。

    Raises:
        ImportError: 未安装 ``langchain-openai`` 时抛出。
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
            timeout=90,
            max_retries=1,
        )
    return _vision_client


def get_embeddings():
    """获取（并缓存）Embedding 客户端。

    Returns:
        ``OpenAIEmbeddings`` 实例，向量维度由 ``EMBEDDING_DIM`` 指定。

    Raises:
        ImportError: 未安装 ``langchain-openai`` 时抛出。
    """
    global _embeddings
    if _embeddings is None:
        from langchain_openai import OpenAIEmbeddings

        _embeddings = OpenAIEmbeddings(
            base_url=settings.EMBEDDING_BASE_URL,
            api_key=settings.EMBEDDING_API_KEY,
            model=settings.EMBEDDING_MODEL,
            dimensions=settings.EMBEDDING_DIM,
            # 关闭客户端侧的 token 长度预检：部分 OpenAI 兼容服务商不返回模型上下文长度，
            # 开启校验会直接报错；真实超长由服务端返回错误处理。
            check_embedding_ctx_length=False,
        )
    return _embeddings


async def stream_chat(messages: List[AnyMessage]) -> AsyncIterator[str]:
    """流式调用对话模型，逐块产出新增文本。

    Args:
        messages: 完整的消息序列（System / Human / AI），由调用方负责裁剪历史窗口。

    Yields:
        str: 模型增量输出的文本片段；空片段（仅工具调用或思考标记）已被过滤。

    Raises:
        Exception: 透传底层 SDK 异常（超时、鉴权失败、限流），由调用方决定重试或降级。
    """
    async for chunk in get_client().astream(messages):
        text = chunk.text
        if text:
            yield text


async def ainvoke_text(system_prompt: str, user_prompt: str,
                       temperature: Optional[float] = None,
                       max_tokens: Optional[int] = None) -> str:
    """一次性取回完整文本（非流式），用于摘要、改写等中间步骤。

    Args:
        system_prompt: 系统提示词。
        user_prompt: 用户侧输入。
        temperature: 临时覆盖全局温度；None 表示沿用 ``settings.LLM_TEMPERATURE``。
        max_tokens: 临时覆盖全局长度上限；None 表示沿用 ``settings.LLM_MAX_TOKENS``。

    Returns:
        模型输出的纯文本。

    Raises:
        Exception: 透传底层 SDK 异常。
    """
    llm = get_client()
    # 仅在需要覆盖时才 bind，避免无谓地克隆一份 Runnable 配置。
    if temperature is not None or max_tokens is not None:
        llm = llm.bind(temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
                       max_tokens=max_tokens or settings.LLM_MAX_TOKENS)
    resp = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])
    return resp.text


# 结构化输出的三种实现方式，按「表达力从强到弱」排列：
# json_schema 由服务端强制约束；function_calling 借工具调用协议；json_mode 只保证返回 JSON。
# 不同 OpenAI 兼容服务商支持程度不一，逐级降级是兼容性兜底。
_STRUCTURED_METHODS = ("json_schema", "function_calling", "json_mode")
# 记住上一次成功的方式，后续优先复用：既能省掉一轮失败尝试，也避免同一进程内行为漂移。
_structured_method: Optional[str] = None


def _json_mode_hint(schema: Type[BaseModel]) -> str:
    """把 Pydantic schema 拼成提示词片段，供不支持原生 schema 约束的服务商使用。

    Args:
        schema: 目标输出模型。

    Returns:
        追加到 system prompt 末尾的约束文本（含完整 JSON Schema）。

    Raises:
        无。
    """
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return ("\n\n必须严格按以下 JSON Schema 输出一个 JSON 对象，只输出 JSON，不要任何额外文字：\n"
            + schema_json)


async def astructured_invoke(schema: Type[BaseModel], system_prompt: str,
                             user_prompt: str) -> BaseModel:
    """让模型按给定 schema 输出结构化结果，失败时自动换一种实现方式重试。

    Args:
        schema: 目标 Pydantic 模型（其 ``Field(description=...)`` 会随 schema 一起下发，
            因此字段描述写得好不好直接决定抽取质量）。
        system_prompt: 系统提示词。
        user_prompt: 用户侧输入。

    Returns:
        已校验的 ``schema`` 实例。

    Raises:
        Exception: 三种方式全部失败时，抛出最后一次遇到的异常（保留原始错误便于定位）。
        RuntimeError: 各方式都未报错但也没返回结果（模型返回空）。
    """
    global _structured_method
    llm = get_client()
    # 上次成功的方式排到最前；其余保持原顺序补齐，保证总能尝试全部方式。
    order = ([_structured_method] if _structured_method else []) + \
            [m for m in _STRUCTURED_METHODS if m != _structured_method]

    last_error: Optional[Exception] = None
    for method in order:
        try:
            if method == "json_mode":
                # json_mode 不做 schema 校验，只能把 schema 塞进提示词里「请求」模型遵守。
                client = llm.with_structured_output(schema, method="json_mode")
                return await client.ainvoke([
                    SystemMessage(content=system_prompt + _json_mode_hint(schema)),
                    HumanMessage(content=user_prompt),
                ])
            client = llm.with_structured_output(schema, method=method)
            result = await client.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
            # 个别服务商在 schema 模式下会返回 None 而不抛错，这里当作失败继续降级。
            if result is not None:
                _structured_method = method
                return result
        except Exception as exc:
            last_error = exc
            # 已缓存的方式失效（如服务商临时不支持）就清掉，避免后续每次都先失败一轮。
            if _structured_method == method:
                _structured_method = None
            logger.debug("结构化输出方式 %s 不可用：%s", method, str(exc)[:120])
    if last_error is not None:
        raise last_error
    raise RuntimeError("结构化输出失败：模型未返回有效结果")


# 模拟回复语料：覆盖健康、喂养、行为、就医四类常见问法，让前端在无 Key 环境下
# 也能看到接近真实的流式效果与排版。
_MOCK_REPLIES = [
    "收到你的问题。当前是本地模拟回复模式：关于{name}的健康与习性咨询，建议在 .env 中配置 LLM_API_KEY 后获取真实解答。",
    "这是模拟回复。一般来说，宠物出现食欲下降、精神萎靡等异常时，建议先记录症状持续时间，必要时尽快就医排查。",
    "模拟回复：关于{name}的喂养问题，建议按年龄阶段选择对应粮，定时定量，并保证充足饮水。",
    "当前为本地模拟模式，未接入大模型。请配置 LLM_API_KEY 后重试，即可获得针对性的养宠建议。",
    "模拟回复：宠物行为习惯的养成需要耐心，建议采用正向引导（奖励为主），避免惩罚式训练。",
    "这是模拟回复。若{name}出现呕吐、腹泻等症状，可先禁食观察半天，症状加重请及时咨询宠物医生。",
]


async def mock_stream(message: str, pet_name: str = "") -> AsyncIterator[str]:
    """产出模拟回复流，行为与真实流式接口保持一致。

    Args:
        message: 用户输入，用于按长度稳定地挑选一条回复（同一问题重复提问结果一致）。
        pet_name: 宠物名，用于替换回复里的 ``{name}`` 占位。

    Yields:
        str: 长度为 2~4 个字符的文本片段，模拟逐字输出。

    Raises:
        asyncio.CancelledError: 客户端断开时由调用方取消，属正常收尾路径。
    """
    name = (pet_name or "").strip() or "你的宠物"
    reply = _MOCK_REPLIES[len(message.strip()) % len(_MOCK_REPLIES)].format(name=name)

    # 片段长度在 2~4 之间轮换并夹带 sleep，模拟网络抖动；固定长度会让前端看起来像假数据。
    pos = 0
    while pos < len(reply):
        size = 2 + ((pos // 2) % 3)
        yield reply[pos:pos + size]
        pos += size
        await asyncio.sleep(0.05)
