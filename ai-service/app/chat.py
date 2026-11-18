"""对话路由层：SSE 流式对话与会话管理。

职责：承载 ``/ai/chat/**`` 接口——流式对话（SSE）、附件上传、历史 / 会话 / 长期记忆的查询与删除。
本层只做「协议 + 并发 + 落库」，业务决策（要不要检索、查哪些数据、如何作答）全部交给
``app.agent`` 里的自主 AgentLoop。

主要导出：
    - ``router``  FastAPI 路由（前缀 ``/ai``）

外部依赖：``app.agent``（AgentLoop 图 / 事件适配）、``app.persistence``（历史）、
``app.checkpoint``（对话记忆）、``app.limits``（并发闸）、``app.media`` / ``app.oss``（附件）、
``app.longterm`` / ``app.memstore``（长期记忆）。

SSE 事件协议（前端按序消费）：
    meta     → 首帧，回传 sessionId（前端据此把「新会话」变为可回访会话）
    refs     → 有知识命中时，在答案首字之前推送引用来源
    delta    → 增量文本，可出现多次
    approval → 敏感操作（支付 / 下单 / 公开发布）需要人工审批，本轮挂起且无 done；
               前端弹卡片后调 ``POST /ai/chat/approve`` 从断点继续
    done     → 正常结束
    error    → 异常结束，msg 为可直接展示的中文提示

使用示例：
    POST /ai/chat/stream
    Header: X-User-Id: 42
    Body: {"message": "猫咪打疫苗要注意什么", "pet": {"id": 7, "species": "猫"}}
"""

import asyncio
import json
import logging
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, File, Header, Path, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.types import Command

from app import checkpoint, limits, llm, longterm, memstore, media, oss, persistence
from app.agent import get_chat_agent, initial_state, preview_knowledge
from app.agent.events import extract_interrupt, extract_refs, is_answer_chunk
from app.agent.graph import NODE_FINALIZE
from app.config import settings
from app.schemas import (
    ApprovalRequest,
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

# 全局并发闸：LLM 调用是长耗时外部依赖，不设上限时一次流量高峰会把连接池与内存打满。
_semaphore = asyncio.Semaphore(20)
# 等全局闸的最长时间：宁可快速告诉用户「稍后再试」，也不要让请求挂在队列里等到超时。
_SEMAPHORE_TIMEOUT = 3.0
# 单用户并发上限：防止单账号（或脚本）把全局名额占满。
_USER_MAX_CONCURRENT = 2
# SSE 心跳间隔：网关 / 代理的 idle 超时通常在 30~60s，15s 一次 ping 可稳定保活。
_HEARTBEAT_INTERVAL = 15.0
_MAX_MESSAGE_LEN = 2000
# 归还并发名额的最长等待：收尾阶段不能被 Redis 抖动拖住，失败就靠 TTL 自动回收。
_SLOT_RELEASE_TIMEOUT = 2.0
# 流式生成的最大尝试次数：仅首帧前失败才重试，避免已输出内容后重复。
_STREAM_ATTEMPTS = 2
_ERROR_MSG = "AI 服务暂时开小差了，请稍后再试"
_USER_BUSY_MSG = "您有正在生成的咨询回复，请等它结束后再发送"
_APPROVAL_EXPIRED_MSG = "没有待审批的操作，可能已处理或已过期"
_APPROVAL_STALE_MSG = "审批已失效，请重新发起该操作"

# SSE 响应头：两个流式接口共用，避免只改一处导致 Nginx 缓冲又把流变成「一次性返回」。
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    # 关掉 Nginx 的响应缓冲：否则 SSE 会被攒够一个 buffer 才下发，流式效果消失。
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

# 持有后台记忆抽取任务的强引用：asyncio 只保留弱引用，不留引用可能在任务完成前被 GC 回收。
_bg_memory_tasks: set = set()


def _sse(data: dict) -> str:
    """把字典编码为一帧 SSE 数据。

    Args:
        data: 事件体（须含 ``type``）。

    Returns:
        形如 ``data: {...}\\n\\n`` 的帧文本；``ensure_ascii=False`` 保证中文不被转义成
        ``\\uXXXX``（体积约为 1/3，且便于抓包排查）。

    Raises:
        无。
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _ref_items(knowledge: list) -> list:
    """把检索结果转成前端引用面板所需的结构。

    同时做两件事：snake_case → camelCase（前端约定）与字段裁剪（只给展示需要的字段）。

    Args:
        knowledge: ``app.agent`` 产出的知识片段列表（工具 artifact 或预检索结果）。

    Returns:
        引用项列表，每项含 rank / docId / source / docTitle / kind / section / category /
        score / matchedBy / snippet。

    Raises:
        无。
    """
    items = []
    for item in knowledge or []:
        items.append({
            "rank": item.get("rank"),
            "docId": item.get("doc_id"),
            "source": item.get("source"),
            # 文档名缺失时依次回落到来源文件名与「知识库」，避免前端出现空标题。
            "docTitle": item.get("doc_title") or item.get("source") or "知识库",
            "kind": item.get("kind") or "md",
            # 小节路径形如「文档 > 章 > 节」，面板只展示最末一级。
            "section": (item.get("section") or "").split(" > ")[-1],
            "category": item.get("category") or "",
            "score": item.get("score") or 0,
            "matchedBy": item.get("matched_by") or "",
            "snippet": item.get("snippet") or "",
        })
    return items


def _spawn_memory_extraction(user_id: int, pet_id: int, pet_name: str,
                             user_msg: str, reply: str) -> None:
    """以「发射后不管」的方式触发长期记忆抽取。

    Args:
        user_id: 用户 ID。
        pet_id: 宠物 ID。
        pet_name: 宠物名。
        user_msg: 本轮用户消息。
        reply: 本轮助手回复。

    Returns:
        None。

    Raises:
        无（任务内部自行吞异常）。
    """
    task = asyncio.create_task(
        longterm.extract_and_apply(user_id, pet_id, pet_name, user_msg, reply))
    _bg_memory_tasks.add(task)
    # 完成后自动从集合移除，否则集合会随对话量无限增长。
    task.add_done_callback(_bg_memory_tasks.discard)


def _parse_user_id(x_user_id: Optional[str]) -> Optional[int]:
    """解析网关注入的用户标识。

    Args:
        x_user_id: 请求头 ``X-User-Id`` 的原始值。

    Returns:
        正整数用户 ID；缺失、非法或非正数时返回 None（调用方统一返回「未登录」）。

    Raises:
        无。
    """
    if not x_user_id:
        return None
    try:
        user_id = int(x_user_id)
    except (TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


async def _prepare_attachments(attachments: list, user_id: int) -> list:
    """补全附件信息（当前只做音频转写）。

    必须在进入编排图之前完成：音频对纯文本模型完全不可见，只有先转成文字，
    用户「发语音提问」才能被正常回答。

    Args:
        attachments: 附件字典列表（原地修改）。
        user_id: 用户 ID，用于越权校验。

    Returns:
        同一个列表（已回填 ``transcript``）。

    Raises:
        无——转写失败时保持无 transcript，由提示词引导用户补充文字。
    """
    if settings.is_mock or not attachments:
        return attachments
    for att in attachments:
        if att.get("type") != media.KIND_AUDIO:
            continue
        transcript = await media.transcribe_audio(att, user_id)
        if transcript:
            att["transcript"] = transcript
    return attachments


def _attachment_enriched_text(text: str, attachments: list) -> str:
    """把用户文字与附件说明拼成「给模型看的输入」。

    Args:
        text: 用户原始文字（可为空）。
        attachments: 附件列表。

    Returns:
        拼接后的文本；无附件时原样返回用户文字。

    Raises:
        无。
    """
    parts = [text] if text else []
    if attachments:
        # 先给「发了几样东西」的概况，再给「哪些内容你看不到」的补充说明。
        summary = media.attachment_summary(attachments)
        if summary:
            parts.append(f"（{summary}）")
        describe = media.describe_for_prompt(
            attachments, settings.vision_enabled, settings.asr_enabled)
        if describe:
            parts.append(describe)
    return "\n".join(parts)


async def _resolve_session(user_id: int, req: ChatRequest) -> tuple[Optional[int], Optional[JSONResponse]]:
    """确定本轮使用的会话 ID（已有则校验归属，没有则新建）。

    Args:
        user_id: 用户 ID。
        req: 对话请求（``sessionId`` 可缺省）。

    Returns:
        ``(session_id, error_response)``：成功时第二项为 None；失败时第一项为 None，
        第二项是可直接返回给客户端的报文。

    Raises:
        无——数据库异常被捕获并转成 500 报文。
    """
    pet: PetInfo = req.pet if req.pet is not None else PetInfo()
    pet_id = pet.id if pet.id is not None else 0
    try:
        if req.sessionId is not None:
            # 用 user_id 一起查：既拿到会话，又顺手完成归属校验（他人会话查不到）。
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
    """把异步流「泵」进队列，并统一成 ``(kind, payload)`` 二元组。

    引入队列是为了让消费端可以用 ``wait_for`` 加超时——直接在 ``async for`` 上等待无法
    插入心跳，也无法在超时后继续等待同一路流。

    Args:
        stream: 异步迭代器，产出字符串（增量文本）或 ``(kind, payload)`` 元组。
        queue: 目标队列。

    Returns:
        None。

    Raises:
        无——异常被包装成 ``("error", exc)`` 放入队列，由消费端决定是否重试。
    """
    try:
        async for event in stream:
            # 字符串统一视为增量文本；元组（如 refs）原样透传。
            await queue.put(("delta", event) if isinstance(event, str) else event)
        await queue.put(("end", None))
    except Exception as exc:
        await queue.put(("error", exc))


async def _graph_stream(graph_input, config: dict):
    """执行 AgentLoop（或恢复挂起的图）并产出「对前端有意义的事件」。

    Args:
        graph_input: 首轮传初始状态字典；审批恢复时传 ``Command(resume=...)``。
        config: LangGraph 运行配置（含 thread_id）。

    Yields:
        ``("refs", [...])`` / ``("delta", str)`` / ``("approval", {...})``。

    Raises:
        Exception: 图执行失败时透传，由 ``_pump`` 转成 error 事件。

    说明：AgentLoop 内部每次模型调用都会产出 token（决策轮 / 改写轮 / 收口轮），
    只有 ``is_answer_chunk`` 认定的片段才是给用户看的正文；引用来源由 ``extract_refs``
    提取。工具里触发人工审批时，图会挂起并产出 ``__interrupt__`` 更新，这里转成 approval
    事件后立即结束——本轮不再有后续 token。
    """
    graph = get_chat_agent()
    # 同时订阅两种流：updates 用于拿节点产出（引用 / 审批），messages 用于拿 token 增量。
    async for mode, payload in graph.astream(
            graph_input, config=config, stream_mode=["updates", "messages"]):
        if mode == "updates":
            approval = extract_interrupt(payload)
            if approval:
                yield "approval", approval
                return
            refs = extract_refs(payload)
            if refs:
                yield "refs", refs
            continue

        chunk, chunk_meta = payload
        if is_answer_chunk(chunk, chunk_meta):
            yield "delta", chunk.text


def _history_messages(history: list) -> List[AnyMessage]:
    """把数据库历史转成 LangChain 消息，用于「无 checkpoint 时」回填上下文。

    Args:
        history: ``persistence.read_history`` 的结果（时间正序）。

    Returns:
        HumanMessage / AIMessage 列表；空内容的消息会被跳过。

    Raises:
        无。
    """
    messages: List[AnyMessage] = []
    for idx, item in enumerate(history):
        content = item.get("content")
        if not content:
            continue
        # 显式给每条消息一个稳定 id：LangGraph 的 add_messages 依据 id 去重，
        # 没有 id 时同内容消息可能被误判为重复而丢弃。
        msg_id = f"bf-{idx}-{item.get('ts', 0)}"
        if item.get("role") == "user":
            messages.append(HumanMessage(content=content, id=msg_id))
        elif item.get("role") == "assistant":
            messages.append(AIMessage(content=content, id=msg_id))
    return messages


async def _persist_interrupted(graph, config: dict, user_id: int, pet_id: int,
                               session_id: int, user_msg: str,
                               human_msg: HumanMessage, partial: str,
                               attachments: Optional[list] = None,
                               refs: Optional[list] = None) -> None:
    """收尾保存「被中止的一轮」。

    两处都要写：checkpoint 让被中止的轮次进入后续对话的长期记忆（否则「暂停即失忆」），
    PostgreSQL 让前端历史能回放出半截回复。

    Args:
        graph: 对话图实例（``aupdate_state`` 需要）。
        config: 该会话的运行配置。
        user_id: 用户 ID。
        pet_id: 宠物 ID。
        session_id: 会话 ID。
        user_msg: 落库用的用户消息文本（附件消息会退化为摘要文案）。
        human_msg: 送入图的消息对象（含 id 与附件描述）。
        partial: 已产出的部分回复（可能为空串）。
        attachments: 用户消息附件。
        refs: 已推送的引用来源。

    Returns:
        None。

    Raises:
        无——两处写入各自独立降级，任一失败都不影响另一处。
    """
    messages: List[AnyMessage] = [human_msg]
    if partial:
        messages.append(AIMessage(content=partial, additional_kwargs={"interrupted": True}))
    if checkpoint.available():
        try:
            # as_node="finalize"：声明这批消息是收口节点的产出（新图中该节点存在），
            # 图状态里的 messages 才会被正确归位（否则会被当作未知来源的更新）。
            await graph.aupdate_state(config, {"messages": messages}, as_node=NODE_FINALIZE)
        except Exception:
            logger.warning("中断记忆补写 checkpoint 失败(忽略): user_id=%s session_id=%s",
                           user_id, session_id, exc_info=True)
    await persistence.save_turn_if_exists(user_id, pet_id, session_id, user_msg, partial,
                                          interrupted=True, attachments=attachments, refs=refs)


async def _append_mock_turn(graph, config: dict, input_messages: List[AnyMessage], reply: str) -> None:
    """把 mock 模式的一轮问答补写进图状态。

    mock 模式不执行 AgentLoop（没有模型可供决策），若不补写，checkpoint 里就只有
    存量的旧消息——多轮「对话」会看起来像自问自答。

    Args:
        graph: 对话图实例。
        config: 运行配置。
        input_messages: 本轮送入图的消息（含回填的历史与本轮用户消息）。
        reply: 已输出的模拟回复。

    Returns:
        None。

    Raises:
        无——补写失败只记日志，不影响已经发给用户的回复。
    """
    if not checkpoint.available():
        return
    messages: List[AnyMessage] = list(input_messages)
    if reply.strip():
        messages.append(AIMessage(content=reply))
    try:
        await graph.aupdate_state(
            config,
            {"messages": messages, "reply": reply},
            as_node=NODE_FINALIZE,
        )
    except Exception:
        logger.warning("mock 轮次补写 checkpoint 失败(忽略)", exc_info=True)


def _last_human_text(snapshot) -> str:
    """从会话快照里取最近一条用户消息文本。

    Args:
        snapshot: ``graph.aget_state`` 返回的状态快照。

    Returns:
        用户消息正文；没有时为空串。

    Raises:
        无。

    用途：审批恢复时这一轮还没落库，需要靠 checkpoint 里的原始提问补写历史。
    """
    values = getattr(snapshot, "values", None) or {}
    for msg in reversed(values.get("messages") or []):
        if isinstance(msg, HumanMessage):
            return str(msg.text or "")
    return ""


async def _pending_snapshot(graph, config: dict, approval_id: str):
    """校验会话里是否存在待审批的中断。

    Args:
        graph: 对话图实例。
        config: 会话运行配置。
        approval_id: 前端回传的审批单 ID（可为空，表示为「当前挂起的那个」）。

    Returns:
        ``(snapshot, error_msg)``：成功时 ``error_msg`` 为 None；失败时 snapshot 为 None。

    Raises:
        无——读取异常被转成可直接展示的中文提示。
    """
    try:
        snapshot = await graph.aget_state(config)
    except Exception:
        logger.warning("读取审批现场失败", exc_info=True)
        return None, "读取会话状态失败，请稍后再试"

    if snapshot is None or not getattr(snapshot, "next", None):
        return None, _APPROVAL_EXPIRED_MSG

    pending = None
    for task in getattr(snapshot, "tasks", ()) or ():
        for item in getattr(task, "interrupts", ()) or ():
            value = getattr(item, "value", None)
            if isinstance(value, dict) and value.get("type") == "approval":
                pending = item
    if pending is None:
        return None, _APPROVAL_EXPIRED_MSG
    # 校验审批单：防止前端拿着上一次的审批去批准这一次的操作（重放）。
    if approval_id and str(approval_id) != str(getattr(pending, "id", "")):
        return None, _APPROVAL_STALE_MSG
    return snapshot, None


async def _approval_generator(config: dict, user_id: int, session_id: int, pet_id: int,
                              pet_name: str, user_msg: str, snapshot, approved: bool,
                              reason: str):
    """审批恢复的 SSE 生成器：从挂起点继续跑图并输出剩余内容。

    Args:
        config: 会话运行配置（thread_id 必须与挂起时一致）。
        user_id: 用户 ID。
        session_id: 会话 ID。
        pet_id: 宠物 ID（落库用）。
        pet_name: 宠物名（记忆抽取用）。
        user_msg: 本轮用户消息（从 checkpoint 取回，落库用）。
        snapshot: 挂起时的状态快照。
        approved: 是否批准。
        reason: 拒绝原因。

    Yields:
        str: 编码好的 SSE 帧（meta / refs / delta / approval / done / error）。

    Raises:
        asyncio.CancelledError: 客户端断开时向上抛，属正常收尾路径。
    """
    resume_value = {"approved": bool(approved), "reason": (reason or "")[:200], "by": user_id}

    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=_SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        yield _sse({"type": "error", "msg": _ERROR_MSG})
        return

    collected: List[str] = []
    last_refs: List[dict] = []
    paused = False
    approval_event: Optional[dict] = None
    producer: Optional[asyncio.Task] = None
    user_slot: Optional[bool] = None
    saved = False
    attachments = list((getattr(snapshot, "values", None) or {}).get("attachments") or [])

    try:
        acquired, via_redis = await limits.try_acquire(user_id, _USER_MAX_CONCURRENT)
        if not acquired:
            yield _sse({"type": "error", "msg": _USER_BUSY_MSG})
            return
        user_slot = via_redis

        yield _sse({"type": "meta", "sessionId": session_id})

        queue: asyncio.Queue = asyncio.Queue()
        producer = asyncio.create_task(
            _pump(_graph_stream(Command(resume=resume_value), config), queue))
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_INTERVAL)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if kind == "end":
                break
            if kind == "error":
                logger.warning("审批恢复执行失败: %r", payload)
                yield _sse({"type": "error", "msg": _ERROR_MSG})
                return
            if kind == "approval":
                # 恢复后又命中另一个敏感操作：再次挂起，让前端继续审批。
                paused = True
                approval_event = payload
                break
            if kind == "refs":
                last_refs = _ref_items(payload or [])
                yield _sse({"type": "refs", "items": last_refs})
                continue
            collected.append(payload)
            yield _sse({"type": "delta", "content": payload})

        if paused:
            yield _sse({"type": "approval", **(approval_event or {})})
            return

        reply = "".join(collected)
        await persistence.save_turn(user_id, pet_id, session_id, user_msg, reply,
                                    attachments=attachments, refs=last_refs)
        saved = True
        if (reply.strip() and settings.MEMORY_ENABLED and memstore.available()
                and not settings.is_mock):
            _spawn_memory_extraction(user_id, pet_id, pet_name, user_msg, reply)
        yield _sse({"type": "done"})
    except (asyncio.CancelledError, GeneratorExit):
        # 恢复过程中被中止：把已产出的部分补写进历史，避免「审批完就丢消息」。
        try:
            if not saved:
                await asyncio.shield(persistence.save_turn_if_exists(
                    user_id, pet_id, session_id, user_msg, "".join(collected),
                    interrupted=True, attachments=attachments, refs=last_refs))
        except BaseException:
            pass
        raise
    except Exception:
        logger.exception("审批恢复流出现未预期异常")
        yield _sse({"type": "error", "msg": _ERROR_MSG})
    finally:
        if producer is not None:
            producer.cancel()
        if user_slot is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(limits.release(user_id, user_slot)), timeout=_SLOT_RELEASE_TIMEOUT)
            except BaseException:
                pass
        _semaphore.release()


async def _stream_generator(req: ChatRequest, user_id: int, session_id: int, pet_id: int):
    """SSE 流式对话的核心生成器。

    整体结构：抢全局闸 → 抢用户闸 → 组装输入 → 执行图 / 模拟流 → 落库 → 释放名额。
    所有退出路径（正常、异常、客户端断开）都必须走到 ``finally`` 归还名额，
    否则并发闸会被逐步耗尽。

    Args:
        req: 对话请求。
        user_id: 用户 ID。
        session_id: 会话 ID（已解析）。
        pet_id: 宠物 ID。

    Yields:
        str: 编码好的 SSE 帧。

    Raises:
        asyncio.CancelledError: 客户端断开 / 任务被取消时向上抛出，属正常收尾路径。
        GeneratorExit: 生成器被关闭时向上抛出，同样走补写落库的收尾分支。
    """
    pet: PetInfo = req.pet if req.pet is not None else PetInfo()

    try:
        await asyncio.wait_for(_semaphore.acquire(), timeout=_SEMAPHORE_TIMEOUT)
    except asyncio.TimeoutError:
        # 没拿到信号量就直接返回，不进入下方那个带 finally 的 try 块——
        # 否则会 release 一个从未 acquire 的名额，把并发上限越放越宽。
        yield _sse({"type": "error", "msg": _ERROR_MSG})
        return

    collected = []
    last_refs = []
    appended = False
    # 本轮是否因人工审批而挂起；挂起时要把 approval 帧发出去并跳过落库。
    paused = False
    approval_event: Optional[dict] = None
    producer: Optional[asyncio.Task] = None
    user_slot: Optional[bool] = None
    graph = None
    config: Optional[dict] = None
    human_msg: Optional[HumanMessage] = None
    try:
        acquired, via_redis = await limits.try_acquire(user_id, _USER_MAX_CONCURRENT)
        if not acquired:
            yield _sse({"type": "error", "msg": _USER_BUSY_MSG})
            return
        user_slot = via_redis

        graph = get_chat_agent()
        config = checkpoint.thread_config(user_id, session_id)
        text = (req.message or "").strip()
        attachments = [a.model_dump() for a in (req.attachments or [])]
        enriched = _attachment_enriched_text(text, attachments)
        # 纯附件消息也要有内容，否则空 HumanMessage 会让模型无从下手。
        human_msg = HumanMessage(content=enriched or "（附件咨询）", id=uuid4().hex)
        # 落库文本与模型输入分开：历史里展示「图片×2」比展示整段附件说明更清爽。
        persist_msg = text or (media.attachment_summary(attachments) or "附件咨询")

        yield _sse({"type": "meta", "sessionId": session_id})

        if attachments:
            # 转写要等 OSS 下载 + ASR 调用，放在 meta 帧之后：让前端先拿到 sessionId，
            # 体感上「已经开始响应」而不是干等。
            attachments = await _prepare_attachments(attachments, user_id)
            enriched = _attachment_enriched_text(text, attachments)
            if enriched:
                # 沿用原消息 id，保证同一条消息在状态里不被视作两条。
                human_msg = HumanMessage(content=enriched, id=human_msg.id)

        input_messages: List[AnyMessage] = [human_msg]
        if checkpoint.available():
            try:
                snapshot = await graph.aget_state(config)
                has_memory = bool(snapshot and snapshot.values.get("messages"))
            except Exception:
                # 读不到状态时保守假设「有记忆」：宁可少回填一次历史，
                # 也不要重复回填导致上下文里出现两份相同历史。
                logger.warning("读取对话 checkpoint 失败(本轮不回填历史): user_id=%s session_id=%s",
                               user_id, session_id, exc_info=True)
                has_memory = True
            if not has_memory:
                # 仅首次（checkpoint 为空，如历史会话迁库后）才从业务表回填，
                # 否则会与 checkpoint 里已有的消息重复。
                history = await persistence.read_history(
                    user_id, session_id, limit=settings.HISTORY_MAX_MESSAGES)
                input_messages = _history_messages(history) + input_messages

        # 检索查询优先用文字；纯语音提问则用转写结果拼成查询。
        query = text
        if not query:
            query = "；".join(a.get("transcript") or "" for a in attachments).strip("；")
        state = initial_state(
            query=query,
            pet=pet.model_dump(),
            user_id=user_id,
            messages=input_messages,
            attachments=attachments,
        )

        if settings.is_mock:
            # mock 模式没有模型可供决策，直接按关键词判定的结果做一次检索，
            # 保证前端在无 Key 环境下也能看到引用面板。
            mock_refs = await preview_knowledge(state)
            if mock_refs:
                last_refs = _ref_items(mock_refs)
                yield _sse({"type": "refs", "items": last_refs})

        queue: asyncio.Queue = asyncio.Queue()
        for attempt in range(_STREAM_ATTEMPTS):
            if settings.is_mock:
                stream = llm.mock_stream(human_msg.content, pet.name or "")
            else:
                # 重试时重新调用 _graph_stream（而非复用旧生成器）：旧生成器多半已处于异常状态。
                stream = _graph_stream(state, config)
            producer = asyncio.create_task(_pump(stream, queue))
            retryable = False
            while True:
                try:
                    kind, payload = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    # SSE 注释行（以冒号开头）不会被 EventSource 当作事件，纯心跳。
                    yield ": ping\n\n"
                    continue
                if kind == "end":
                    break
                if kind == "error":
                    # 只在「还没输出过任何内容」时重试：已经吐字后重试会让用户看到重复答案。
                    if not collected and attempt < _STREAM_ATTEMPTS - 1:
                        logger.warning("LLM 流首帧前失败，自动重试（第 %s 次）: %r",
                                       attempt + 1, payload)
                        retryable = True
                        break
                    logger.warning("LLM 流生成失败: %r", payload)
                    yield _sse({"type": "error", "msg": _ERROR_MSG})
                    return
                if kind == "approval":
                    # 敏感操作触发了人工审批：本轮挂起，等前端调用 /chat/approve 再继续。
                    paused = True
                    approval_event = payload
                    break
                if kind == "refs":
                    last_refs = _ref_items(payload or [])
                    yield _sse({"type": "refs", "items": last_refs})
                    continue
                collected.append(payload)
                yield _sse({"type": "delta", "content": payload})
            if paused or not retryable:
                break

        if paused:
            # 挂起不等于结束：不写 done、不落库（这一轮还没产生最终答复），
            # 现场已由 checkpoint 保存，前端审批后调 /chat/approve 从断点继续。
            yield _sse({"type": "approval", **(approval_event or {})})
            return

        reply = "".join(collected)
        if settings.is_mock and reply.strip():
            await _append_mock_turn(graph, config, input_messages, reply)
        await persistence.save_turn(user_id, pet_id, session_id, persist_msg, reply,
                                    attachments=attachments, refs=last_refs)
        # 落库成功后才置位：中断路径据此判断「是否还需要补写」。
        appended = True
        if (not settings.is_mock and reply.strip()
                and settings.MEMORY_ENABLED and memstore.available()):
            # 记忆抽取要再调一次 LLM，放后台避免用户等 done 帧。
            _spawn_memory_extraction(user_id, pet_id, pet.name or "",
                                     human_msg.content, reply)
        yield _sse({"type": "done"})
    except (asyncio.CancelledError, GeneratorExit):
        # 客户端断开 / 用户点「停止生成」：先把已有的半截内容补写两处存储，再向上抛。
        try:
            if not appended and graph is not None and human_msg is not None:
                partial = "".join(collected)
                # shield：此时当前任务正在被取消，不 shield 的话 await 会立刻再次被中断，
                # 收尾写入就白做了。
                await asyncio.shield(_persist_interrupted(
                    graph, config, user_id, pet_id, session_id, persist_msg,
                    human_msg, partial, attachments=attachments, refs=last_refs))
        except BaseException:
            # 收尾失败不能再抛，否则会盖掉原始的取消语义。
            pass
        raise
    except Exception:
        logger.exception("对话流生成出现未预期异常")
        yield _sse({"type": "error", "msg": _ERROR_MSG})
    finally:
        if producer is not None:
            # 生产者可能仍在拉取 LLM 流，必须取消，否则连接会被泄漏到流自然结束。
            producer.cancel()
        if user_slot is not None:
            try:
                # 归还失败不该影响响应收尾，超时后由 Redis 键 TTL 兜底回收。
                await asyncio.wait_for(
                    asyncio.shield(limits.release(user_id, user_slot)), timeout=_SLOT_RELEASE_TIMEOUT)
            except BaseException:
                pass
        _semaphore.release()


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """流式对话（SSE）。

    参数校验在进入流之前完成：一旦返回 ``StreamingResponse``，就无法再改状态码 / 报文，
    因此「未登录 / 空消息 / 超长 / 超附件数 / 用户忙」都必须提前拦掉。

    Args:
        req: 对话请求体。
        x_user_id: 网关注入的用户 ID 请求头。

    Returns:
        正常情况返回 ``text/event-stream`` 流；参数或鉴权问题返回统一业务报文。

    Raises:
        无。
    """
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))

    if not (req.message or "").strip() and not req.attachments:
        return JSONResponse(status_code=200, content=fail(400, "参数错误"))
    if len(req.message or "") > _MAX_MESSAGE_LEN:
        return JSONResponse(status_code=200, content=fail(400, f"消息过长，请精简到 {_MAX_MESSAGE_LEN} 字以内"))
    if len(req.attachments) > settings.CHAT_MAX_ATTACHMENTS:
        return JSONResponse(status_code=200, content=fail(
            400, f"单条消息最多携带 {settings.CHAT_MAX_ATTACHMENTS} 个附件"))

    # 提前判断「忙」：省掉一次会话创建与流对象构造，也避免用户看到 meta 帧后才被拒绝。
    if await limits.is_busy(user_id, _USER_MAX_CONCURRENT):
        return JSONResponse(status_code=200, content=fail(429, _USER_BUSY_MSG))

    pet: PetInfo = req.pet if req.pet is not None else PetInfo()
    pet_id = pet.id if pet.id is not None else 0
    session_id, err = await _resolve_session(user_id, req)
    if err is not None:
        return err

    return StreamingResponse(
        _stream_generator(req, user_id, session_id, pet_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/chat/approve")
async def approve_chat(
    req: ApprovalRequest,
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """人工审批敏感操作，并从挂起点继续这一轮对话（SSE）。

    前端收到 ``approval`` 帧后弹审批卡片；用户点「同意 / 拒绝」时调用本接口，
    服务端用 ``Command(resume=...)`` 恢复图，继续输出剩余回答。

    Args:
        req: 审批请求（会话 ID、审批单 ID、是否批准、拒绝原因）。
        x_user_id: 网关注入的用户 ID。

    Returns:
        正常情况返回 ``text/event-stream`` 流；参数 / 鉴权 / 审批失效返回统一业务报文。

    Raises:
        无。
    """
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))

    try:
        session = await persistence.get_session(user_id, req.sessionId)
    except Exception:
        logger.warning("审批时查询会话失败: user_id=%s sessionId=%s", user_id, req.sessionId, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if session is None:
        return JSONResponse(status_code=200, content=fail(404, "会话不存在或已删除"))

    graph = get_chat_agent()
    config = checkpoint.thread_config(user_id, req.sessionId)
    snapshot, err = await _pending_snapshot(graph, config, req.approvalId)
    if err is not None:
        return JSONResponse(status_code=200, content=fail(409, err))

    values = getattr(snapshot, "values", None) or {}
    pet = values.get("pet") or {}
    try:
        pet_id = int(pet.get("id") or 0)
    except (TypeError, ValueError):
        pet_id = 0

    return StreamingResponse(
        _approval_generator(
            config=config,
            user_id=user_id,
            session_id=req.sessionId,
            pet_id=pet_id,
            pet_name=str(pet.get("name") or ""),
            user_msg=_last_human_text(snapshot),
            snapshot=snapshot,
            approved=bool(req.approved),
            reason=req.reason or "",
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/chat/upload")
async def upload_chat_attachment(
    file: UploadFile = File(..., description="附件文件（图片/音频/视频）"),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """上传对话附件，返回可随消息回传的附件描述。

    Args:
        file: 上传文件（图片 / 音频 / 视频）。
        x_user_id: 网关注入的用户 ID。

    Returns:
        ``ok({type, url, key, mime, name, size})``；失败时返回对应业务错误码。

    Raises:
        无。
    """
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    try:
        attachment = await media.save_upload(file.filename, file.content_type, file.read, user_id)
    except media.UploadError as exc:
        # 用户输入问题（类型 / 大小），原因可原样展示。
        return JSONResponse(status_code=200, content=fail(400, str(exc)))
    except oss.OssError as exc:
        logger.warning("附件上传失败: user_id=%s filename=%s", user_id, file.filename, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, str(exc)))
    except Exception:
        logger.warning("保存附件失败: user_id=%s filename=%s", user_id, file.filename, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, "附件上传失败，请稍后再试"))
    return JSONResponse(status_code=200, content=ok(attachment))


@router.get("/chat/history")
async def get_history(
    sessionId: Optional[int] = Query(default=None),
    x_user_id: Optional[str] = Header(default=None, alias="X-User-Id"),
):
    """查询会话历史（时间正序，助手消息带引用来源）。

    Args:
        sessionId: 会话 ID（必填）。
        x_user_id: 网关注入的用户 ID。

    Returns:
        ``ok(HistoryData)``；会话不存在或不属于当前用户时返回 404 报文。

    Raises:
        无。
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
    """列出用户的全部会话（跨宠物，最近活跃在前）。

    Args:
        x_user_id: 网关注入的用户 ID。

    Returns:
        ``ok(SessionListData)``。

    Raises:
        无。
    """
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
    """列出用户的长期记忆（用户级 + 宠物级，最近更新在前）。

    Args:
        x_user_id: 网关注入的用户 ID。

    Returns:
        ``ok(MemoryListData)``。

    Raises:
        无。
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
    """删除一条长期记忆。

    Args:
        key: 记忆键。
        scope: ``user`` 或 ``pet``；决定在哪个命名空间下删除。
        petId: 宠物 ID，``scope=pet`` 时必填。
        x_user_id: 网关注入的用户 ID。

    Returns:
        ``ok(None)``；参数不合法或存储不可用时返回对应错误码。

    Raises:
        无。
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
    # 命名空间由服务端按 user_id 拼出，客户端只能指定 scope 与 petId，无法越权删他人记忆。
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
    """删除会话：同时清理消息与对话记忆。

    Args:
        session_id: 会话 ID。
        x_user_id: 网关注入的用户 ID。

    Returns:
        ``ok(None)``；会话不存在时返回 404 报文。

    Raises:
        无。
    """
    user_id = _parse_user_id(x_user_id)
    if user_id is None:
        return JSONResponse(status_code=200, content=fail(401, "未登录"))
    try:
        session = await persistence.get_session(user_id, session_id)
    except Exception:
        logger.warning("查询会话归属失败: user_id=%s sessionId=%s", user_id, session_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    if session is None:
        return JSONResponse(status_code=200, content=fail(404, "会话不存在或已删除"))

    try:
        # 两处并行删除：只删其一会留下「看得见但已经空掉」的会话，
        # 或者留下无法通过接口访问、却仍占用存储的记忆。
        await asyncio.gather(
            checkpoint.adelete_thread(user_id, session_id),
            persistence.delete_session(user_id, session_id),
        )
    except Exception:
        logger.warning("删除会话失败: user_id=%s sessionId=%s", user_id, session_id, exc_info=True)
        return JSONResponse(status_code=200, content=fail(500, _ERROR_MSG))
    return JSONResponse(status_code=200, content=ok(None))
