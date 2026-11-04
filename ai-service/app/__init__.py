"""PetVerse AI 服务应用包。

职责：承载 ai-service 的全部业务代码——FastAPI 路由（对话 / 健康评估 / 评论摘要 /
个性化推荐 / 知识库管理）、LangGraph 编排图、RAG 子系统与各类存储适配层。

主要导出：本包不做聚合导出，各子模块按职责独立导入：
    - ``app.main``           FastAPI 应用实例与生命周期钩子（启动装配的唯一入口）
    - ``app.config``         全局配置对象 ``settings``
    - ``app.chat`` 等路由模块 提供 ``router`` 供 ``app.main`` 挂载

外部依赖：FastAPI、LangGraph、LangChain、PostgreSQL(psycopg / pgvector)、Redis、OSS。

使用示例：
    # 以模块方式启动服务（等价于 README 中的 uvicorn 命令）
    python -m app.main
"""
