"""LangGraph 编排图子包。

职责：把每类 AI 能力实现为一张独立的状态图（StateGraph），路由层只负责协议与会话，
业务决策全部收敛在图节点里。

主要导出：
    - ``chat_graph``      养宠顾问对话（意图识别 → 知识检索 / 工具调用 → 记忆召回 → 组装 → 生成）
    - ``health_graph``    宠物健康评估（档案整理 → 评估 → 落库）
    - ``review_graph``    商品评论摘要（拉取评论 → 摘要 → 缓存）
    - ``recommend_graph`` 个性化推荐（画像聚合 → 候选召回 → 重排 → 缓存）

外部依赖：``langgraph``（StateGraph / Send / add_messages）、``app.llm``、``app.clients``、
``app.schemas``、``app.kb`` 以及各存储适配层。

使用示例：
    from app.graph.chat_graph import get_chat_graph, initial_state

    graph = get_chat_graph()          # 进程内单例，编译一次后复用
    state = initial_state(query="猫咪打疫苗要注意什么", pet={}, user_id=1, messages=[])
    result = await graph.ainvoke(state, config={"configurable": {"thread_id": "t-1"}})
"""
