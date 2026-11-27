"""AI 扩展管理接口（管理员限定）：MCP 服务、Skill 技能与模型供应商的增删改查。

为后台「AI 扩展管理」页提供三类配置的维护能力——MCP 服务（远程工具来源，存 PostgreSQL）、
Skill（标准 ``skills/<name>/SKILL.md`` 技能包，存磁盘）、模型供应商（协议 / 地址 / Key /
模型清单，存 PostgreSQL）。写操作成功后立即失效运行时缓存
（``app.agent.extensions.invalidate`` / ``app.model_registry.invalidate``），下一轮对话即生效。
Skill 按技能名（hyphen-case）操作，运行时以「元数据目录常驻 + load_skill 按需取正文」的
渐进式披露装配；模型以「供应商 + 模型」两级组织，全局只有一个当前生效模型。

鉴权说明：与知识库管理同一套双通道——网关注入的 ``X-User-Role: ADMIN`` 为主，
``X-Kb-Admin`` + ``KB_ADMIN_TOKEN`` 为直连运维备选（见 app.admin_auth）。
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Header, Path
from fastapi.responses import JSONResponse
from psycopg.errors import UniqueViolation

from app import ext_store, model_registry, skill_store
from app.admin_auth import admin_denied
from app.agent import extensions
from app.config import settings
from app.schemas import (
    McpServerUpsert,
    ModelActivateRequest,
    ModelProviderUpsert,
    ModelTestRequest,
    SkillUpsert,
    fail,
    ok,
)
from app.skill_store import SkillError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/admin")

_ERROR_MSG = "AI 扩展管理服务暂时不可用，请稍后再试"

# stdio 参数与键值对的数量 / 长度上限：配置来自管理端表单，但仍要在边界处挡住异常体量。
_MAX_LIST_ITEMS = 32
_MAX_KV_ITEMS = 32
_MAX_KV_VALUE_LEN = 1000
# 单个供应商下的模型数量上限：够覆盖「一个服务商挂多档模型」，又不至于把管理页撑爆。
_MAX_MODELS = 20


def _validate_mcp(data: McpServerUpsert) -> Optional[str]:
    """校验 MCP 配置的字段组合约束。

    Args:
        data: 已通过 pydantic 基础校验的请求体。

    Returns:
        None 表示合法；否则返回可直接展示给管理员的错误说明。
    """
    if not data.name.strip():
        return "服务名称不能为空"
    if data.transport in ("streamable_http", "sse"):
        url = data.url.strip()
        if not url:
            return "HTTP / SSE 传输必须填写服务地址"
        if not url.startswith(("http://", "https://")):
            return "服务地址必须以 http:// 或 https:// 开头"
    elif data.transport == "stdio" and not data.command.strip():
        return "stdio 传输必须填写启动命令"
    if len(data.args) > _MAX_LIST_ITEMS:
        return f"命令参数最多 {_MAX_LIST_ITEMS} 个"
    for label, mapping in (("环境变量", data.env), ("请求头", data.headers)):
        if len(mapping) > _MAX_KV_ITEMS:
            return f"{label}最多 {_MAX_KV_ITEMS} 项"
        if any(len(str(v)) > _MAX_KV_VALUE_LEN for v in mapping.values()):
            return f"{label}单项值不能超过 {_MAX_KV_VALUE_LEN} 字"
    return None


def _mcp_payload(data: McpServerUpsert) -> dict:
    """请求体 → 存储层字典（名称去首尾空白，其余原样）。"""
    return {
        "name": data.name.strip(),
        "transport": data.transport,
        "url": data.url.strip(),
        "command": data.command.strip(),
        "args": data.args,
        "env": data.env,
        "headers": data.headers,
        "description": data.description.strip(),
        "enabled": data.enabled,
    }


def _skill_payload(data: SkillUpsert) -> dict:
    """请求体 → 存储层字典。"""
    return {
        "name": data.name.strip(),
        "description": data.description.strip(),
        "content": data.content.strip(),
        "enabled": data.enabled,
    }


# ---------------- MCP 服务 ----------------


@router.get("/mcp/servers")
async def list_mcp_servers(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """全部 MCP 服务配置（含停用项，按创建先后排序）。

    Returns:
        ``ok({items: [...]})``；非管理员返回 403。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        items = await ext_store.list_mcp_servers()
    except Exception:
        logger.warning("查询 MCP 服务列表失败", exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    return JSONResponse(status_code=200, content=ok({"items": items}))


@router.post("/mcp/servers")
async def create_mcp_server(
    data: McpServerUpsert,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """新增 MCP 服务。

    Args:
        data: 服务配置（传输协议 + 地址 / 命令 + 可选鉴权信息）。

    Returns:
        ``ok(创建后的配置)``；参数不合法 400，名称重复 409。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    err = _validate_mcp(data)
    if err:
        return JSONResponse(status_code=200, content=fail(400, err))
    try:
        item = await ext_store.create_mcp_server(_mcp_payload(data))
    except UniqueViolation:
        return JSONResponse(status_code=200, content=fail(409, f"服务名称「{data.name.strip()}」已存在"))
    except Exception:
        logger.warning("新增 MCP 服务失败: %s", data.name, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    extensions.invalidate()
    logger.info("MCP 服务已新增: %s（id=%s, 操作者 user_id=%s）", item["name"], item["id"], x_user_id)
    return JSONResponse(status_code=200, content=ok(item))


@router.put("/mcp/servers/{server_id}")
async def update_mcp_server(
    data: McpServerUpsert,
    server_id: int = Path(..., description="服务 ID"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """整体更新 MCP 服务配置（启停开关也走这里，提交完整配置）。

    Returns:
        ``ok(更新后的配置)``；不存在 404，名称重复 409。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    err = _validate_mcp(data)
    if err:
        return JSONResponse(status_code=200, content=fail(400, err))
    try:
        item = await ext_store.update_mcp_server(server_id, _mcp_payload(data))
    except UniqueViolation:
        return JSONResponse(status_code=200, content=fail(409, f"服务名称「{data.name.strip()}」已存在"))
    except Exception:
        logger.warning("更新 MCP 服务失败: id=%s", server_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if item is None:
        return JSONResponse(status_code=200, content=fail(404, "MCP 服务不存在或已删除"))
    extensions.invalidate()
    logger.info("MCP 服务已更新: %s（id=%s, enabled=%s, 操作者 user_id=%s）",
                item["name"], server_id, item["enabled"], x_user_id)
    return JSONResponse(status_code=200, content=ok(item))


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(
    server_id: int = Path(..., description="服务 ID"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """删除 MCP 服务（下一轮对话起模型不再看见其工具）。

    Returns:
        ``ok({"id": server_id})``；不存在 404。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        deleted = await ext_store.delete_mcp_server(server_id)
    except Exception:
        logger.warning("删除 MCP 服务失败: id=%s", server_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if not deleted:
        return JSONResponse(status_code=200, content=fail(404, "MCP 服务不存在或已删除"))
    extensions.invalidate()
    logger.info("MCP 服务已删除: id=%s（操作者 user_id=%s）", server_id, x_user_id)
    return JSONResponse(status_code=200, content=ok({"id": server_id}))


@router.post("/mcp/servers/{server_id}/test")
async def test_mcp_server(
    server_id: int = Path(..., description="服务 ID"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """测试 MCP 服务连通性：即时连接一次并列出其暴露的工具（停用中的服务也可测试）。

    Returns:
        ``ok({toolCount, tools: [{name, description}]})``；连接失败 400 并附原因。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        server = await ext_store.get_mcp_server(server_id)
    except Exception:
        logger.warning("测试前查询 MCP 服务失败: id=%s", server_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if server is None:
        return JSONResponse(status_code=200, content=fail(404, "MCP 服务不存在或已删除"))
    try:
        tools = await extensions.test_mcp_server(server)
    except ImportError:
        return JSONResponse(status_code=200, content=fail(
            400, "服务端未安装 MCP 依赖（langchain-mcp-adapters），请先安装并重启 ai-service"))
    except Exception as exc:
        # 连接失败的原因（地址错 / 鉴权失败 / 超时）对管理员排障是必要信息，截断后透传。
        reason = str(exc).strip() or type(exc).__name__
        logger.warning("MCP 连接测试失败: id=%s name=%s reason=%s", server_id, server["name"], reason)
        return JSONResponse(status_code=200, content=fail(400, f"连接失败：{reason[:200]}"))
    return JSONResponse(status_code=200, content=ok({"toolCount": len(tools), "tools": tools}))


# ---------------- Skill 技能（标准 SKILL.md 包，按技能名操作） ----------------


@router.get("/skills")
async def list_skills(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """全部技能包（含停用项与正文，按名称排序）。

    Returns:
        ``ok({items: [{name, description, enabled, content, bodyLines, resources, updateTime}]})``。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        items = await asyncio.to_thread(skill_store.list_skills)
    except Exception:
        logger.warning("查询 Skill 列表失败", exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    return JSONResponse(status_code=200, content=ok({"items": items}))


@router.post("/skills")
async def create_skill(
    data: SkillUpsert,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """新增技能包（写 ``skills/<name>/SKILL.md``）。

    Args:
        data: 技能配置（name / description / content / enabled）。

    Returns:
        ``ok(创建后的技能)``；格式非法或重名 400。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        item = await asyncio.to_thread(skill_store.create_skill, _skill_payload(data))
    except SkillError as exc:
        return JSONResponse(status_code=200, content=fail(400, str(exc)))
    except Exception:
        logger.warning("新增 Skill 失败: %s", data.name, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    extensions.invalidate()
    logger.info("Skill 已新增: %s（操作者 user_id=%s）", item["name"], x_user_id)
    return JSONResponse(status_code=200, content=ok(item))


@router.put("/skills/{name}")
async def update_skill(
    data: SkillUpsert,
    name: str = Path(..., description="技能名（hyphen-case）"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """整体更新技能包（启停开关也走这里；改名时迁移目录）。

    Returns:
        ``ok(更新后的技能)``；不存在 404，格式非法或新名占用 400。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        item = await asyncio.to_thread(skill_store.update_skill, name, _skill_payload(data))
    except SkillError as exc:
        return JSONResponse(status_code=200, content=fail(400, str(exc)))
    except Exception:
        logger.warning("更新 Skill 失败: %s", name, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if item is None:
        return JSONResponse(status_code=200, content=fail(404, "Skill 不存在或已删除"))
    extensions.invalidate()
    logger.info("Skill 已更新: %s（enabled=%s, 操作者 user_id=%s）",
                item["name"], item["enabled"], x_user_id)
    return JSONResponse(status_code=200, content=ok(item))


@router.delete("/skills/{name}")
async def delete_skill(
    name: str = Path(..., description="技能名（hyphen-case）"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """删除技能包（整目录，下一轮对话起不再出现在技能目录）。

    Returns:
        ``ok({"name": name})``；不存在 404。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        deleted = await asyncio.to_thread(skill_store.delete_skill, name)
    except SkillError as exc:
        return JSONResponse(status_code=200, content=fail(400, str(exc)))
    except Exception:
        logger.warning("删除 Skill 失败: %s", name, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if not deleted:
        return JSONResponse(status_code=200, content=fail(404, "Skill 不存在或已删除"))
    extensions.invalidate()
    logger.info("Skill 已删除: %s（操作者 user_id=%s）", name, x_user_id)
    return JSONResponse(status_code=200, content=ok({"name": name}))


# ---------------- 模型供应商（协议 + 地址 + Key + 模型清单，全局一个生效模型） ----------------


def _validate_provider(data: ModelProviderUpsert, existing: Optional[dict] = None) -> Optional[str]:
    """校验模型供应商配置。

    Args:
        data: 已通过 pydantic 基础校验的请求体。
        existing: 更新场景下的原配置。``apiKey`` 留空表示「沿用原 Key」，
            因此只有原配置也没有 Key 时才要求填写。

    Returns:
        None 表示合法；否则返回可直接展示给管理员的错误说明。
    """
    if not data.name.strip():
        return "供应商名称不能为空"
    base_url = (data.baseUrl or "").strip()
    if not base_url:
        return "请填写 Base URL"
    if not base_url.startswith(("http://", "https://")):
        return "Base URL 必须以 http:// 或 https:// 开头"
    has_key = bool((data.apiKey or "").strip()
                   or (existing and (existing.get("apiKey") or "").strip()))
    if not has_key:
        return "请填写 API Key（本地推理服务可填任意占位值）"
    if not data.models:
        return "请至少添加一个模型"
    if len(data.models) > _MAX_MODELS:
        return f"单个供应商最多添加 {_MAX_MODELS} 个模型"
    seen = set()
    for item in data.models:
        model_id = (item.id or "").strip()
        if not model_id:
            return "模型 ID 不能为空"
        if model_id in seen:
            return f"模型 ID「{model_id}」重复"
        seen.add(model_id)
    return None


def _provider_payload(data: ModelProviderUpsert, existing: Optional[dict] = None) -> dict:
    """请求体 → 存储层字典。

    Args:
        data: 请求体。
        existing: 更新场景下的原配置；``apiKey`` 留空时沿用它（管理页拿到的是脱敏值，
            不能把掩码当新 Key 写回）。

    Returns:
        可直接交给 ``ext_store`` 的完整配置。
    """
    protocol = data.protocol or model_registry.PROTOCOL_OPENAI
    api_key = (data.apiKey or "").strip()
    if not api_key and existing:
        api_key = existing.get("apiKey") or ""
    return {
        "name": data.name.strip(),
        "protocol": protocol,
        # 统一规整地址：Anthropic 去掉误粘的 /v1，OpenAI 去掉误粘的 /chat/completions。
        "baseUrl": model_registry.normalize_base_url(protocol, data.baseUrl),
        "apiKey": api_key,
        "models": [
            {
                "id": item.id.strip(),
                "label": (item.label or "").strip(),
                "vision": bool(item.vision),
                "maxTokens": int(item.maxTokens or 0),
            }
            for item in data.models
        ],
        "description": data.description.strip(),
        "enabled": data.enabled,
    }


def _provider_view(provider: dict) -> dict:
    """存储层字典 → 接口返回体（去掉 Key 明文，只留尾号掩码）。"""
    return {
        "id": provider["id"],
        "name": provider["name"],
        "protocol": provider.get("protocol") or model_registry.PROTOCOL_OPENAI,
        "baseUrl": provider.get("baseUrl") or "",
        "apiKeyMask": model_registry.mask_key(provider.get("apiKey") or ""),
        "hasApiKey": bool((provider.get("apiKey") or "").strip()),
        "models": provider.get("models") or [],
        "description": provider.get("description") or "",
        "enabled": bool(provider.get("enabled")),
        "createTime": provider.get("createTime") or 0,
        "updateTime": provider.get("updateTime") or 0,
    }


@router.get("/models")
async def list_model_providers(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """全部模型供应商 + 当前生效模型 + 运行时实际生效配置。

    Returns:
        ``ok({items, active: {providerId, model}, runtime: {source, providerName, model, ...}})``；
        ``runtime`` 是服务端真正在用的配置（可能因配置失效回落到 .env），用于管理页对照排障。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        items = await ext_store.list_model_providers()
        provider_id, model_id = await ext_store.get_active_model()
    except Exception:
        logger.warning("查询模型供应商列表失败", exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    runtime = await model_registry.current()
    return JSONResponse(status_code=200, content=ok({
        "items": [_provider_view(p) for p in items],
        "active": {"providerId": provider_id, "model": model_id},
        "runtime": runtime.describe(),
        # MOCK_CHAT 是硬开关，优先级高于任何模型配置；管理页要显式提示，否则会误以为
        # 「配了就生效」而实际仍在返回模拟回复。
        "mock": bool(settings.MOCK_CHAT),
    }))


@router.post("/models")
async def create_model_provider(
    data: ModelProviderUpsert,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """新增模型供应商（可一次带上多个模型）。

    Returns:
        ``ok(供应商视图)``；参数不合法 400，名称重复 409。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    err = _validate_provider(data)
    if err:
        return JSONResponse(status_code=200, content=fail(400, err))
    try:
        item = await ext_store.create_model_provider(_provider_payload(data))
    except UniqueViolation:
        return JSONResponse(status_code=200, content=fail(409, f"供应商名称「{data.name.strip()}」已存在"))
    except Exception:
        logger.warning("新增模型供应商失败: %s", data.name, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    model_registry.invalidate()
    logger.info("模型供应商已新增: %s（protocol=%s, 模型数=%s, 操作者 user_id=%s）",
                item["name"], item["protocol"], len(item.get("models") or []), x_user_id)
    return JSONResponse(status_code=200, content=ok(_provider_view(item)))


@router.put("/models/{provider_id}")
async def update_model_provider(
    data: ModelProviderUpsert,
    provider_id: int = Path(..., description="供应商 ID"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """整体更新模型供应商（启停开关也走这里，提交完整配置）。

    Returns:
        ``ok(供应商视图)``；不存在 404，名称重复 409。当前生效模型若被移出清单，
        会同时清空生效指针并提示。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        existing = await ext_store.get_model_provider(provider_id)
    except Exception:
        logger.warning("更新前查询模型供应商失败: id=%s", provider_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if existing is None:
        return JSONResponse(status_code=200, content=fail(404, "模型供应商不存在或已删除"))
    err = _validate_provider(data, existing)
    if err:
        return JSONResponse(status_code=200, content=fail(400, err))
    try:
        item = await ext_store.update_model_provider(provider_id, _provider_payload(data, existing))
    except UniqueViolation:
        return JSONResponse(status_code=200, content=fail(409, f"供应商名称「{data.name.strip()}」已存在"))
    except Exception:
        logger.warning("更新模型供应商失败: id=%s", provider_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))

    # 生效模型被改名 / 移出清单时清掉指针：否则运行时只能回落到 .env，
    # 管理页却仍显示一个已经不存在的中文名字，排障时会误导。
    try:
        active_provider, active_model = await ext_store.get_active_model()
        alive = {m.get("id") for m in (item.get("models") or []) if isinstance(m, dict)}
        if active_provider == provider_id and (active_model not in alive or not item.get("enabled")):
            await ext_store.clear_active_model()
            logger.info("当前生效模型已失效，指针已清空: provider_id=%s model=%s",
                        provider_id, active_model)
    except Exception:
        logger.warning("更新后校正生效指针失败（忽略）: id=%s", provider_id, exc_info=True)

    model_registry.invalidate()
    logger.info("模型供应商已更新: %s（id=%s, enabled=%s, 操作者 user_id=%s）",
                item["name"], provider_id, item["enabled"], x_user_id)
    return JSONResponse(status_code=200, content=ok(_provider_view(item)))


@router.delete("/models/{provider_id}")
async def delete_model_provider(
    provider_id: int = Path(..., description="供应商 ID"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """删除模型供应商（连带清空生效指针，运行时回落到 .env）。

    Returns:
        ``ok({"id": provider_id})``；不存在 404。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        deleted = await ext_store.delete_model_provider(provider_id)
    except Exception:
        logger.warning("删除模型供应商失败: id=%s", provider_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if not deleted:
        return JSONResponse(status_code=200, content=fail(404, "模型供应商不存在或已删除"))
    model_registry.invalidate()
    logger.info("模型供应商已删除: id=%s（操作者 user_id=%s）", provider_id, x_user_id)
    return JSONResponse(status_code=200, content=ok({"id": provider_id}))


@router.post("/models/activate")
async def activate_model(
    data: ModelActivateRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """切换当前生效模型（下一轮对话立即使用新模型，无需重启）。

    Returns:
        ``ok({active, runtime})``；供应商不存在 404，未启用或模型不在清单内 400。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        provider = await ext_store.get_model_provider(data.providerId)
    except Exception:
        logger.warning("切换模型前查询供应商失败: id=%s", data.providerId, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if provider is None:
        return JSONResponse(status_code=200, content=fail(404, "模型供应商不存在或已删除"))
    if not provider.get("enabled"):
        return JSONResponse(status_code=200, content=fail(400, "该供应商已停用，不能设为当前模型"))
    model_id = (data.model or "").strip()
    if model_id not in {m.get("id") for m in (provider.get("models") or []) if isinstance(m, dict)}:
        return JSONResponse(status_code=200, content=fail(400, "该模型不在供应商的模型清单内"))

    try:
        await ext_store.set_active_model(data.providerId, model_id)
    except Exception:
        logger.warning("切换模型失败: provider_id=%s model=%s", data.providerId, model_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))

    model_registry.invalidate()
    runtime = await model_registry.current()
    logger.info("当前模型已切换: provider=%s model=%s（运行时=%s/%s, 操作者 user_id=%s）",
                provider["name"], model_id, runtime.source, runtime.model, x_user_id)
    return JSONResponse(status_code=200, content=ok({
        "active": {"providerId": data.providerId, "model": model_id},
        "runtime": runtime.describe(),
    }))


@router.post("/models/test")
async def test_model_provider(
    data: ModelTestRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
    x_user_role: Optional[str] = Header(default=None, alias="X-User-Role"),
    x_kb_admin: Optional[str] = Header(default=None, alias="X-Kb-Admin"),
):
    """测试指定模型的连通性：发一次最小请求，验证地址 / Key / 模型 ID 三者都通。

    Returns:
        ``ok({model, protocol, latencyMs, reply})``；连接失败 400 并附中文原因。
    """
    denied = admin_denied(x_user_id, x_user_role, x_kb_admin, resource="AI 扩展")
    if denied is not None:
        return denied
    try:
        provider = await ext_store.get_model_provider(data.providerId)
    except Exception:
        logger.warning("测试模型前查询供应商失败: id=%s", data.providerId, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if provider is None:
        return JSONResponse(status_code=200, content=fail(404, "模型供应商不存在或已删除"))
    model_id = (data.model or "").strip() or next(
        (m.get("id") for m in (provider.get("models") or []) if isinstance(m, dict)), "")
    if model_id not in {m.get("id") for m in (provider.get("models") or []) if isinstance(m, dict)}:
        return JSONResponse(status_code=200, content=fail(400, "该模型不在供应商的模型清单内"))
    try:
        result = await model_registry.test_connection(provider, model_id)
    except ValueError as exc:
        return JSONResponse(status_code=200, content=fail(400, str(exc)))
    except ImportError as exc:
        return JSONResponse(status_code=200, content=fail(
            400, f"服务端缺少对应 SDK，无法测试该协议：{exc}"))
    except Exception as exc:
        reason = model_registry.friendly_error(exc)
        # 原始报文（多为英文）只进日志：管理页展示中文原因，排障时再回看日志里的原文。
        logger.warning("模型连通性测试失败: provider=%s model=%s reason=%s raw=%s",
                       provider["name"], model_id, reason, str(exc)[:300])
        return JSONResponse(status_code=200, content=fail(400, f"连接失败：{reason}"))
    logger.info("模型连通性测试通过: provider=%s model=%s 耗时=%sms",
                provider["name"], model_id, result["latencyMs"])
    return JSONResponse(status_code=200, content=ok(result))