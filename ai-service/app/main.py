"""服务入口：FastAPI 应用装配与生命周期管理。

职责：
    1. 修正 ``sys.path``，使 ``python app/main.py`` 这种直接执行方式也能用 ``app.xxx`` 绝对导入；
    2. 在启动钩子里按依赖顺序初始化各存储 / 注册中心，在停机钩子里逆序释放；
    3. 挂载全部路由，并把参数校验错误统一改写为业务报文。

主要导出：
    - ``app``     FastAPI 实例（uvicorn 的入口对象）
    - ``lifespan`` 启动 / 停机钩子

外部依赖：FastAPI、uvicorn（仅 ``__main__`` 分支），以及本项目全部业务模块。

使用示例：
    # 方式一（推荐，见 README）
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8086
    # 方式二
    python -m app.main

启动约定：所有初始化都「失败可降级」，唯一例外是必要配置缺失时会在日志里显式告警
（MOCK 模式、RAG 缺 Embedding），确保运维能第一时间看到而不是等用户报错。
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# 直接执行本文件时（python app/main.py），sys.path[0] 是 app/ 目录，
# `import app.xxx` 会找不到包；这里把项目根目录补进路径，使两种启动方式行为一致。
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from app import checkpoint, memory, memstore, nacos_client, persistence, pg_store
from app.chat import router as chat_router
from app.clients import biz
from app.config import settings
from app.health import router as health_router
from app.kb import store as kb_store
from app.kb_api import router as kb_router
from app.logging_setup import setup_logging
from app.recommend import router as recommend_router
from app.review import router as review_router
from app.schemas import fail

# 在创建 app 之前配置日志，保证 import 阶段（如各模块创建 logger）之后的所有输出都带统一格式。
setup_logging()
logger = logging.getLogger("ai-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动 / 停机钩子。

    启动顺序按「被依赖者在前」排列：Redis（缓存 / 并发计数）→ 会话持久化 → 记忆相关
    （checkpoint / store / 报告库）→ 知识库 → 服务注册。停机严格逆序释放，避免
    先关底层连接、后关上层对象导致关闭时报错。

    Args:
        app: FastAPI 实例（框架注入，此处未直接使用）。

    Yields:
        None：``yield`` 之前的代码在启动时执行，之后在停机时执行。

    Raises:
        无——各 init 内部均已降级处理。
    """
    # 两类「能启动但功能不完整」的情况必须在启动日志里说清楚，否则排查时会误以为是代码 bug。
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
    await kb_store.init()
    if settings.RAG_ENABLED and settings.embedding_enabled:
        logger.info("知识库规模: %s", await kb_store.stats())
    # 注册与反注册是同步阻塞调用（HTTP / SDK），放线程池执行以免卡住事件循环启动。
    await asyncio.to_thread(nacos_client.register)
    logger.info("ai-service 启动完成: %s @ %s:%s（mock=%s, rag开关=%s, embedding=%s）",
                settings.AI_SERVICE_NAME, settings.AI_SERVICE_IP,
                settings.AI_SERVICE_PORT, settings.is_mock,
                settings.RAG_ENABLED, settings.embedding_enabled)
    yield
    await asyncio.to_thread(nacos_client.deregister)
    await biz.close()
    await checkpoint.close()
    await memstore.close()
    await kb_store.close()
    await pg_store.close()
    await persistence.close()
    await memory.close()
    logger.info("ai-service 已停止，资源已释放")


app = FastAPI(title="PetVerse AI Service", lifespan=lifespan)

app.include_router(chat_router)
app.include_router(kb_router)
app.include_router(health_router)
app.include_router(review_router)
app.include_router(recommend_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """把框架级参数校验错误改写成统一业务报文。

    默认情况下 FastAPI 返回 HTTP 422 + 英文错误数组，而前端只认 HTTP 200 +
    ``{code,msg,data}``；不转换会让参数错误在前端表现为「请求失败」而非可读提示。

    Args:
        request: 触发异常的请求（用于日志定位）。
        exc: 校验异常，``errors()`` 含字段级原因。

    Returns:
        HTTP 200 的 ``fail(400, "参数错误")`` 响应。

    Raises:
        无。
    """
    logger.warning("参数校验失败: %s %s -> %s", request.method, request.url.path, exc.errors())
    return JSONResponse(status_code=200, content=fail(400, "参数错误"))


if __name__ == "__main__":
    import uvicorn

    # 直接用 uvicorn.run 而非命令行：便于 IDE 一键调试；生产仍建议用 uvicorn 命令 + 多 worker。
    uvicorn.run(app, host=settings.AI_SERVICE_IP, port=settings.AI_SERVICE_PORT)
