"""服务入口：FastAPI 应用装配与生命周期管理。

启动约定：所有初始化都「失败可降级」，只有「能启动但功能不完整」的情况（MOCK 模式、
RAG 缺 Embedding）会在启动日志里显式告警，避免被误当成代码 bug。
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# 直接执行本文件时 sys.path[0] 是 app/，找不到 `app.xxx` 包；补上项目根目录让两种启动方式一致。
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from app import checkpoint, ext_store, memory, memstore, nacos_client, persistence, pg_store
from app.chat import router as chat_router
from app.clients import biz
from app.config import settings
from app.ext_api import router as ext_router
from app.health import router as health_router
from app.kb import store as kb_store
from app.kb_api import router as kb_router
from app.logging_setup import setup_logging
from app.recommend import router as recommend_router
from app.review import router as review_router
from app.schemas import fail

# 必须在创建 app 之前配置，保证 import 阶段之后的所有输出都带统一格式。
setup_logging()
logger = logging.getLogger("ai-service")


async def _register_after_ready() -> None:
    """等 uvicorn 真正监听端口后再注册 Nacos。

    lifespan startup 阶段端口尚未开始监听，此时回连自检必然失败，会把 127.0.0.1
    注册出去，网关跨容器访问不到。
    """
    await asyncio.sleep(1.5)
    await asyncio.to_thread(nacos_client.register)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动 / 停机钩子：启动按「被依赖者在前」初始化，停机严格逆序释放。"""
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
    memory.init()
    await memory.check_connection()
    await persistence.init()
    await checkpoint.init()
    await memstore.init()
    await pg_store.init()
    await ext_store.init()
    await kb_store.init()
    if settings.RAG_ENABLED and settings.embedding_enabled:
        logger.info("知识库规模: %s", await kb_store.stats())
    # Nacos 注册放后台任务：lifespan 阶段 uvicorn 还没监听端口，注册前自检会失败。
    register_task = asyncio.create_task(_register_after_ready())
    logger.info("ai-service 启动完成: %s @ %s:%s（mock=%s, rag开关=%s, embedding=%s, deploy=%s）",
                settings.AI_SERVICE_NAME, settings.AI_SERVICE_HOST,
                settings.AI_SERVICE_PORT, settings.is_mock,
                settings.RAG_ENABLED, settings.embedding_enabled,
                settings.SERVICE_DEPLOY_MODE)
    yield
    register_task.cancel()
    with suppress(asyncio.CancelledError):
        await register_task
    await asyncio.to_thread(nacos_client.deregister)
    await biz.close()
    await checkpoint.close()
    await memstore.close()
    await kb_store.close()
    await ext_store.close()
    await pg_store.close()
    await persistence.close()
    await memory.close()
    logger.info("ai-service 已停止，资源已释放")


app = FastAPI(title="PetVerse AI Service", lifespan=lifespan)

app.include_router(chat_router)
app.include_router(kb_router)
app.include_router(ext_router)
app.include_router(health_router)
app.include_router(review_router)
app.include_router(recommend_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """把框架级参数校验错误改写成统一业务报文。

    FastAPI 默认返回 422 + 英文错误数组，而前端只认 HTTP 200 + ``{code,msg,data}``。
    """
    logger.warning("参数校验失败: %s %s -> %s", request.method, request.url.path, exc.errors())
    return JSONResponse(status_code=200, content=fail(400, "参数错误"))


if __name__ == "__main__":
    import uvicorn

    # host 用监听地址 AI_SERVICE_HOST，不要用注册给 Nacos 的 AI_SERVICE_IP：
    # 后者留空时是探测出来的，拿去绑定会因本机没有那张网卡而启动失败。
    uvicorn.run(app, host=settings.AI_SERVICE_HOST, port=settings.AI_SERVICE_PORT)
