"""ai-service 启动入口

FastAPI 实例 + lifespan 生命周期：
- 启动：初始化 Redis 连接池（redis.asyncio）+ MySQL 连接池（aiomysql）+ Nacos 注册（同步 SDK 放后台线程，不阻塞事件循环）
- 停机：Nacos 反注册 + 关闭 MySQL 连接池 + 关闭 Redis 连接池

服务绑定 127.0.0.1:8086（与 .env 中 AI_SERVICE_IP / AI_SERVICE_PORT 保持一致）。
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import memory, nacos_client, persistence
from app.chat import router as chat_router
from app.config import settings
from app.schemas import fail

# 统一日志格式（uvicorn 自身的 access/error 日志不受影响）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("ai-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务生命周期：启动时初始化资源，停机时释放"""
    # ---------- 启动阶段 ----------
    if settings.is_mock:
        logger.warning(
            "当前运行在 MOCK 模式（MOCK_CHAT=%s，DEEPSEEK_API_KEY %s），对话返回内置模拟回复；"
            "如需接入真实 DeepSeek，请在 .env 中填写 DEEPSEEK_API_KEY 并将 MOCK_CHAT 置为 false",
            settings.MOCK_CHAT, "为空" if not settings.DEEPSEEK_API_KEY.strip() else "已配置但被开关覆盖",
        )
    # Redis 连接池：创建连接池本身不发网络请求，真正连接在首次命令时惰性建立
    memory.init()
    # MySQL 连接池：对话消息持久化存储，aiomysql.create_pool 是异步的需 await
    await persistence.init()
    # Nacos 注册：SDK 是同步库，用 to_thread 丢进后台线程，避免阻塞事件循环
    await asyncio.to_thread(nacos_client.register)
    logger.info("ai-service 启动完成: %s @ %s:%s（mock=%s）",
                settings.AI_SERVICE_NAME, settings.AI_SERVICE_IP,
                settings.AI_SERVICE_PORT, settings.is_mock)
    yield
    # ---------- 停机阶段 ----------
    await asyncio.to_thread(nacos_client.deregister)
    await persistence.close()
    await memory.close()
    logger.info("ai-service 已停止，资源已释放")


# FastAPI 应用实例
app = FastAPI(title="PetVerse AI Service", lifespan=lifespan)

# 挂载对话路由（/ai/chat/stream、/ai/chat/history）
app.include_router(chat_router)


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
