"""长期记忆的抽取与维护。

职责：每轮对话结束后，异步判断「本轮是否产生了值得长期记住的信息」，并把模型的判断结果
落成 add / update / delete 操作。与 ``app.memstore`` 的分工：memstore 管存储，本模块管
「记什么、怎么改」。

主要导出：
    - ``extract_and_apply``  抽取并应用记忆维护操作（后台任务调用）
    - ``list_memories``      列出用户全部记忆（用户级 + 其所有宠物级）
    - ``memory_enabled``     记忆能力总开关（配置 + 存储可用性）

外部依赖：``app.llm.astructured_invoke``、``app.memstore``、``app.schemas``。

使用示例：
    # 通常在对话流结束后作为后台任务触发，不阻塞用户拿到回复
    asyncio.create_task(longterm.extract_and_apply(user_id, pet_id, pet_name, user_msg, reply))

记忆清单会作为「既有记忆」一并发给模型，使其能判断「该新增还是该更新某条」，
避免同一事实被反复 add 成多条。
"""

import logging
import time
import uuid
from typing import Optional

from app import llm, memstore
from app.config import settings
from app.schemas import MemoryUpdateResult

logger = logging.getLogger(__name__)

# 既有记忆的回顾条数上限：太多会挤占上下文，也让模型难以判断哪条该更新。
_REVIEW_LIMIT = 20
# 单条记忆长度上限，与提示词里「50 字以内一条客观事实」的约束保持一致。
_MAX_CONTENT_LEN = 50
# 单轮最多应用的操作数：防止模型一次输出几十条把记忆刷成噪声。
_MAX_OPS = 8


async def _existing_lines(user_id: int, pet_id: int) -> list[tuple[str, str, str]]:
    """取出既有记忆清单，供模型对比去重。

    Args:
        user_id: 用户 ID。
        pet_id: 宠物 ID。

    Returns:
        ``(key, 归属标签, 内容)`` 三元组列表；store 不可用时返回空列表。

    Raises:
        无。
    """
    lines: list[tuple[str, str, str]] = []
    store = memstore.get_store()
    if store is None:
        return lines
    for ns, label in (
        (memstore.user_ns(user_id), "用户"),
        (memstore.pet_ns(user_id, pet_id), "宠物"),
    ):
        items = await store.asearch(ns, limit=_REVIEW_LIMIT)
        for item in items:
            content = (item.value or {}).get("content", "")
            if content:
                lines.append((item.key, label, str(content)))
    return lines


async def extract_and_apply(user_id: int, pet_id: int, pet_name: str,
                            user_msg: str, reply: str) -> None:
    """抽取本轮对话中的长期信息并写回存储。

    该函数设计为「可失败的后台任务」：任何异常都在内部吞掉并记日志，
    因为它跑在对话流结束之后，失败不该影响用户已经看到的回复。

    Args:
        user_id: 用户 ID。
        pet_id: 当前会话关联的宠物 ID（决定 pet 级记忆写入哪个命名空间）。
        pet_name: 宠物名，写入记忆条目以便跨宠物展示时区分。
        user_msg: 本轮用户消息（已含附件描述）。
        reply: 本轮助手回复。

    Returns:
        None。

    Raises:
        无——内部捕获全部异常。
    """
    try:
        existing = await _existing_lines(user_id, pet_id)
        # 没有既有记忆时给一句明确的占位，否则提示词里会出现空段落，模型容易忽略该段。
        existing_text = "\n".join(f"- key={k} | {label} | {c}" for k, label, c in existing) or "（暂无记忆）"

        system_prompt = (
            "你是宠物社区 AI 助手的长期记忆维护器。根据本轮对话与既有记忆清单，"
            "判断是否需要维护长期记忆，只输出操作列表。\n"
            "抽取原则：\n"
            "1. 只记录长期有效的养宠相关信息：用户偏好（预算、喂养理念、用品习惯等）、"
            "宠物习性（爱吃/怕什么、性格、作息）、宠物事实（病史、过敏、绝育状态等）；\n"
            "2. 忽略一次性提问细节（如某天体温多少、某个商品价格），忽略寒暄与常识问答；\n"
            "3. 与既有记忆重复或演进的信息输出 update（必须带原 key），不要重复 add；\n"
            "4. 既有记忆已过时或与最新表述矛盾时输出 update 或 delete；\n"
            "5. 本轮无长期有效信息时输出一个 action=none 的空操作或不输出任何操作；\n"
            "6. 每条 content 为 50 字以内的一条客观事实，不要写成段落。"
        )
        user_prompt = (
            f"当前宠物：{pet_name or '未知'}\n\n"
            f"【既有长期记忆清单】\n{existing_text}\n\n"
            f"【本轮用户提问】\n{user_msg}\n\n"
            f"【本轮助手回复】\n{reply}"
        )

        result: MemoryUpdateResult = await llm.astructured_invoke(
            MemoryUpdateResult, system_prompt, user_prompt)
        # 先滤掉 none，再截断到上限：若先截断，前面的 none 会白白占掉名额。
        ops = [op for op in result.ops if op.action != "none"][:_MAX_OPS]
        if not ops:
            return

        store = memstore.get_store()
        if store is None:
            return
        now = int(time.time())
        applied = 0
        for op in ops:
            content = (op.content or "").strip()[:_MAX_CONTENT_LEN]
            if op.action == "add":
                # add 用 uuid 作 key：同一事实若模型判断失误重复 add，至少不会覆盖别的记忆。
                if not content:
                    continue
                await store.aput(
                    memstore.user_ns(user_id) if op.scope == "user" else memstore.pet_ns(user_id, pet_id),
                    uuid.uuid4().hex,
                    {"content": content, "kind": op.kind,
                     "petName": (pet_name or None) if op.scope == "pet" else None,
                     "createdAt": now, "updatedAt": now},
                )
                applied += 1
            elif op.action == "update" and op.key:
                if not content:
                    continue
                ns = (memstore.user_ns(user_id) if op.scope == "user"
                      else memstore.pet_ns(user_id, pet_id))
                # 先读旧值：put 是整体覆盖，不读回就会丢掉 createdAt（记忆的「首次记下时间」）。
                old = await store.aget(ns, op.key)
                old_value = old.value if old else None
                await store.aput(
                    ns, op.key,
                    {"content": content, "kind": op.kind,
                     "petName": (pet_name or None) if op.scope == "pet" else None,
                     "createdAt": (old_value or {}).get("createdAt", now),
                     "updatedAt": now},
                )
                applied += 1
            elif op.action == "delete" and op.key:
                await store.adelete(
                    memstore.user_ns(user_id) if op.scope == "user" else memstore.pet_ns(user_id, pet_id),
                    op.key,
                )
                applied += 1
        if applied:
            logger.info("长期记忆已更新: user_id=%s pet_id=%s 共 %s 条操作",
                        user_id, pet_id, applied)
    except Exception:
        logger.warning("长期记忆抽取失败(忽略): user_id=%s pet_id=%s", user_id, pet_id,
                       exc_info=True)


async def list_memories(user_id: int) -> list[dict]:
    """列出用户的全部长期记忆（用户级 + 所有宠物级），按更新时间倒序。

    Args:
        user_id: 用户 ID。

    Returns:
        记忆字典列表；store 不可用时为空列表。

    Raises:
        无。
    """
    items: list[dict] = []
    store = memstore.get_store()
    if store is None:
        return items

    def _to_item(item, scope: str, pet_id: Optional[str]) -> dict:
        """把存储条目转成接口展示结构。"""
        value = item.value or {}
        return {
            "key": item.key,
            "scope": scope,
            "petId": pet_id,
            "petName": value.get("petName"),
            "kind": value.get("kind", "preference"),
            "content": str(value.get("content", "")),
            "createdAt": int(item.created_at.timestamp()) if item.created_at else 0,
            "updatedAt": int(item.updated_at.timestamp()) if item.updated_at else 0,
        }

    for item in await store.asearch(memstore.user_ns(user_id), limit=200):
        items.append(_to_item(item, "user", None))
    for item in await store.asearch(memstore.pet_prefix(user_id), limit=200):
        # 宠物记忆的命名空间是 (根, pet_mem, user_id, pet_id)，宠物 ID 固定在下标 3。
        pet_id = item.namespace[3] if len(item.namespace) > 3 else "0"
        items.append(_to_item(item, "pet", pet_id))
    items.sort(key=lambda x: x["updatedAt"], reverse=True)
    return items


def memory_enabled() -> bool:
    """长期记忆能力是否开启（配置开关 + 存储可用）。

    Returns:
        True 表示可以读写长期记忆。

    Raises:
        无。
    """
    return settings.MEMORY_ENABLED and memstore.available()
