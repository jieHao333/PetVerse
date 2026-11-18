"""LangGraph 编排图子包（确定性批处理）。

职责：把「步骤固定、无需模型决策」的 AI 能力实现为独立的状态图（StateGraph），
路由层只负责协议与会话，业务步骤全部收敛在图节点里。

主要导出：
    - ``health_graph``    宠物健康评估（档案整理 → 评估 → 落库）
    - ``review_graph``    商品评论摘要（拉取评论 → 摘要 → 缓存）
    - ``recommend_graph`` 个性化推荐（画像聚合 → 候选召回 → 重排 → 缓存）

外部依赖：``langgraph``（StateGraph）、``app.llm``、``app.clients``、``app.schemas``、
``app.kb`` 以及各存储适配层。

使用示例：
    from app.graph.health_graph import get_health_graph

    report = await get_health_graph().ainvoke(initial_state(pet))

说明：对话链路**不在这里**。它的步骤数取决于用户问什么（可能查知识、可能查订单、可能都不查），
无法预先画成固定路径，因此改由 ``app.agent`` 的 AgentLoop 承载——由模型自己决定每一步。
"""
