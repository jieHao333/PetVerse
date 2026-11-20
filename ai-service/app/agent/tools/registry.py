"""工具注册表：按业务域装配模型可调用的能力。

职责：把「有哪些工具」与「这次请求该给模型哪些工具」解耦。新增能力只需写一个
``@tool`` 并在 ``_BUILDERS`` 里登记一行，不需要改图、改条件边或改路由层。

主要导出：
    - ``ToolContext``    构造工具所需的运行时上下文
    - ``build_tools``    按分组装配内置工具列表
    - ``build_tools_async`` 内置工具 + 管理员配置的 MCP 远程工具（对话链路用这个）
    - ``enabled_groups`` 解析配置里启用的分组

外部依赖：``app.agent.tools.knowledge``、``app.agent.tools.business``、``app.config``。

使用示例：
    from app.agent.tools import ToolContext, build_tools

    tools = build_tools(ToolContext(user_id=42))
    model.bind_tools(tools)

分组的意义：线上可以按配置关掉某一域（如商城维护时关掉 shop），模型就会「看不见」
这些工具，而不是靠提示词劝它别调用。工具的可见性比提示词约束更可靠。
"""

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

from langchain_core.tools import BaseTool

from app.agent.tools.actions import (
    add_to_cart,
    comment_post,
    like_post,
    publish_post,
    register_pet,
    remove_from_cart,
    unlike_post,
    update_cart_quantity,
    update_pet_health,
)
from app.agent.tools.business import (
    build_community_tools,
    build_pet_tools,
    build_profile_tools,
    build_shop_tools,
)
from app.agent.tools.knowledge import search_pet_knowledge
from app.agent.tools.skills import load_skill
from app.config import settings

logger = logging.getLogger(__name__)

# 全部分组，顺序即工具在提示词与模型候选列表中的顺序：知识检索最常用，放最前；
# 写操作靠后，减少模型在「只读场景」里被写工具分散注意力的概率。
ALL_GROUPS: tuple[str, ...] = (
    "knowledge", "skill", "pet", "shop", "cart", "community", "interaction", "pet_edit",
    "publish", "content", "profile",
)

# 有副作用的分组：用于提示词里追加「操作类规则」，也用于默认关闭高风险能力。
# 注意 ``order``（下单/支付）不在此列——交易类动作不开放给模型，见 actions.py 的能力边界说明。
WRITE_GROUPS = frozenset({"cart", "interaction", "pet_edit", "publish", "content"})
# 默认不开启的分组：公开发布需要显式配置后才交给模型。
# ``publish`` 不在此列：发布动态虽属公开发布，但每一次都由人工审批把关（见 AGENT_APPROVAL_TOOLS），
# 默认交给模型才能让「帮我发条动态」这件事办得成。``content``（评论）尚无审批，保持默认关闭。
DEFAULT_OFF_GROUPS = frozenset({"content"})

# 不需要用户身份的分组：未登录也该能查知识库（虽然当前路由层已拦截未登录）。
# skill 加载的是全局技能包，与用户身份无关，同样匿名可用。
_ANONYMOUS_GROUPS = frozenset({"knowledge", "skill"})


@dataclass(frozen=True)
class ToolContext:
    """构造工具所需的运行时上下文。

    Attributes:
        user_id: 当前登录用户 ID。业务工具全部闭包捕获它，模型无法通过参数越权。
    """

    user_id: int = 0


def _build_knowledge(_: ToolContext) -> List[BaseTool]:
    """知识检索工具无需按用户构造，复用同一个实例（schema 只推断一次）。"""
    return [search_pet_knowledge]


# 分组 → 构造函数。key 必须与 ALL_GROUPS 一致，否则配置里写了也不生效。
# 读工具按用户闭包构造（需要 user_id 做数据隔离）；写工具是无状态的模块级工具，
# 用户身份与 run_id 通过 InjectedState 在调用时注入（见 actions.py）。
_BUILDERS: Dict[str, Callable[[ToolContext], List[BaseTool]]] = {
    "knowledge": _build_knowledge,
    "skill": lambda ctx: [load_skill],
    "pet": lambda ctx: build_pet_tools(ctx.user_id),
    "shop": lambda ctx: build_shop_tools(ctx.user_id),
    "cart": lambda ctx: [add_to_cart, update_cart_quantity, remove_from_cart],
    "community": lambda ctx: build_community_tools(ctx.user_id),
    "interaction": lambda ctx: [like_post, unlike_post],
    "pet_edit": lambda ctx: [register_pet, update_pet_health],
    # 发布与评论分开：发布走人工审批、默认开启；评论没有审批、默认关闭。
    "publish": lambda ctx: [publish_post],
    "content": lambda ctx: [comment_post],
    "profile": lambda ctx: build_profile_tools(ctx.user_id),
}


def _default_groups() -> List[str]:
    """未配置时使用的分组集合。

    Returns:
        ``ALL_GROUPS`` 去掉 ``DEFAULT_OFF_GROUPS``：默认只开低风险、可撤销的能力，
        公开发布与资金/库存类必须由运维显式打开。

    Raises:
        无。
    """
    return [g for g in ALL_GROUPS if g not in DEFAULT_OFF_GROUPS]


def enabled_groups() -> List[str]:
    """解析配置中启用的工具分组。

    Returns:
        合法且启用中的分组名列表（保持 ``ALL_GROUPS`` 的顺序）；配置为空时退回默认分组
        （不含 ``content``）。显式配置 ``content`` 时会连带启用 ``publish``（兼容旧语义）。
        非法分组名只记日志、不抛异常——配错一个词
        不该让整个对话服务起不来。

    Raises:
        无。
    """
    raw = (settings.AGENT_TOOL_GROUPS or "").strip()
    if not raw:
        return _default_groups()
    wanted = {g.strip().lower() for g in raw.split(",") if g.strip()}
    unknown = wanted - set(ALL_GROUPS)
    if unknown:
        logger.warning("AGENT_TOOL_GROUPS 含未知分组，已忽略: %s", sorted(unknown))
    # 兼容旧配置：``content`` 历史上同时包含评论与发布，现在发布单独成组（便于单独审批）；
    # 显式写了 ``content`` 的部署继续同时得到两者，避免升级后「发动态」突然消失。
    if "content" in wanted:
        wanted.add("publish")
    # skill 是技能渐进式披露的基础能力（load_skill 工具），始终启用——
    # 否则旧的 AGENT_TOOL_GROUPS 配置漏写 skill 会让整个技能机制静默失效。
    wanted.add("skill")
    groups = [g for g in ALL_GROUPS if g in wanted]
    if not groups:
        logger.warning("AGENT_TOOL_GROUPS 未匹配到任何合法分组，回退为默认分组: %r", raw)
        return _default_groups()
    return groups


def has_write_tools(groups: Optional[Sequence[str]] = None) -> bool:
    """判断本次启用的工具里是否包含写操作。

    Args:
        groups: 指定分组；None 时读配置。

    Returns:
        True 表示应给模型追加「操作类能力」的使用规则。

    Raises:
        无。
    """
    wanted = set(groups) if groups is not None else set(enabled_groups())
    return bool(wanted & WRITE_GROUPS)


def build_tools(ctx: ToolContext, groups: Optional[Sequence[str]] = None) -> List[BaseTool]:
    """按分组装配工具列表。

    Args:
        ctx: 运行时上下文（用户身份）。
        groups: 指定分组；None 时读配置 ``AGENT_TOOL_GROUPS``。

    Returns:
        工具列表；未登录用户只会拿到不需要身份的分组（知识检索）。

    Raises:
        无——单个分组构造失败只跳过该组，不影响其他能力。
    """
    wanted = list(groups) if groups is not None else enabled_groups()
    tools: List[BaseTool] = []
    for group in wanted:
        builder = _BUILDERS.get(group)
        if builder is None:
            continue
        if ctx.user_id <= 0 and group not in _ANONYMOUS_GROUPS:
            # 没有身份就查不了「我的」数据，给了工具只会让模型查出一堆空结果。
            continue
        try:
            tools.extend(builder(ctx))
        except Exception:
            logger.warning("装配工具分组失败，已跳过: group=%s", group, exc_info=True)
    return tools


async def build_tools_async(ctx: ToolContext, groups: Optional[Sequence[str]] = None) -> List[BaseTool]:
    """内置工具 + 管理员配置的 MCP 远程工具（对话 Agent 的完整工具列表）。

    MCP 工具与内置工具同名时保留内置工具：内置工具的身份隔离与审批语义经过审计，
    不允许外部服务用一个同名工具把它顶掉。MCP 装配失败只损失远程工具，不影响内置能力。

    Args:
        ctx: 运行时上下文（用户身份）。
        groups: 指定分组；None 时读配置 ``AGENT_TOOL_GROUPS``。

    Returns:
        工具列表（内置在前，MCP 在后）。

    Raises:
        无——MCP 侧异常已在 extensions 内降级。
    """
    from app.agent.extensions import get_mcp_tools

    tools = build_tools(ctx, groups)
    mcp_tools = await get_mcp_tools()
    if mcp_tools:
        builtin_names = {t.name for t in tools}
        for tool in mcp_tools:
            if tool.name in builtin_names:
                logger.warning("MCP 工具与内置工具重名，保留内置工具: tool=%s", tool.name)
                continue
            tools.append(tool)
    return tools
