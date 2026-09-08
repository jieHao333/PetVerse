"""对话路由（APIRouter prefix="/ai"）

网关 StripPrefix=1：前端请求 /api/ai/chat/stream，本服务实际收到 /ai/chat/stream。

会话模型：对话按「用户 + 宠物 + 会话」三级隔离——
一只宠物可拥有多个会话；切换宠物 / 新建对话均进入待创建态，会话在用户发出首条消息时才落库
（避免频繁切换堆积空会话），历史会话可手动选择恢复；会话支持删除。
前端侧栏直接展示该用户与所有宠物的全部历史会话（每条带归属宠物 petId），无需先选宠物。

接口一览：
  1. POST   /ai/chat/stream             SSE 流式对话（sessionId 缺省时自动建会话，首帧 meta 回传）
  2. GET    /ai/chat/history            查询指定会话的对话历史（时间正序：旧 → 新）
  3. GET    /ai/chat/sessions           查询用户的全部会话（跨宠物统一展示，最近活跃在前）
  4. DELETE /ai/chat/sessions/{sid}     删除会话（连带消息与 Redis 缓存）

存储分层：
  - Redis（memory.py）：LLM 短窗口上下文缓存（按会话隔离），滚动保留最近 N 条 + TTL，追求写入延迟；
  - MySQL（persistence.py）：会话与消息持久层，服务重启 / Redis 过期都不丢历史；
  - 写入时两者并行，读取时：GET /chat/history 走 MySQL（持久），
    LLM 上下文优先 Redis，Redis 为空时回落 MySQL 最近 N 条。
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Header, Path, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app import llm, memory, persistence, persona
from app.config import settings
from app.schemas import (
    ChatRequest,
    HistoryData,
    PetInfo,
    SessionListData,
    fail,
    ok,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai")

# 全局并发闸：同一时刻最多 20 路流式对话
_semaphore = asyncio.Semaphore(20)
# 获取并发闸的超时（秒）：超时视为过载，向前端发 error 事件
_SEMAPHORE_TIMEOUT = 3.0
# SSE 心跳间隔（秒）：超过该时间没有 token 产出就发一行注释 ping，防止代理断连
_HEARTBEAT_INTERVAL = 15.0
# 对外统一兜底话术
_ERROR_MSG = "AI 服务暂时开小差了，请稍后再试"


def _sse(data: dict) -> str:
    """构造一条 SSE 事件（data: JSON + 空行结尾）"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _parse_user_id(x_user_id: Optional[str]) -> Optional[int]:
    """解析网关注入的 X-User-Id 请求头；缺失或非法时返回 None"""
    if not x_user_id:
        return None
    try:
        user_id = int(x_user_id)
    except (TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


async def _resolve_session(user_id: int, req: ChatRequest) -> tuple[Optional[int], Optional[JSONResponse]]:
    """解析本轮对话使用的会话 ID

    返回 (session_id, None) 表示解析成功；(None, response) 表示失败，直接返回业务错误：
    - 请求带 sessionId：校验会话存在且属于当前用户（防止跨用户 / 跨宠物串会话）；
    - 请求不带 sessionId：按 pet.id 自动新建会话（切换宠物后首次发消息即落入新会话）；
    - 会话创建 / 校验依赖 MySQL，故障时返回「稍后再试」错误。
    """
    pet: PetInfo = req.pet if req.pet is not None else PetInfo()
    pet_id = pet.id if pet.id is not None else 0
    try:
        if req.sessionId is not None:
            session = await persistence.get_session(user_id, req.sessionId)
            if session is None:
                return None, JSONResponse(status_code=200, content=fail(404, "会话不存在或已删除"))
            return session["id"], None
        session_id = await persistence.create_session(user_id, pet_id)
        return session_id, None
    except Exception:
        logger.warning("解析对话会话失败: user_id=%s sessionId=%s", user_id, req.sessionId, exc_info=True)
        return None, JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))


async def _pump(stream, queue: asyncio.Queue) -> None:
    """生产者协程：消费 LLM / mock 流，把片段与结束信号统一搬进队列

    这样做是为了让消费端（SSE 生成器）能对"等待下一个片段"做超时心跳，
    而不必直接对 async generator 使用 wait_for（超时取消会破坏生成器状态）。
    """
    try:
        async for chunk in stream:
            await queue.put(("delta", chunk))
        await queue.put(("end", None))
    except Exception as exc:  # 生产过程中的任何异常都转成队列信号，统一兜底
        await queue.put(("error", exc))


async def _stream_generator(req: ChatRequest, user_id: int, session_id: int, pet_id: int):
    """SSE 流式对话生成器（核心逻辑）"""
    pet: PetInfo = req.pet if req.pet is not None else PetInfo()

    # 1. 并发闸：超时拿不到信号量说明服务过载
    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=_SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        yield _sse({"type": "error", "msg": _ERROR_MSG})
        return

    # 2. 首帧回传会话元信息：前端据此绑定 sessionId（自动新建会话场景）并刷新会话列表
    yield _sse({"type": "meta", "sessionId": session_id})

    collected = []          # 已累计的回复片段
    appended = False        # 本轮对话是否已成功写入历史（防止取消分支重复兜底写入）
    producer: Optional[asyncio.Task] = None
    try:
        # 3. 组装 LLM 消息：养宠顾问上下文 + 本会话历史 + 本轮用户输入
        # 上下文来源：优先 Redis（短窗口快），Redis 为空时回落 MySQL 最近 N 条
        # （服务重启、Redis TTL 过期、Redis 被清空等场景下仍能拿到历史）
        history = await memory.read(user_id, session_id)
        if not history:
            history = await persistence.read_history(
                user_id, session_id, limit=settings.HISTORY_MAX_MESSAGES)
        messages = [{"role": "system", "content": persona.build_system_prompt(pet)}]
        for item in history:
            role, content = item.get("role"), item.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": req.message})

        # 4. 按模式选择数据源：mock 流或真实 DeepSeek 流
        if settings.is_mock:
            stream = llm.mock_stream(req.message, pet.name or "")
        else:
            stream = llm.stream_chat(messages)

        # 5. 生产者-消费者消费流：消费端带 15 秒心跳超时
        queue: asyncio.Queue = asyncio.Queue()
        producer = asyncio.create_task(_pump(stream, queue))
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                # 心跳注释行：以冒号开头，SSE 客户端会自动忽略
                yield ": ping\n\n"
                continue
            if kind == "end":
                break
            if kind == "error":
                logger.warning("LLM 流生成失败: %r", payload)
                yield _sse({"type": "error", "msg": _ERROR_MSG})
                return
            # kind == "delta"
            collected.append(payload)
            yield _sse({"type": "delta", "content": payload})

        # 6. 流正常结束：Redis + MySQL 并行写入，随后发出 done
        # 两者均为 best-effort（内部已捕获异常仅记日志），任何一个失败都不影响另一个；
        # 写入完成后才置 appended=True，避免取消分支重复兜底保存。
        reply = "".join(collected)
        if reply.strip():
            await asyncio.gather(
                memory.append(user_id, session_id, req.message, reply),
                persistence.save_turn(user_id, pet_id, session_id, req.message, reply),
            )
            appended = True   # 已成功写入：后续若被取消，兜底分支不再重复保存
        yield _sse({"type": "done"})
    except (asyncio.CancelledError, GeneratorExit):
        # 客户端中途断开：把已累计的部分回复写入历史后安静退出
        #
        # 竞态说明：
        # 1) 若用户此刻已删除会话（Redis key 被 DELETE / MySQL 行被 DELETE），
        #    无条件写入会把刚删掉的历史“复活”，故 Redis 用 append_if_exists、MySQL 用 save_turn_if_exists，
        #    两者均在不存时原子跳过，不复活已删除的记忆；
        # 2) 若正常路径的写入已成功执行（appended=True），此处再写一次会造成双写，
        #    因此仅在本轮尚未成功写入时才做兜底保存；
        # 3) 取消路径中裸 await 可能再次被取消导致写入中途夭折，用 shield 包住保证写入落地。
        try:
            if collected and not appended:
                partial = "".join(collected)
                await asyncio.shield(asyncio.gather(
                    memory.append_if_exists(user_id, session_id, req.message, partial),
                    persistence.save_turn_if_exists(user_id, pet_id, session_id, req.message, partial),
                ))
        except BaseException:
            pass
        raise
    except Exception:
        # 其余未预期异常：发 error 事件后正常结束流，HTTP 层不 5xx
        logger.exception("对话流生成出现未预期异常")
        yield _sse({"type": "error", "msg": _ERROR_MSG})
    finally:
        if producer is not None:
            producer.cancel()   # 生产者可能仍在跑（如消费端已退出），主动取消防泄漏
        _semaphore.release()


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """SSE 流式对话

    - 网关注入的 X-User-Id 缺失 / 非法：HTTP 200 + {"code":401,"msg":"未登录"}
    - 消息为空或纯空白：HTTP 200 + {"code":400,"msg":"参数错误"}
    - sessionId 非法 / 不属于当前用户：HTTP 200 + {"code":404,"msg":"会话不存在或已删除"}
    - 正常：text/event-stream 流，meta → delta * n → done（异常时发 error 后结束）
    """
    # 1. 登录校验（网关统一鉴权后注入 X-User-Id）
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))

    # 2. 消息内容校验：空 / 纯空白直接拒绝
    if not req.message or not req.message.strip():
        return JSONResponse(status_code=200, content=fail(400, "参数错误"))

    # 3. 会话解析：校验已有会话归属，或按宠物自动新建会话
    pet: PetInfo = req.pet if req.pet is not None else PetInfo()
    pet_id = pet.id if pet.id is not None else 0
    session_id, err = await _resolve_session(user_id, req)
    if err is not None:
        return err

    # 4. 返回 SSE 流式响应
    return StreamingResponse(
        _stream_generator(req, user_id, session_id, pet_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",        # 禁止各级缓存
            "X-Accel-Buffering": "no",         # 禁止 Nginx 反代缓冲
            "Connection": "keep-alive",         # 保持长连接
        },
    )


@router.get("/chat/history")
async def get_history(
    sessionId: Optional[int] = Query(default=None),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """查询指定会话的对话历史（时间正序：旧 → 新）

    从 MySQL 读取持久化的完整历史（服务重启、Redis TTL 过期都不丢）；
    查询前校验会话归属，防止越权读取他人会话；
    MySQL 读取异常时内部已降级为 []，前端看到空历史不会报错。
    """
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    if sessionId is None:
        return JSONResponse(status_code=200, content=fail(400, "参数错误"))
    try:
        session = await persistence.get_session(user_id, sessionId)
    except Exception:
        logger.warning("查询会话归属失败: user_id=%s sessionId=%s", user_id, sessionId, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if session is None:
        return JSONResponse(status_code=200, content=fail(404, "会话不存在或已删除"))

    messages = await persistence.read_history(user_id, sessionId)
    data = HistoryData(sessionId=sessionId, messages=messages).model_dump()
    return JSONResponse(status_code=200, content=ok(data))


@router.get("/chat/sessions")
async def list_sessions(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """查询用户的全部会话（跨宠物统一展示，最近活跃在前），每条带归属宠物 petId"""
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    try:
        sessions = await persistence.list_sessions(user_id)
    except Exception:
        logger.warning("查询会话列表失败: user_id=%s", user_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    data = SessionListData(sessions=sessions).model_dump()
    return JSONResponse(status_code=200, content=ok(data))


@router.delete("/chat/sessions/{session_id}")
async def delete_session(
    session_id: int = Path(...),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """删除会话：连带删除会话下全部消息（MySQL）与上下文缓存（Redis）"""
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    # 归属校验：仅允许删除本人会话
    try:
        session = await persistence.get_session(user_id, session_id)
    except Exception:
        logger.warning("查询会话归属失败: user_id=%s sessionId=%s", user_id, session_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if session is None:
        return JSONResponse(status_code=200, content=fail(404, "会话不存在或已删除"))

    # 并行删除：Redis 上下文缓存 + MySQL 会话与消息；Redis 异常已内部降级仅记日志
    await asyncio.gather(
        memory.clear(user_id, session_id),
        persistence.delete_session(user_id, session_id),
    )
    return JSONResponse(status_code=200, content=ok(None))
