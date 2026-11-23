"""自主 Agent 的工具子包。

把「模型可调用的能力」按业务域组织成可插拔的注册表，并区分只读能力与写操作。

分组语义：
    只读：``knowledge`` / ``pet`` / ``shop`` / ``community`` / ``profile``
    写操作：``cart`` / ``interaction`` / ``pet_edit``（默认开启，可撤销）
            ``publish``（默认开启，每次发布都挂起等人工审批）
            ``content``（默认关闭，公开发布且暂无审批）
    交易类动作（下单/支付）不提供工具，AI 的电商能力止于加购。
"""

from app.agent.tools.knowledge import search_pet_knowledge
from app.agent.tools.registry import (
    ALL_GROUPS,
    WRITE_GROUPS,
    ToolContext,
    build_tools,
    build_tools_async,
    enabled_groups,
    has_write_tools,
)

__all__ = [
    "ALL_GROUPS",
    "WRITE_GROUPS",
    "ToolContext",
    "build_tools",
    "build_tools_async",
    "enabled_groups",
    "has_write_tools",
    "search_pet_knowledge",
]
