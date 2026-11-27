"""对话模型的运行时注册表：把「当前用哪个模型」从硬编码配置变成可热切换的数据。

设计参照 ZCode / Zed 一类的成熟 Agent 客户端——「供应商（Provider）+ 模型（Model）」两级：
供应商描述协议（OpenAI ``/chat/completions`` 或 Anthropic ``/v1/messages``）、Base URL 与
API Key，其下挂多个模型 ID；全局只有一个「当前生效模型」指针（``ai_model_setting``）。
管理员在「AI 扩展 → 模型」页切换后立即失效缓存，下一轮对话即生效，无需重启服务。

解析顺序：数据库里已启用且配置完整的生效模型 → ``.env`` 的 ``LLM_*``。后者的存在保证
「没配过模型」和「PostgreSQL 暂时不可用」两种情况下对话都能继续跑，不会因为管理端配置
缺失而整体不可用。

对外只暴露两件事：``current()`` 给出本轮该用哪套配置，``chat_client()`` / ``vision_client()``
给出可直接调用的 LangChain 客户端（按配置签名缓存，配置一变就重建）。
"""

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from typing import Any, Dict, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

# 数据库配置的进程内缓存 TTL（秒）：管理接口写入会显式 invalidate，
# TTL 只用于兜底多 worker 部署下其他进程的最终一致。
_CACHE_TTL = 60.0

# 供应商协议：openai = OpenAI 兼容 /chat/completions；anthropic = Anthropic Messages。
PROTOCOL_OPENAI = "openai"
PROTOCOL_ANTHROPIC = "anthropic"
PROTOCOLS = (PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC)

# 单次连通性测试的生成上限与超时：只验证「打得通 + 认这个模型」，不需要完整回答。
_TEST_MAX_TOKENS = 32
_TEST_TIMEOUT = 45.0
_TEST_PROMPT = "只回复两个字：收到"

_cache: Optional[Tuple[float, "RuntimeModel"]] = None
_load_lock = asyncio.Lock()
# 客户端缓存：(配置签名, 客户端)。签名变化即重建，切模型时不需要显式清理。
_chat_client: Optional[Tuple[tuple, Any]] = None
_vision_client: Optional[Tuple[tuple, Any]] = None


@dataclass(frozen=True)
class RuntimeModel:
    """一轮对话实际使用的一套模型配置。"""

    source: str            # db：管理端配置；env：.env 回落
    provider_id: int
    provider_name: str
    protocol: str          # openai / anthropic
    base_url: str
    api_key: str
    model: str
    label: str
    max_tokens: int
    vision: bool
    note: str = ""         # 回落原因等提示，写日志与展示在管理页

    def signature(self, streaming: bool, max_tokens: Optional[int] = None,
                  temperature: Optional[float] = None) -> tuple:
        """客户端缓存的比较键：任一构造参数变化都必须重建客户端。"""
        return (self.protocol, self.base_url, self.api_key, self.model,
                max_tokens if max_tokens else self.max_tokens,
                settings.LLM_TEMPERATURE if temperature is None else temperature,
                streaming)

    def describe(self) -> Dict[str, Any]:
        """对外（管理页 / 日志）的安全视图：不含 Key 明文。"""
        return {
            "source": self.source,
            "providerId": self.provider_id,
            "providerName": self.provider_name,
            "protocol": self.protocol,
            "baseUrl": self.base_url,
            "model": self.model,
            "label": self.label,
            "maxTokens": self.max_tokens,
            "vision": self.vision,
            "note": self.note,
        }


def normalize_base_url(protocol: str, url: str) -> str:
    """把管理员填的地址规整成 SDK 需要的形态。

    两家 SDK 的语义不同，粘贴错的后果是一个 404 而不是明确的报错，所以在这里统一纠正：

    - OpenAI 兼容：``base_url`` 会被拼上 ``/chat/completions``，所以要保留 ``/v1`` 这类前缀，
      但要剥掉误粘的完整路径 ``/chat/completions``；
    - Anthropic：SDK 自己拼 ``/v1/messages``，``base_url`` 必须以 ``/v1`` 之前的部分收尾，
      因此剥掉结尾的 ``/v1``（官方与多数网关都是「不带 /v1」的写法）。

    Args:
        protocol: ``openai`` / ``anthropic``。
        url: 管理员填写的原始地址。

    Returns:
        规整后的地址；输入为空时返回空串。
    """
    base = (url or "").strip().rstrip("/")
    if not base:
        return ""
    lowered = base.lower()
    for suffix in ("/chat/completions", "/v1/messages"):
        if lowered.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            lowered = base.lower()
            break
    if protocol == PROTOCOL_ANTHROPIC and lowered.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def mask_key(key: str) -> str:
    """API Key 脱敏（管理页只展示尾号，避免凭证回显到浏览器与日志）。

    Returns:
        形如 ``sk-…a8a9`` 的掩码；未配置时返回空串。
    """
    raw = (key or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return raw[0] + "***"
    return f"{raw[:3]}…{raw[-4:]}"


def _env_runtime() -> RuntimeModel:
    """``.env`` 回落配置（协议固定 OpenAI 兼容，与改造前一致）。"""
    return RuntimeModel(
        source="env",
        provider_id=0,
        provider_name="环境变量（.env）",
        protocol=PROTOCOL_OPENAI,
        base_url=(settings.LLM_BASE_URL or "").strip(),
        api_key=(settings.LLM_API_KEY or "").strip(),
        model=(settings.LLM_MODEL or "").strip(),
        label=(settings.LLM_MODEL or "").strip(),
        max_tokens=int(settings.LLM_MAX_TOKENS),
        vision=bool(settings.LLM_VISION_MODEL.strip()),
    )


def _pick_model(provider: dict, model_id: str) -> Optional[dict]:
    """在供应商的模型清单里按 ID 找一项。

    Returns:
        模型字典（id / label / vision / maxTokens）；找不到时返回 None。
    """
    for item in provider.get("models") or []:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == model_id:
            return item
    return None


async def _resolve() -> RuntimeModel:
    """读数据库解析当前模型；任何一步不成立都回落到 ``.env``。

    Returns:
        生效配置。数据库不可用时 ``note`` 记录回落原因，便于排障。
    """
    from app import ext_store

    if not ext_store.available():
        return replace(_env_runtime(), note="PostgreSQL 未就绪，使用 .env 配置")
    try:
        provider_id, model_id = await ext_store.get_active_model()
        if not provider_id or not model_id:
            return _env_runtime()
        provider = await ext_store.get_model_provider(provider_id)
    except Exception:
        logger.warning("读取模型配置失败，本轮回落到 .env", exc_info=True)
        return replace(_env_runtime(), note="模型配置读取失败，已回落到 .env")

    if provider is None:
        return replace(_env_runtime(), note="当前生效模型指向的供应商已删除，已回落到 .env")
    if not provider.get("enabled"):
        return replace(_env_runtime(), note=f"供应商「{provider['name']}」已停用，已回落到 .env")
    item = _pick_model(provider, model_id)
    if item is None:
        return replace(_env_runtime(), note="当前生效的模型已被移除，已回落到 .env")

    protocol = provider.get("protocol") or PROTOCOL_OPENAI
    base_url = normalize_base_url(protocol, provider.get("baseUrl") or "")
    api_key = (provider.get("apiKey") or "").strip()
    if not base_url or not api_key:
        return replace(_env_runtime(), note=f"供应商「{provider['name']}」缺少地址或 Key，已回落到 .env")

    max_tokens = int(item.get("maxTokens") or 0) or int(settings.LLM_MAX_TOKENS)
    return RuntimeModel(
        source="db",
        provider_id=int(provider["id"]),
        provider_name=provider["name"],
        protocol=protocol,
        base_url=base_url,
        api_key=api_key,
        model=model_id,
        label=(item.get("label") or "").strip() or model_id,
        max_tokens=max_tokens,
        vision=bool(item.get("vision")),
    )


async def current() -> RuntimeModel:
    """取当前生效模型（带 TTL 缓存与并发合并）。

    Returns:
        ``RuntimeModel``；首次调用会读一次数据库。
    """
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_TTL:
        return _cache[1]
    async with _load_lock:
        now = time.monotonic()
        if _cache is not None and now - _cache[0] < _CACHE_TTL:
            return _cache[1]
        resolved = await _resolve()
        _cache = (time.monotonic(), resolved)
        return resolved


async def prime() -> RuntimeModel:
    """启动时预热缓存并打印生效配置（让「到底在用哪个模型」在日志里一眼可见）。"""
    invalidate()
    cfg = await current()
    logger.info("对话模型已就绪: source=%s provider=%s protocol=%s model=%s vision=%s%s",
                cfg.source, cfg.provider_name, cfg.protocol, cfg.model, cfg.vision,
                f"（{cfg.note}）" if cfg.note else "")
    return cfg


def invalidate() -> None:
    """让配置缓存立即过期（管理端切换 / 增删改后调用），下一轮对话重新解析。

    注意是「过期」而不是「清空」：同步读缓存的地方（``settings.is_mock``、``protocol``）
    在异步刷新完成前仍能拿到上一次可用的配置，不会瞬间退回 mock 模式。
    """
    global _cache
    if _cache is not None:
        _cache = (0.0, _cache[1])


def _cached() -> Optional[RuntimeModel]:
    """同步读缓存（不触发数据库访问），供 ``settings`` 的属性判断使用。"""
    return _cache[1] if _cache else None


def is_configured() -> bool:
    """是否具备可用的模型凭证（用于替代「Key 为空即 mock」的静态判断）。

    Returns:
        True 表示能调真实模型：缓存已解析出配置时以它为准，否则看 ``.env``。
    """
    cfg = _cached()
    if cfg is not None:
        return bool(cfg.api_key.strip() and cfg.model.strip())
    return bool(settings.LLM_API_KEY.strip() and settings.LLM_MODEL.strip())


def protocol() -> str:
    """当前协议（结构化输出等分支按它选择实现方式）。

    Returns:
        ``openai`` / ``anthropic``；未解析前按 OpenAI 兼容处理。
    """
    cfg = _cached()
    return cfg.protocol if cfg else PROTOCOL_OPENAI


def _env_vision_configured() -> bool:
    """``.env`` 里是否单独配了视觉模型（三项齐全）。"""
    return bool(settings.LLM_VISION_MODEL.strip()
                and settings.LLM_VISION_API_KEY.strip()
                and settings.LLM_VISION_BASE_URL.strip())


async def vision_ready() -> bool:
    """本轮是否具备看图能力。

    判定只看「这一轮实际用哪套配置」，不做跨配置借用：

    - 管理端选中的模型：以模型行上的「支持图片」勾选为准，没勾就退化为文字占位；
    - 兜底到 ``.env`` 时：用 ``LLM_VISION_*``（改造前的老配置，保持兼容）。

    这样切换模型后的行为是可预期的——不会出现「对话换成 A、看图却偷偷用了 B」。

    Returns:
        True 表示图片可以真正送进模型。
    """
    if settings.MOCK_CHAT:
        return False
    cfg = await current()
    if cfg.source == "db":
        return bool(cfg.vision and cfg.api_key and cfg.model)
    return _env_vision_configured()


def vision_ready_cached() -> bool:
    """``vision_ready`` 的同步版本（仅供 ``settings`` 属性使用，不访问数据库）。"""
    if settings.MOCK_CHAT:
        return False
    cfg = _cached()
    if cfg is not None and cfg.source == "db":
        return bool(cfg.vision and cfg.api_key and cfg.model)
    return _env_vision_configured()


def _build_client(cfg: RuntimeModel, *, max_tokens: Optional[int] = None,
                  streaming: bool = True, timeout: float = 60.0, max_retries: int = 1):
    """按协议构造 LangChain 客户端。

    Args:
        cfg: 生效配置。
        max_tokens: 覆盖长度上限；None 表示用配置里的值。
        streaming: 对话链路必须为 True（SSE 逐字下发），连通性测试用 False。
        timeout: 单次请求超时（秒）。
        max_retries: SDK 层重试次数；测试链路用 0，避免超时被翻倍。

    Returns:
        ``ChatOpenAI`` 或 ``ChatAnthropic`` 实例。

    Raises:
        ImportError: 对应 SDK 未安装（Anthropic 需 ``langchain-anthropic``）。
        ValueError: 协议未知（脏数据）。
    """
    limit = max_tokens or cfg.max_tokens or settings.LLM_MAX_TOKENS
    # 部分本地推理服务（Ollama / vLLM）不校验 Key，但 OpenAI SDK 要求非空串。
    api_key = cfg.api_key or "sk-noauth"

    if cfg.protocol == PROTOCOL_ANTHROPIC:
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=cfg.model,
            api_key=api_key,
            base_url=cfg.base_url or "https://api.anthropic.com",
            max_tokens=limit,
            temperature=settings.LLM_TEMPERATURE,
            timeout=timeout,
            max_retries=max_retries,
            streaming=streaming,
        )
    if cfg.protocol != PROTOCOL_OPENAI:
        raise ValueError(f"未知模型协议: {cfg.protocol}")

    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=cfg.base_url or None,
        api_key=api_key,
        model=cfg.model,
        streaming=streaming,
        max_tokens=limit,
        temperature=settings.LLM_TEMPERATURE,
        # 对话场景宁可快速失败走首帧重试，也不要在服务端长时间挂住占一个并发名额。
        timeout=timeout,
        max_retries=max_retries,
    )


async def chat_client():
    """取（并缓存）对话客户端；配置变化时自动重建。

    Returns:
        LangChain 聊天模型实例（``streaming=True``）。
    """
    global _chat_client
    cfg = await current()
    sig = cfg.signature(streaming=True)
    if _chat_client is not None and _chat_client[0] == sig:
        return _chat_client[1]
    client = _build_client(cfg, streaming=True, timeout=60.0)
    _chat_client = (sig, client)
    return client


async def vision_client():
    """取（并缓存）看图用的客户端。

    Returns:
        管理端模型支持图片时返回该模型的对话客户端；兜底到 ``.env`` 时返回
        ``LLM_VISION_*`` 对应的客户端。

    Raises:
        RuntimeError: 当前模型不支持图片，且 ``.env`` 也没有可用的视觉模型。
    """
    global _vision_client
    cfg = await current()
    if cfg.source == "db":
        if not (cfg.vision and cfg.api_key and cfg.model):
            raise RuntimeError("当前模型未勾选「支持图片」，无法处理图片输入")
        return await chat_client()

    # 老配置回落：视觉模型与对话模型不同源，构造时不复用对话配置。
    if not _env_vision_configured():
        raise RuntimeError("未配置可用的视觉模型")
    env_cfg = RuntimeModel(
        source="env", provider_id=0, provider_name="环境变量（.env）",
        protocol=PROTOCOL_OPENAI,
        base_url=normalize_base_url(PROTOCOL_OPENAI, settings.LLM_VISION_BASE_URL),
        api_key=settings.LLM_VISION_API_KEY.strip(),
        model=settings.LLM_VISION_MODEL.strip(),
        label=settings.LLM_VISION_MODEL.strip(),
        max_tokens=int(settings.LLM_MAX_TOKENS),
        vision=True,
    )
    sig = env_cfg.signature(streaming=True)
    if _vision_client is not None and _vision_client[0] == sig:
        return _vision_client[1]
    # 图片上传与视觉推理明显慢于纯文本，超时放宽到 90s。
    client = _build_client(env_cfg, streaming=True, timeout=90.0)
    _vision_client = (sig, client)
    return client


def friendly_error(exc: Exception) -> str:
    """把 SDK 异常翻译成管理员能据此动手的中文提示。

    Args:
        exc: 底层 SDK / 网络异常。

    Returns:
        一句话原因（截断到 200 字），可直接展示在管理页。
    """
    status = getattr(exc, "status_code", None)
    text = str(exc).strip()
    low = text.lower()

    def has(*words: str) -> bool:
        return any(w in low for w in words)

    if status == 401 or has("401", "authentication", "invalid api key", "invalid x-api-key",
                            "unauthorized"):
        return "鉴权失败（401）：API Key 无效，或与所选协议的认证方式不匹配"
    if status == 403 or has("403", "permission", "forbidden"):
        return "无权访问（403）：该 Key 没有此模型权限，或被网关策略拦截"
    if status == 404 or has("404", "not found", "model_not_found"):
        return "地址或模型不存在（404）：检查 Base URL 是否需要 /v1，以及模型 ID 是否正确"
    # 不少服务商对「模型名不存在」返回 400 而不是 404，只能按报文里的关键词识别。
    if has("model_not_found", "invalid model", "no such model", "not exist",
           "supported api model", "unknown model"):
        return "模型 ID 不存在或不支持：请核对服务商文档里的模型名"
    if status == 429 or has("429", "rate limit", "quota", "insufficient"):
        return "限流或额度不足（429）：稍后重试，或检查账户余额"
    if has("timeout", "timed out", "read timed out"):
        return "请求超时：网络不可达或服务端响应过慢"
    if has("connection", "connect", "ssl", "proxy", "getaddrinfo"):
        return "连接失败：Base URL 不可达（域名 / 端口 / 网络 / 代理）"
    return (text or type(exc).__name__)[:200]


async def test_connection(provider: dict, model_id: str) -> Dict[str, Any]:
    """对指定供应商的某个模型发一次最小请求，验证「地址 + Key + 模型」三者都通。

    Args:
        provider: 供应商配置（可含尚未入库的表单值）。
        model_id: 要测试的模型 ID。

    Returns:
        ``{model, protocol, latencyMs, reply}``。

    Raises:
        ValueError: 供应商配置不完整（缺地址 / Key / 模型）。
        Exception: 底层调用失败（由路由层用 ``friendly_error`` 转成中文提示）。
    """
    protocol_name = provider.get("protocol") or PROTOCOL_OPENAI
    base_url = normalize_base_url(protocol_name, provider.get("baseUrl") or "")
    api_key = (provider.get("apiKey") or "").strip()
    model = (model_id or "").strip()
    if not base_url:
        raise ValueError("请先填写 Base URL")
    if not model:
        raise ValueError("请先选择要测试的模型")
    if not api_key:
        raise ValueError("请先填写 API Key")

    cfg = RuntimeModel(
        source="db", provider_id=int(provider.get("id") or 0),
        provider_name=provider.get("name") or "未命名", protocol=protocol_name,
        base_url=base_url, api_key=api_key, model=model, label=model,
        max_tokens=_TEST_MAX_TOKENS, vision=False,
    )
    client = _build_client(cfg, max_tokens=_TEST_MAX_TOKENS, streaming=False,
                           timeout=_TEST_TIMEOUT, max_retries=0)

    from langchain_core.messages import HumanMessage

    started = time.perf_counter()
    resp = await client.ainvoke([HumanMessage(content=_TEST_PROMPT)])
    latency_ms = int((time.perf_counter() - started) * 1000)
    reply = (getattr(resp, "text", "") or "").strip()
    return {"model": model, "protocol": protocol_name, "latencyMs": latency_ms,
            "reply": reply[:200]}
