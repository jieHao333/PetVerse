"""自主 Agent 的工具子包。

职责：把「模型可调用的能力」按业务域组织成可插拔的注册表，并区分只读能力与写操作。

主要导出：
    - ``ToolContext``   构造工具所需的运行时上下文
    - ``build_tools``   按分组装配工具列表
    - ``enabled_groups`` 解析配置里启用的分组
    - ``has_write_tools`` 本次是否启用了有副作用的能力

外部依赖：``langchain_core.tools``、``langgraph.prebuilt``（状态注入）、``app.clients.biz``。

使用示例：
    from app.agent.tools import ToolContext, build_tools

    tools = build_tools(ToolContext(user_id=42))

分组语义：
    只读：``knowledge`` / ``pet`` / ``shop`` / ``community`` / ``profile``
    写操作：``cart`` / ``interaction`` / ``pet_edit``（默认开启，可撤销）
            ``content``（默认关闭，公开发布）
    交易类动作（下单/支付）不提供工具，AI 的电商能力止于加购。
"""

from app.agent.tools.knowledge import search_pet_knowledge
from app.agent.tools.registry import (
    ALL_GROUPS,
    WRITE_GROUPS,
    ToolContext,
    build_tools,
    enabled_groups,
    has_write_tools,
)

__all__ = [
    "ALL_GROUPS",
    "WRITE_GROUPS",
    "ToolContext",
    "build_tools",
    "enabled_groups",
    "has_write_tools",
    "search_pet_knowledge",
]
