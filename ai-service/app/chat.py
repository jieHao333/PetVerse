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
  4. DELETE /ai/chat/sessions/{sid}     删除会话（连带消息与 checkpoint 记忆）

存储分层（PostgreSQL 库 petverse_ai，业务数据同库）：
  - LangGraph checkpoint（checkpoint.py）：LLM 上下文记忆——图状态 messages 按
    thread（用户 + 会话）自动持久化，跨轮恢复；用户中止生成时把用户问题与
    已产出的部分回复补写回图状态，被暂停的轮次同样进入后续对话的记忆；
  - chat_session / chat_message（persistence.py）：会话与消息持久层，前端历史
    展示 / 会话管理从这里读，与 checkpoint 同库不同表。
"""
import asyncio
import json
import logging
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Header, Path, Query
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage

from app import checkpoint, llm, longterm, memstore, persistence
from app.config import settings
from app.graph.chat_graph import get_chat_graph, initial_state
from app.schemas import (
    ChatRequest,
    HistoryData,
    MemoryListData,
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
# 单用户并发上限：防止个别用户刷脚本占满全局 20 路并发，挤占其他用户
_USER_MAX_CONCURRENT = 2
# 单用户当前并发的流式对话数（user_id -> 计数），随流的 finally 释放
_user_active: dict = {}
# SSE 心跳间隔（秒）：超过该时间没有 token 产出就发一行注释 ping，防止代理断连
_HEARTBEAT_INTERVAL = 15.0
# 单条用户消息长度上限（字符）：超长消息会稀释上下文、烧 token，直接拒绝
_MAX_MESSAGE_LEN = 2000
# 流式生成的最大尝试次数：首帧前失败（LLM 网络抖动 / 服务端瞬时故障）自动静默重试一轮
_STREAM_ATTEMPTS = 2
# 对外统一兑底话术
_ERROR_MSG = "AI 服务暂时开小差了，请稍后再试"
_USER_BUSY_MSG = "您有正在生成的咨询回复，请等它结束后再发送"

# 后台长期记忆抽取任务集合（持引用防 GC；done 回调自动移除）
_bg_memory_tasks: set = set()


def _release_user_slot(user_id: int) -> None:
    """释放单用户并发槽位（计数归零时移除键，避免字典无限膨胀）"""
    left = _user_active.get(user_id, 0) - 1
    if left > 0:
        _user_active[user_id] = left
    else:
        _user_active.pop(user_id, None)


def _sse(data: dict) -> str:
    """构造一条 SSE 事件（data: JSON + 空行结尾）"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _spawn_memory_extraction(user_id: int, pet_id: int, pet_name: str,
                             user_msg: str, reply: str) -> None:
    """每轮正常结束后后台抽取长期记忆（不阻塞 SSE done / 不拖慢响应）

    条件由调用方保证（真实模式 + reply 非空 + 长期记忆可用）；任务持引用防 GC，
    done 回调从集合移除避免无限膨胀；任务内部已全部静默降级，此处无需接异常。
    """
    task = asyncio.create_task(
        longterm.extract_and_apply(user_id, pet_id, pet_name, user_msg, reply))
    _bg_memory_tasks.add(task)
    task.add_done_callback(_bg_memory_tasks.discard)


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
    - 会话创建 / 校验依赖 PostgreSQL，故障时返回「稍后再试」错误。
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


async def _graph_stream(state: dict, config: dict):
    """把 LangGraph 的消息流归一化为「助手文本片段」流

    graph.astream(stream_mode="messages") 会同时产出各节点的 LLM 调用消息（意图识别、
    工具 Agent、最终生成）；这里只取最终生成节点的助手文本增量，避免把中间产物
    透传给前端。通过 metadata.langgraph_node 过滤 generate 节点。
    config 携带 thread_id：图状态在每个超级步后由 checkpoint 自动持久化。
    """
    graph = get_chat_graph()
    async for chunk, meta in graph.astream(state, config=config, stream_mode="messages"):
        # 仅透传最终生成节点（generate）的助手文本
        if meta and meta.get("langgraph_node") != "generate":
            continue
        content = getattr(chunk, "content", None)
        if not content:
            continue
        # 仅保留 AI 消息（过滤 Human/System/Tool 消息）
        chunk_type = getattr(chunk, "type", "")
        if chunk_type and chunk_type not in ("AIMessageChunk", "ai"):
            continue
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, str):
                    yield part
                elif isinstance(part, dict) and part.get("type") == "text":
                    yield part.get("text", "")


def _history_messages(history: list) -> list:
    """把历史消息（role/content）转成 LangChain 消息（存量会话首次接入 checkpoint 时回填用）

    id 取「回填-序号-时间戳」的确定性值：若同一轮重复触发回填，
    add_messages 按 id 覆盖而非追加，不会产生重复消息。
    """
    messages: list = []
    for idx, item in enumerate(history):
        content = item.get("content")
        if not content:
            continue
        msg_id = f"bf-{idx}-{item.get('ts', 0)}"
        if item.get("role") == "user":
            messages.append(HumanMessage(content=content, id=msg_id))
        elif item.get("role") == "assistant":
            messages.append(AIMessage(content=content, id=msg_id))
    return messages


async def _persist_interrupted(graph, config: dict, user_id: int, pet_id: int,
                               session_id: int, user_msg: str,
                               human_msg: HumanMessage, partial: str) -> None:
    """用户中止生成后的记忆补写（在 asyncio.shield 保护下调用）

    1) checkpoint：把用户问题（与输入同 id，去重）与已产出的部分回复补写回图状态，
       部分回复标记 interrupted；as_node="generate" 表示这些更新来自最终生成节点，
       图状态就此收尾（next 为空），下一轮对话从 START 重新展开，不会重放未完成节点；
    2) PostgreSQL：兜底保存本轮（供前端历史回放），会话已删除时原子跳过。
    """
    messages: list = [human_msg]
    if partial:
        messages.append(AIMessage(content=partial, additional_kwargs={"interrupted": True}))
    if checkpoint.available():
        try:
            await graph.aupdate_state(config, {"messages": messages}, as_node="generate")
        except Exception:
            logger.warning("中断记忆补写 checkpoint 失败(忽略): user_id=%s session_id=%s",
                           user_id, session_id, exc_info=True)
    await persistence.save_turn_if_exists(user_id, pet_id, session_id, user_msg, partial,
                                          interrupted=True)


async def _append_mock_reply(graph, config: dict, reply: str) -> None:
    """mock 模式的回复由路由层打字机流产出（不在图内），补写进图状态保持跨轮记忆一致"""
    if not checkpoint.available():
        return
    try:
        await graph.aupdate_state(config,
                                  {"messages": [AIMessage(content=reply)], "reply": reply},
                                  as_node="generate")
    except Exception:
        logger.warning("mock 回复补写 checkpoint 失败(忽略)", exc_info=True)


async def _stream_generator(req: ChatRequest, user_id: int, session_id: int, pet_id: int):
    """SSE 流式对话生成器（核心逻辑，由 LangGraph 编排驱动）

    对话记忆由 LangGraph 官方 checkpoint（PostgreSQL）承载：
    - 正常结束：generate 节点把助手回复写回图状态 messages，随 checkpoint 自动持久化；
    - 用户中止：取消路径把用户问题与已产出的部分回复补写回图状态（_persist_interrupted），
      下一轮对话从 checkpoint 恢复时即可看到这段记忆，不再出现「暂停后失忆」。
    """
    pet: PetInfo = req.pet if req.pet is not None else PetInfo()

    # 1. 并发闸：超时拿不到信号量说明服务过载
    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=_SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        yield _sse({"type": "error", "msg": _ERROR_MSG})
        return

    collected = []          # 已累计的回复片段
    appended = False        # 本轮是否已收尾写入（防止取消分支重复补写）
    producer: Optional[asyncio.Task] = None
    user_slot_taken = False # 是否已登记单用户并发槽位（finally 按此决定是否释放）
    graph = None            # 在首个 await 前完成赋值，保证取消分支可安全引用
    config: Optional[dict] = None
    human_msg: Optional[HumanMessage] = None
    try:
        # 1.5 单用户并发登记：与路由层的预检查配合（非严格原子，竞态窗口极小）
        _user_active[user_id] = _user_active.get(user_id, 0) + 1
        user_slot_taken = True
        if _user_active[user_id] > _USER_MAX_CONCURRENT:
            yield _sse({"type": "error", "msg": _USER_BUSY_MSG})
            return   # finally 负责释放信号量与用户槽位
    
        # 2. 构造本轮对话的图引用 / 线程配置 / 输入消息（全部同步，先于任何 await 完成）：
        #    首帧 meta 一旦发出，用户随时可能点停止，
        #    取消分支必须能安全引用这三个对象来补写记忆。
        graph = get_chat_graph()
        config = checkpoint.thread_config(user_id, session_id)
        human_msg = HumanMessage(content=req.message, id=uuid4().hex)

        # 3. 首帧回传会话元信息：前端据此绑定 sessionId（自动新建会话场景）并刷新会话列表
        yield _sse({"type": "meta", "sessionId": session_id})

        # 4. 构造本轮输入：历史记忆由 checkpoint 按 thread（用户 + 会话）恢复；
        #    中断过的轮次也已补写进图状态，因此「用户暂停后 AI 没记忆」不再发生。
        #    兼容升级：存量会话在 checkpoint 中还没有记录（此前记忆在 Redis），
        #    首次对话时用 PostgreSQL 最近 N 条历史回填一次（回填消息 id 确定性，重复触发不叠加）。
        input_messages: list = [human_msg]
        if checkpoint.available():
            try:
                snapshot = await graph.aget_state(config)
                has_memory = bool(snapshot and snapshot.values.get("messages"))
            except Exception:
                logger.warning("读取对话 checkpoint 失败(本轮不回填历史): user_id=%s session_id=%s",
                               user_id, session_id, exc_info=True)
                has_memory = True   # 读取失败时跳过回填，避免与已有 checkpoint 产生重复
            if not has_memory:
                history = await persistence.read_history(
                    user_id, session_id, limit=settings.HISTORY_MAX_MESSAGES)
                input_messages = _history_messages(history) + input_messages

        # 5. 由 LangGraph 编排本轮对话：意图识别 → (RAG 检索 / 工具调用) → Prompt 组装 → 生成
        state = initial_state(
            query=req.message,
            pet=pet.model_dump(),
            user_id=user_id,
            messages=input_messages,
        )

        if settings.is_mock:
            # mock 模式：先跑一遍编排（意图/检索/工具/组装逻辑一致），再用打字机流输出内置回复
            try:
                await graph.ainvoke(state, config=config)
            except Exception:
                logger.warning("mock 模式编排执行异常（忽略，继续输出 mock 回复）", exc_info=True)

        # 6. 生产者-消费者消费流：消费端带 15 秒心跳超时
        # 首帧韧性：若在产出任何内容之前流就失败（LLM 服务商网络抖动、瞬时 429 等），
        # 自动重建流静默重试一轮，用户无感知；已产出内容后失败不重试，
        # 避免向用户重复输出（collected 非空时直接走 error 分支）。
        # 重试重建的 astream 携带同一 thread config：本轮 HumanMessage 以固定 id 合并进
        # 图状态，重复投递只会按 id 覆盖，不会产生重复消息。
        queue: asyncio.Queue = asyncio.Queue()
        for attempt in range(_STREAM_ATTEMPTS):
            if settings.is_mock:
                stream = llm.mock_stream(req.message, pet.name or "")
            else:
                stream = _graph_stream(state, config)
            producer = asyncio.create_task(_pump(stream, queue))
            retryable = False
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
                    if not collected and attempt < _STREAM_ATTEMPTS - 1:
                        # 首帧前失败：静默重建流重试（producer 已随 error 信号自行结束）
                        logger.warning("LLM 流首帧前失败，自动重试（第 %s 次）: %r",
                                       attempt + 1, payload)
                        retryable = True
                        break
                    logger.warning("LLM 流生成失败: %r", payload)
                    yield _sse({"type": "error", "msg": _ERROR_MSG})
                    return
                # kind == "delta"
                collected.append(payload)
                yield _sse({"type": "delta", "content": payload})
            if not retryable:
                break   # 流正常结束，退出尝试循环进入收尾写入

        # 7. 流正常结束：PostgreSQL 持久化（前端历史）+ mock 模式补写图状态，随后发出 done
        # checkpoint 侧：真实模式由 generate 节点写回 messages，图任务收尾时自动落库；
        # mock 模式回复不在图内，由 _append_mock_reply 手动补写，保持两模式记忆一致。
        reply = "".join(collected)
        if settings.is_mock and reply.strip():
            await _append_mock_reply(graph, config, reply)
        await persistence.save_turn(user_id, pet_id, session_id, req.message, reply)
        appended = True   # 已收尾写入：后续若被取消，取消分支不再重复补写
        # 8. 后台抽取长期记忆：正常轮 + 完整回复才抽取（中断轮 partial 易误导），
        #    异步执行不阻塞 done 事件，失败在任务内部静默降级
        if (not settings.is_mock and reply.strip()
                and settings.MEMORY_ENABLED and memstore.available()):
            _spawn_memory_extraction(user_id, pet_id, pet.name or "",
                                     req.message, reply)
        yield _sse({"type": "done"})
    except (asyncio.CancelledError, GeneratorExit):
        # 客户端中途断开 / 用户点击「停止生成」：补写本轮记忆后安静退出。
        #
        # 为什么必须补写：中断发生在图任务走到 generate 落盘之前，若不补写，
        # 这轮「用户问了什么 + 已答了多少」在后续对话中完全缺失（即此前的失忆问题）。
        #
        # 竞态与安全说明：
        # 1) 取消路径中裸 await 可能被再次取消导致写入夭折，用 shield 包住保证写入落地；
        # 2) human_msg 与输入共用同一固定 id，add_messages 按 id 覆盖去重——
        #    即使输入轮已进过 checkpoint，也不会产生重复消息；
        # 3) PostgreSQL 侧用 save_turn_if_exists：用户此刻已删除会话则原子跳过，不复活历史；
        # 4) 正常路径已收尾写入（appended=True）时不再补写，避免双写。
        try:
            if not appended and graph is not None and human_msg is not None:
                partial = "".join(collected)
                await asyncio.shield(_persist_interrupted(
                    graph, config, user_id, pet_id, session_id, req.message, human_msg, partial))
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
        if user_slot_taken:
            _release_user_slot(user_id)
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

    # 2. 消息内容校验：空 / 纯空白直接拒绝；超长消息拒绝（保护上下文窗口与 token 成本）
    if not req.message or not req.message.strip():
        return JSONResponse(status_code=200, content=fail(400, "参数错误"))
    if len(req.message) > _MAX_MESSAGE_LEN:
        return JSONResponse(status_code=200, content=fail(400, f"消息过长，请精简到 {_MAX_MESSAGE_LEN} 字以内"))
    
    # 2.5 单用户并发闸：同一用户最多同时 _USER_MAX_CONCURRENT 路流式对话（预检查在会话创建前，
    # 避免超限请求白建空会话；正式登记在流生成器内完成，二者非严格原子但竞态窗口极小）
    if _user_active.get(user_id, 0) >= _USER_MAX_CONCURRENT:
        return JSONResponse(status_code=200, content=fail(429, _USER_BUSY_MSG))

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

    从 PostgreSQL 读取持久化的完整历史（服务重启也不丢）；
    查询前校验会话归属，防止越权读取他人会话；
    PostgreSQL 读取异常时内部已降级为 []，前端看到空历史不会报错。
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


@router.get("/chat/memories")
async def list_memories(
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """查询当前用户的全部长期记忆（用户级 + 所有宠物级，最近更新在前）

    长期记忆存于 LangGraph 官方 runtime store（PostgreSQL，见 app/memstore.py），
    跨会话生效、与删除会话解耦；读取异常已内部降级为空列表，前端不会报错。
    """
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    try:
        memories = await longterm.list_memories(user_id)
    except Exception:
        logger.warning("查询长期记忆失败: user_id=%s", user_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    data = MemoryListData(memories=memories).model_dump()
    return JSONResponse(status_code=200, content=ok(data))


@router.delete("/chat/memories/{key}")
async def delete_memory(
    key: str = Path(...),
    scope: str = Query(default="user"),
    petId: Optional[int] = Query(default=None),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """删除单条长期记忆

    命名空间由「用户 ID + scope + petId」定位，天然隔离越权：他人无法通过
    猜 key 删掉别人的记忆；store 侧删除异常已内部降级，返回成功但记忆可能
    仍在（前端刷新列表可见），不影响主流程。
    """
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    if scope not in ("user", "pet"):
        return JSONResponse(status_code=200, content=fail(400, "参数错误"))
    if scope == "pet" and petId is None:
        return JSONResponse(status_code=200, content=fail(400, "参数错误"))

    store = memstore.get_store()
    if store is None:
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    ns = (memstore.user_ns(user_id) if scope == "user"
          else memstore.pet_ns(user_id, petId))
    try:
        await store.adelete(ns, key)
    except Exception:
        logger.warning("删除长期记忆失败: user_id=%s key=%s", user_id, key, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    return JSONResponse(status_code=200, content=ok(None))


@router.delete("/chat/sessions/{session_id}")
async def delete_session(
    session_id: int = Path(...),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """删除会话：连带删除会话下全部消息（PostgreSQL）与 checkpoint 记忆"""
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

    # 并行删除：会话与消息（PostgreSQL，事务化）+ checkpoint 记忆（同库）。
    # checkpoint 侧异常已内部降级仅记日志；会话消息侧是事务化删除
    # （消息 + 会话要么都删要么都不删）且失败会向上抛，此处必须接住转业务错误：
    # 否则 FastAPI 会返回裸 500，前端拿不到统一的 {"code","msg","data"} 报文。
    # checkpoint 已删而消息事务回滚的情况无害：会话仍在，下轮对话会触发历史回填。
    try:
        await asyncio.gather(
            checkpoint.adelete_thread(user_id, session_id),
            persistence.delete_session(user_id, session_id),
        )
    except Exception:
        logger.warning("删除会话失败: user_id=%s sessionId=%s", user_id, session_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    return JSONResponse(status_code=200, content=ok(None))
