"""LLM / Embedding 客户端封装。

把 LangChain 的模型客户端收敛为「按需创建 + 进程内复用」的几个惰性单例，并在其上提供
三类调用方式——流式对话、纯文本调用、结构化（Pydantic）输出；同时内置结构化输出的
多方式自动降级与无 Key 环境下的模拟流。

对话 / 视觉客户端不再直接读 ``settings.LLM_*``：管理员可以在「AI 扩展 → 模型」页配置并
热切换模型（含 OpenAI 兼容与 Anthropic 两种协议），具体解析与缓存由
``app.model_registry`` 负责，本模块只做调用面。
"""

import asyncio
import json
import logging
from typing import AsyncIterator, List, Optional, Type

from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from app import model_registry
from app.config import settings

logger = logging.getLogger(__name__)

# Embedding 仍是「首次使用时创建」的进程级单例：它不在模型切换范围内（向量维度与已有
# 知识库绑定，换模型需要重建索引），因此继续读 ``.env``。
_embeddings = None


async def get_client():
    """获取对话模型客户端（按当前生效模型解析，配置变化时自动重建）。

    Returns:
        ``langchain_openai.ChatOpenAI`` 或 ``langchain_anthropic.ChatAnthropic``。
        ``streaming=True`` 是硬性要求——对话链路以 SSE 逐字下发，走非流式接口会导致
        首字延迟等于整段生成时间。

    Raises:
        ImportError: 对应协议的 SDK 未安装时抛出。
    """
    return await model_registry.chat_client()


async def get_vision_client():
    """获取视觉模型客户端。

    当前模型自带视觉能力时与对话模型是同一个客户端；否则使用 ``.env`` 里单独配置的
    视觉模型（老部署兼容）。

    Returns:
        LangChain 聊天模型实例。

    Raises:
        ImportError: 未安装对应 SDK 时抛出。
        RuntimeError: 既没有视觉模型也没有兜底配置时抛出。
    """
    return await model_registry.vision_client()


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
    async for chunk in (await get_client()).astream(messages):
        text = chunk.text
        if text:
            yield text


async def ainvoke_text(system_prompt: str, user_prompt: str,
                       temperature: Optional[float] = None,
                       max_tokens: Optional[int] = None) -> str:
    """一次性取回完整文本（非流式），用于摘要、改写等中间步骤。

    Args:
        temperature: 临时覆盖全局温度；None 表示沿用 ``settings.LLM_TEMPERATURE``。
        max_tokens: 临时覆盖全局长度上限；None 表示沿用 ``settings.LLM_MAX_TOKENS``。

    Returns:
        模型输出的纯文本。

    Raises:
        Exception: 透传底层 SDK 异常。
    """
    llm = await get_client()
    # 仅在需要覆盖时才 bind，避免无谓地克隆一份 Runnable 配置。
    if temperature is not None or max_tokens is not None:
        llm = llm.bind(temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
                       max_tokens=max_tokens or settings.LLM_MAX_TOKENS)
    resp = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])
    return resp.text


# 结构化输出的实现方式按协议分两套：
#   OpenAI 兼容：json_schema 由服务端强制约束 → function_calling 借工具调用协议 → json_mode 只保证 JSON
#   Anthropic  ：只支持 function_calling / json_schema（没有 json_mode），工具调用最稳，排前面
# 不同服务商支持程度不一，逐级降级是兼容性兜底。
_STRUCTURED_METHODS = {
    "openai": ("json_schema", "function_calling", "json_mode"),
    "anthropic": ("function_calling", "json_schema"),
}
# 记住上一次成功的方式（连同协议），后续优先复用：既能省掉一轮失败尝试，也避免切模型后误用旧方式。
_structured_method: Optional[tuple] = None


def _json_mode_hint(schema: Type[BaseModel]) -> str:
    """把 Pydantic schema 拼成提示词片段，供不支持原生 schema 约束的服务商使用。

    Returns:
        追加到 system prompt 末尾的约束文本（含完整 JSON Schema）。
    """
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    return ("\n\n必须严格按以下 JSON Schema 输出一个 JSON 对象，只输出 JSON，不要任何额外文字：\n"
            + schema_json)


def _structured_order() -> tuple:
    """按当前协议给出结构化输出的尝试顺序。

    Returns:
        ``(protocol, methods)``；protocol 用于记录「哪种方式在哪个协议下成功过」。
    """
    proto = model_registry.protocol()
    methods = _STRUCTURED_METHODS.get(proto, _STRUCTURED_METHODS["openai"])
    remembered = None
    if _structured_method and _structured_method[0] == proto:
        remembered = _structured_method[1]
    if remembered not in methods:
        remembered = None
    # 上次成功的方式排到最前；其余保持原顺序补齐，保证总能尝试全部方式。
    order = ([remembered] if remembered else []) + [m for m in methods if m != remembered]
    return proto, tuple(order)


async def astructured_invoke(schema: Type[BaseModel], system_prompt: str,
                             user_prompt: str) -> BaseModel:
    """让模型按给定 schema 输出结构化结果，失败时自动换一种实现方式重试。

    Args:
        schema: 目标 Pydantic 模型（其 ``Field(description=...)`` 会随 schema 一起下发，
            因此字段描述写得好不好直接决定抽取质量）。

    Returns:
        已校验的 ``schema`` 实例。

    Raises:
        Exception: 所有方式都失败时，抛出最后一次遇到的异常（保留原始错误便于定位）。
        RuntimeError: 各方式都未报错但也没返回结果（模型返回空）。
    """
    global _structured_method
    llm = await get_client()
    proto, order = _structured_order()

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
                _structured_method = (proto, method)
                return result
        except Exception as exc:
            last_error = exc
            # 已缓存的方式失效（如服务商临时不支持）就清掉，避免后续每次都先失败一轮。
            if _structured_method == (proto, method):
                _structured_method = None
            logger.debug("结构化输出方式 %s 不可用：%s", method, str(exc)[:120])
    if last_error is not None:
        raise last_error
    raise RuntimeError("结构化输出失败：模型未返回有效结果")


# 模拟回复语料：覆盖健康、喂养、行为、就医四类常见问法，让前端在无 Key 环境下
# 也能看到接近真实的流式效果与排版。
#
# 面向的是**终端用户**，不是管理员：这里绝不能出现「模拟模式 / 未配置 / API Key /
# 管理页 / 模型供应商」这类部署与运维信息——模型切换是管理端能力，普通用户不该知道，
# 也不该因为后台没配好而看到技术细节。拿不到模型时就给通用、保守的养护建议。
_MOCK_REPLIES = [
    "关于{name}的情况，建议先从精神、食欲和排泄三方面观察：如果只是偶发的轻微变化，"
    "可以先调整喂养方式并观察一两天；如果持续或加重，建议尽快联系宠物医生。",
    "宠物出现食欲下降或精神不振时，先记录症状持续了多久，有没有伴随呕吐、腹泻或发热，"
    "这些信息对医生判断很有帮助。期间保证饮水，不要自行喂人用药。",
    "关于{name}的喂养，建议按年龄阶段选择对应的主粮，定时定量，保证充足饮水；"
    "换粮时用一周左右逐步过渡，能减少肠胃不适。",
    "日常护理上，建议按周期做体内外驱虫和疫苗接种，保持环境清洁通风，"
    "并留意体重与毛发状态的变化，有异常及时就医。",
    "行为习惯的养成需要耐心，建议用及时奖励的正向引导代替呵斥；"
    "每次训练控制在五到十分钟，时间过长反而容易让宠物失去兴趣。",
    "如果{name}出现呕吐或腹泻，可以先短暂禁食观察并保证饮水；"
    "一旦出现精神差、便血或持续呕吐，请尽快带去宠物医院检查。",
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
