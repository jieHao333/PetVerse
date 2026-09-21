"""ai-service 启动入口

FastAPI 实例 + lifespan 生命周期：
- 启动：初始化 Redis 连接池（通用缓存）+ PostgreSQL（会话/消息/checkpoint/pgvector）
  + Nacos 注册（同步 SDK 放后台线程）
- 停机：Nacos 反注册 + 依次释放各连接池

服务绑定 127.0.0.1:8086（与 .env 中 AI_SERVICE_IP / AI_SERVICE_PORT 保持一致）。
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# 支持以脚本方式直接运行本文件（IDE 里点 Run 走的是 `python .../app/main.py`，而非 `python -m app.main`）：
# 此时 sys.path[0] 是 app/ 目录本身，`from app import ...` 会因找不到 app 包而报 ModuleNotFoundError，
# 这里把包根目录（ai-service）补进 sys.path，保证两种方式（直接运行 / -m 模块运行）都能正常导入。
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from app import checkpoint, memory, memstore, nacos_client, persistence, pg_store
from app.chat import router as chat_router
from app.clients import biz
from app.config import settings
from app.health import router as health_router
from app.logging_setup import setup_logging
from app.recommend import router as recommend_router
from app.review import router as review_router
from app.schemas import fail

# 统一日志格式（uvicorn 自身的 access/error 日志不受影响）
setup_logging()
logger = logging.getLogger("ai-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务生命周期：启动时初始化资源，停机时释放"""
    # ---------- 启动阶段 ----------
    if settings.is_mock:
        logger.warning(
            "当前运行在 MOCK 模式（MOCK_CHAT=%s，LLM_API_KEY %s），对话返回内置模拟回复；"
            "如需接入真实模型，请在 .env 中填写 LLM_API_KEY（并将 MOCK_CHAT 置为 false）",
            settings.MOCK_CHAT, "为空" if not settings.LLM_API_KEY.strip() else "已配置但被开关覆盖",
        )
    if settings.RAG_ENABLED and not settings.embedding_enabled:
        logger.warning(
            "RAG_ENABLED=true 但未配置 Embedding（EMBEDDING_API_KEY / EMBEDDING_MODEL），"
            "知识类提问将直接报错；如不需要知识检索请将 RAG_ENABLED 置为 false"
        )
    # Redis 连接池：评论摘要 / 推荐等通用缓存（创建连接池本身不发网络请求）
    memory.init()
    # 会话与消息持久层（PostgreSQL）：连接池 + 业务表自动创建（幂等）
    await persistence.init()
    # LangGraph checkpoint：对话图状态（记忆）持久化到 PostgreSQL（同库不同表），
    # 建表失败的降级与重试由 checkpoint 模块内部处理，不会阻塞启动
    await checkpoint.init()
    # LangGraph runtime store：长期记忆（用户 / 宠物偏好，跨会话）持久化到
    # PostgreSQL（同库不同表），建表失败的降级与重试由 memstore 模块内部处理
    await memstore.init()
    # PostgreSQL 连接池：健康评估报告持久化 + AI 结果缓存（pgvector 由 vectorstore 惰性初始化）
    await pg_store.init()
    # Nacos 注册：SDK 是同步库，用 to_thread 丢进后台线程，避免阻塞事件循环
    await asyncio.to_thread(nacos_client.register)
    logger.info("ai-service 启动完成: %s @ %s:%s（mock=%s, rag开关=%s, embedding=%s）",
                settings.AI_SERVICE_NAME, settings.AI_SERVICE_IP,
                settings.AI_SERVICE_PORT, settings.is_mock,
                settings.RAG_ENABLED, settings.embedding_enabled)
    yield
    # ---------- 停机阶段 ----------
    await asyncio.to_thread(nacos_client.deregister)
    await biz.close()
    await checkpoint.close()
    await memstore.close()
    await pg_store.close()
    await persistence.close()
    await memory.close()
    logger.info("ai-service 已停止，资源已释放")


# FastAPI 应用实例
app = FastAPI(title="PetVerse AI Service", lifespan=lifespan)

# 挂载各能力路由：对话（/ai/chat/*）、健康评估（/ai/health/*）、
# 评论摘要（/ai/shop/review/summary）、个性化推荐（/ai/recommend/feed）
app.include_router(chat_router)
app.include_router(health_router)
app.include_router(review_router)
app.include_router(recommend_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """请求参数校验失败（缺字段 / 类型错误 / 缺 petId 等）

    统一转换为 HTTP 200 + {"code":400,"msg":"参数错误","data":null}，
    与 Java 端 GlobalExceptionHandler 的行为对齐。
    """
    logger.warning("参数校验失败: %s %s -> %s", request.method, request.url.path, exc.errors())
    return JSONResponse(status_code=200, content=fail(400, "参数错误"))


if __name__ == "__main__":
    # 本地直接运行：python -m app.main（生产建议用 uvicorn 命令，见 README.md）
    import uvicorn

    uvicorn.run(app, host=settings.AI_SERVICE_IP, port=settings.AI_SERVICE_PORT)
