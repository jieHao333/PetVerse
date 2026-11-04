"""对话路由层：SSE 流式对话与会话管理。

职责：承载 ``/ai/chat/**`` 接口——流式对话（SSE）、附件上传、历史 / 会话 / 长期记忆的查询与删除。
本层只做「协议 + 并发 + 落库」，业务决策（意图、检索、生成）全部交给 ``app.graph.chat_graph``。

主要导出：
    - ``router``  FastAPI 路由（前缀 ``/ai``）

外部依赖：``app.graph.chat_graph``（编排图）、``app.persistence``（历史）、
``app.checkpoint``（对话记忆）、``app.limits``（并发闸）、``app.media`` / ``app.oss``（附件）、
``app.longterm`` / ``app.memstore``（长期记忆）。

SSE 事件协议（前端按序消费）：
    meta  → 首帧，回传 sessionId（前端据此把「新会话」变为可回访会话）
    refs  → 有知识命中时，在答案首字之前推送引用来源
    delta → 增量文本，可出现多次
    done  → 正常结束
    error → 异常结束，msg 为可直接展示的中文提示

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
from langchain_core.messages import AIMessage, AIMessageChunk, AnyMessage, HumanMessage

from app import checkpoint, limits, llm, longterm, memstore, media, oss, persistence
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
        knowledge: ``chat_graph.retrieve_knowledge`` 产出的片段列表。

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


async def _graph_stream(state: dict, config: dict):
    """执行编排图并产出「对前端有意义的事件」。

    Args:
        state: 初始图状态。
        config: LangGraph 运行配置（含 thread_id）。

    Yields:
        ``("refs", [...])`` 或 ``("delta", str)``。

    Raises:
        Exception: 图执行失败时透传，由 ``_pump`` 转成 error 事件。
    """
    graph = get_chat_graph()
    # 同时订阅两种流：updates 用于拿节点产出（引用来源），messages 用于拿 token 增量。
    async for mode, payload in graph.astream(
            state, config=config, stream_mode=["updates", "messages"]):
        if mode == "updates":
            if not isinstance(payload, dict):
                continue
            # 只为检索节点产出 refs：其它节点的更新与前端无关。
            update = payload.get("retrieve_knowledge")
            if isinstance(update, dict):
                yield "refs", update.get("knowledge") or []
            continue

        chunk, chunk_meta = payload
        # messages 流会带出图里所有 LLM 调用的 token（意图分类、查询改写、工具代理），
        # 只有 generate 节点的输出才是给用户看的答案。
        if chunk_meta and chunk_meta.get("langgraph_node") != "generate":
            continue
        if not isinstance(chunk, AIMessageChunk):
            continue
        text = chunk.text
        if text:
            yield "delta", text


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
            # as_node="generate"：声明这批消息是 generate 节点的产出，
            # 图状态里的 messages 才会被正确归位（否则会被当作未知来源的更新）。
            await graph.aupdate_state(config, {"messages": messages}, as_node="generate")
        except Exception:
            logger.warning("中断记忆补写 checkpoint 失败(忽略): user_id=%s session_id=%s",
                           user_id, session_id, exc_info=True)
    await persistence.save_turn_if_exists(user_id, pet_id, session_id, user_msg, partial,
                                          interrupted=True, attachments=attachments, refs=refs)


async def _append_mock_reply(graph, config: dict, reply: str) -> None:
    """把 mock 回复补写进图状态。

    mock 模式下 ``generate`` 节点直接返回空串（不调用模型），若不补写，
    checkpoint 里就只有用户消息、没有助手消息，多轮「对话」会看起来像自问自答。

    Args:
        graph: 对话图实例。
        config: 运行配置。
        reply: 已输出的模拟回复。

    Returns:
        None。

    Raises:
        无。
    """
    if not checkpoint.available():
        return
    try:
        await graph.aupdate_state(config,
                                  {"messages": [AIMessage(content=reply)], "reply": reply},
                                  as_node="generate")
    except Exception:
        logger.warning("mock 回复补写 checkpoint 失败(忽略)", exc_info=True)


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
    
        graph = get_chat_graph()
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
            # mock 模式下 generate 节点不产出内容，但要先跑一遍图以取得检索结果（refs）。
            try:
                values = await graph.ainvoke(state, config=config)
                mock_refs = (values or {}).get("knowledge") or []
            except Exception:
                logger.warning("mock 模式编排执行异常（忽略，继续输出 mock 回复）", exc_info=True)
                mock_refs = []
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
                if kind == "refs":
                    last_refs = _ref_items(payload or [])
                    yield _sse({"type": "refs", "items": last_refs})
                    continue
                collected.append(payload)
                yield _sse({"type": "delta", "content": payload})
            if not retryable:
                break

        reply = "".join(collected)
        if settings.is_mock and reply.strip():
            await _append_mock_reply(graph, config, reply)
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
        headers={
            "Cache-Control": "no-cache",
            # 关掉 Nginx 的响应缓冲：否则 SSE 会被攒够一个 buffer 才下发，流式效果消失。
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
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
