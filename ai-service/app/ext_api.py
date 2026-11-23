"""AI 扩展管理接口（管理员限定）：MCP 服务与 Skill 技能的增删改查与连接测试。

为后台「AI 扩展管理」页提供配置维护能力——MCP 服务（远程工具来源，存 PostgreSQL）
与 Skill（标准 ``skills/<name>/SKILL.md`` 技能包，存磁盘）的列表 / 新增 / 更新 / 删除 / 启停，
以及 MCP 连接测试。写操作成功后立即失效运行时缓存（``app.agent.extensions.invalidate``），
下一轮对话即生效。Skill 按技能名（hyphen-case）操作，运行时以「元数据目录常驻 + load_skill
按需取正文」的渐进式披露装配。

鉴权说明：与知识库管理同一套双通道——网关注入的 ``X-User-Role: ADMIN`` 为主，
``X-Kb-Admin`` + ``KB_ADMIN_TOKEN`` 为直连运维备选（见 app.admin_auth）。
"""

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Header, Path
from fastapi.responses import JSONResponse
from psycopg.errors import UniqueViolation

from app import ext_store, skill_store
from app.admin_auth import admin_denied
from app.agent import extensions
from app.schemas import McpServerUpsert, SkillUpsert, fail, ok
from app.skill_store import SkillError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai/admin")

_ERROR_MSG = "AI 扩展管理服务暂时不可用，请稍后再试"

# stdio 参数与键值对的数量 / 长度上限：配置来自管理端表单，但仍要在边界处挡住异常体量。
_MAX_LIST_ITEMS = 32
_MAX_KV_ITEMS = 32
_MAX_KV_VALUE_LEN = 1000


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