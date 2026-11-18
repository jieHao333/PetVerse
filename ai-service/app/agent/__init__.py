"""养宠顾问对话的自主 Agent 运行时。

职责：把「模型自主决策 + 工具调用」的 AgentLoop 封装成一个可直接编译、可持久化、
可流式消费的 LangGraph 应用。接入层只需要 ``get_chat_agent()`` 拿到图，
``initial_state()`` 构造输入，其余决策全部发生在图内。

主要导出：
    - ``get_chat_agent``     取得（并编译一次）图实例
    - ``initial_state``      构造初始状态
    - ``preview_knowledge``  mock 模式下的引用预览
    - ``AgentState``         图状态定义

外部依赖：``langgraph``、``langchain-core``、``app.llm``、``app.kb``、
``app.checkpoint`` / ``app.memstore``。

使用示例：
    from app.agent import get_chat_agent, initial_state

    graph = get_chat_agent()
    state = initial_state(query="猫咪打疫苗要注意什么", pet={}, user_id=1, messages=[])
    async for mode, payload in graph.astream(state, config=cfg,
                                             stream_mode=["updates", "messages"]):
        ...

与 ``app/graph/`` 的分工：``app/graph/`` 保留健康评估 / 评论摘要 / 个性化推荐三张
**确定性批处理图**（步骤固定、无需模型决策）；对话链路因为步骤数无法预先确定，
改由本包的 AgentLoop 承载。
"""

from app.agent.graph import get_chat_agent, preview_knowledge
from app.agent.state import AgentState, initial_state

__all__ = ["AgentState", "get_chat_agent", "initial_state", "preview_knowledge"]
