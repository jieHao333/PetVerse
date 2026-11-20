"""管理员配置的扩展能力装配（MCP 工具 + Skill 技能）。

职责：把后台「AI 扩展管理」里启用的 MCP 服务与 Skill 技能装配进对话 Agent——
MCP 服务的远程工具经 ``langchain-mcp-adapters`` 转成 LangChain 工具并入工具列表；
Skill 采用标准 Agent Skills 的**渐进式披露**：提示词只常驻技能元数据目录
（name + description），模型判断相关时经 ``load_skill`` 工具按需取回正文。
MCP 配置存 PostgreSQL（``app.ext_store``），Skill 是磁盘标准技能包（``app.skill_store``，
``skills/<name>/SKILL.md``）；本模块做进程内缓存，管理接口写入后调 ``invalidate()``
显式失效，多 worker 部署靠 TTL 兜底刷新。

主要导出：
    - ``get_mcp_tools``     启用中 MCP 服务的远程工具（带缓存，失败降级为空）
    - ``get_skills_prompt`` 启用中技能的元数据目录（带缓存，失败降级为空串）
    - ``test_mcp_server``   管理页「测试连接」用：即时连一次并返回工具清单
    - ``invalidate``        配置变更后清空缓存

外部依赖：``langchain-mcp-adapters``（可选，未安装时 MCP 能力整体降级）、
``app.ext_store`` / ``app.skill_store``、``app.config.settings``。

使用示例：
    tools = build_tools(ctx) + await get_mcp_tools()   # 含 load_skill 工具
    prompt = build_agent_prompt(pet, skills_context=await get_skills_prompt())

降级原则：扩展能力是增强项——依赖未安装、存储不可用、MCP 服务不可达或加载超时，
都只让扩展能力缺席，内置工具与对话主链路不受影响。
"""

import asyncio
import logging
import time
from typing import List, Optional, Tuple

from langchain_core.tools import BaseTool

from app import ext_store, skill_store
from app.config import settings

logger = logging.getLogger(__name__)

# 技能元数据目录的总字数上限：技能很多时避免目录本身挤占上下文（正文走 load_skill 按需加载）。
_SKILLS_CATALOG_MAX_CHARS = 4000

# (装载时间戳, 结果)；None 表示冷缓存。失败结果同样缓存，避免每个请求都去撞不可达的服务。
_mcp_cache: Optional[Tuple[float, List[BaseTool]]] = None
_skills_cache: Optional[Tuple[float, str]] = None
# 冷缓存并发首载时只放一个请求去连 MCP / 读库，其余等结果，避免连接风暴。
_mcp_lock = asyncio.Lock()
_skills_lock = asyncio.Lock()


def invalidate() -> None:
    """清空扩展能力缓存（管理接口增删改 / 启停后调用，下一轮对话即生效）。

    Returns:
        None。

    Raises:
        无。
    """
    global _mcp_cache, _skills_cache
    _mcp_cache = None
    _skills_cache = None


def _connection_for(server: dict) -> dict:
    """把数据库里的服务配置转成 langchain-mcp-adapters 的连接参数。

    Args:
        server: ``ext_store`` 返回的服务配置字典。

    Returns:
        连接参数字典（transport / url / headers 或 command / args / env）。

    Raises:
        ValueError: 传输类型未知（脏数据，调用方按单服务失败处理）。
    """
    transport = server.get("transport") or "streamable_http"
    if transport in ("streamable_http", "sse"):
        return {
            "transport": transport,
            "url": server.get("url") or "",
            "headers": server.get("headers") or {},
            "timeout": settings.MCP_LOAD_TIMEOUT,
        }
    if transport == "stdio":
        return {
            "transport": "stdio",
            "command": server.get("command") or "",
            "args": server.get("args") or [],
            "env": server.get("env") or {},
        }
    raise ValueError(f"未知 MCP 传输类型: {transport}")


async def _load_server_tools(server: dict) -> List[BaseTool]:
    """连接单个 MCP 服务并列出其工具（每次工具调用会新建会话，列表可长期复用）。

    Args:
        server: 服务配置字典。

    Returns:
        该服务暴露的 LangChain 工具列表。

    Raises:
        ImportError: 未安装 ``langchain-mcp-adapters``。
        Exception: 连接失败 / 握手失败 / 超时（``asyncio.TimeoutError``）向上抛。
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    name = server.get("name") or f"mcp-{server.get('id', 0)}"
    client = MultiServerMCPClient({name: _connection_for(server)})
    return await asyncio.wait_for(client.get_tools(), timeout=settings.MCP_LOAD_TIMEOUT)


async def get_mcp_tools() -> List[BaseTool]:
    """装配启用中 MCP 服务的远程工具（进程内缓存 + TTL）。

    单个服务加载失败只跳过该服务；跨服务同名工具先到先得（与内置工具的去重
    在 ``registry.build_tools_async`` 里做）。

    Returns:
        远程工具列表；开关关闭 / 依赖缺失 / 存储不可用 / 全部服务失败时为空列表。

    Raises:
        无——扩展能力失败不阻断对话。
    """
    global _mcp_cache
    if not settings.AGENT_MCP_ENABLED:
        return []
    now = time.monotonic()
    if _mcp_cache is not None and now - _mcp_cache[0] < settings.MCP_CACHE_TTL:
        return _mcp_cache[1]
    async with _mcp_lock:
        # 双检：等锁期间可能已被别的请求装载好。
        now = time.monotonic()
        if _mcp_cache is not None and now - _mcp_cache[0] < settings.MCP_CACHE_TTL:
            return _mcp_cache[1]
        tools: List[BaseTool] = []
        try:
            servers = await ext_store.list_enabled_mcp_servers()
        except Exception:
            logger.warning("读取 MCP 服务配置失败，本轮不装配远程工具", exc_info=True)
            servers = []
        seen: set = set()
        for server in servers:
            try:
                loaded = await _load_server_tools(server)
            except ImportError:
                logger.warning("未安装 langchain-mcp-adapters，MCP 扩展能力不可用"
                               "（pip install langchain-mcp-adapters 后重启生效）")
                break
            except Exception:
                logger.warning("MCP 服务工具加载失败，已跳过: name=%s", server.get("name"), exc_info=True)
                continue
            for tool in loaded:
                if tool.name in seen:
                    logger.warning("MCP 工具重名，保留先加载的: tool=%s server=%s",
                                   tool.name, server.get("name"))
                    continue
                seen.add(tool.name)
                tools.append(tool)
        if tools:
            logger.info("MCP 远程工具装配完成: %s 个（%s 个服务）", len(tools), len(servers))
        _mcp_cache = (time.monotonic(), tools)
        return tools


async def get_skills_prompt() -> str:
    """拼出启用中技能的**元数据目录**（进程内缓存 + TTL），供提示词常驻。

    渐进式披露第一级：这里只放 name + description，不放正文；模型判断相关时经
    ``load_skill`` 工具按需取正文（第二级），资源目录按需读取（第三级）。

    Returns:
        目录文本；无启用技能 / 读取失败时为空串。

    Raises:
        无——技能装配失败不阻断对话。
    """
    global _skills_cache
    now = time.monotonic()
    if _skills_cache is not None and now - _skills_cache[0] < settings.MCP_CACHE_TTL:
        return _skills_cache[1]
    async with _skills_lock:
        now = time.monotonic()
        if _skills_cache is not None and now - _skills_cache[0] < settings.MCP_CACHE_TTL:
            return _skills_cache[1]
        try:
            metas = await asyncio.to_thread(skill_store.list_enabled_metadata)
        except Exception:
            logger.warning("读取 Skill 元数据失败，本轮不注入技能目录", exc_info=True)
            metas = []
        lines: List[str] = []
        total = 0
        for meta in metas:
            line = f"- {meta['name']}: {meta['description']}"
            if total + len(line) > _SKILLS_CATALOG_MAX_CHARS:
                logger.warning("技能目录超上限，其余技能本轮省略: 已注入=%s", total)
                break
            lines.append(line)
            total += len(line)
        text = ""
        if lines:
            text = ("## 可用技能（按需加载）\n\n"
                    "以下技能由管理员配置。先按描述判断是否与当前问题相关；相关时调用 load_skill 工具"
                    "（传入技能名）获取完整流程，再按其指令作答；不相关则无需加载。\n\n"
                    + "\n".join(lines))
        _skills_cache = (time.monotonic(), text)
        return text


async def test_mcp_server(server: dict) -> List[dict]:
    """管理页「测试连接」：即时连接一次并返回工具清单（不走缓存）。

    Args:
        server: 服务配置字典（可以是尚未保存的表单数据）。

    Returns:
        ``[{"name", "description"}]`` 工具摘要列表。

    Raises:
        ImportError: 未安装 ``langchain-mcp-adapters``，由路由层转成可读提示。
        Exception: 连接 / 握手 / 超时失败，由路由层转成可读提示。
    """
    tools = await _load_server_tools(server)
    return [{"name": t.name, "description": (t.description or "").strip()[:200]} for t in tools]