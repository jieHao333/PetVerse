"""长期记忆抽取引擎

每轮对话正常结束后（SSE done 之后）由 chat 路由层以后台任务调用：
  1) 从 store 读取该用户现有长期记忆（user 级 + 当前宠物级，各限 N 条）；
  2) 用 LLM 结构化输出比对「本轮问答 + 现有记忆」，产出增/改/删操作列表；
  3) 逐条应用到 store（add 用新 uuid key，update 复用原 key 并刷新时间戳）。

设计要点：
  - 防抖靠 update：LLM 能看到现有记忆清单，重复或演进的同一信息应输出
    update 复用原 key 覆盖更新，避免同一信息重复堆积；
  - 只抽取长期有效的养宠相关信息（用户偏好 / 宠物习性 / 病史等），
    一次性提问细节（如「今天体温 39 度正常吗」）不进长期记忆；
  - 全程异常静默降级：抽取失败仅记日志，不影响对话主流程；
  - mock 模式直接跳过（无真实 LLM 可用）。
"""
import logging
import time
import uuid
from typing import Optional

from app import llm, memstore
from app.config import settings
from app.schemas import MemoryUpdateResult

logger = logging.getLogger(__name__)

# 抽取时读取现有记忆的条数上限（user 级 + pet 级各一份，作为比对清单交给 LLM）
_REVIEW_LIMIT = 20
# 单条记忆内容长度上限（字符）：超长内容在应用前截断，防止异常输出污染记忆
_MAX_CONTENT_LEN = 50
# 单轮抽取产出的操作数上限：异常输出（如把整段回复拆成几十条）截断到安全量
_MAX_OPS = 8


async def _existing_lines(user_id: int, pet_id: int) -> list[tuple[str, str, str]]:
    """读取现有长期记忆清单，返回 (key, 归属标签, 内容) 列表供 LLM 比对"""
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
    """抽取本轮问答中的长期记忆并应用到 store（后台任务，异常静默）

    调用方条件：真实模式 + reply 非空 + MEMORY_ENABLED + memstore.available()；
    中断轮（partial 回复）不应调用——不完整信息易产出误导性记忆。
    """
    try:
        existing = await _existing_lines(user_id, pet_id)
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
                # 更新复用原 key：保留 createdAt 需先读旧值；读取失败则按新建处理
                if not content:
                    continue
                ns = (memstore.user_ns(user_id) if op.scope == "user"
                      else memstore.pet_ns(user_id, pet_id))
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


# ---------- 读取（管理接口用，独立于图内 recall_memory） ----------

async def list_memories(user_id: int) -> list[dict]:
    """读取该用户全部长期记忆（user 级 + 所有宠物级），转为 MemoryItem 结构返回"""
    items: list[dict] = []
    store = memstore.get_store()
    if store is None:
        return items

    def _to_item(item, scope: str, pet_id: Optional[str]) -> dict:
        value = item.value or {}
        return {
            "key": item.key,
            "scope": scope,
            "petId": pet_id,
            "petName": value.get("petName"),
            "kind": value.get("kind", "preference"),
            "content": str(value.get("content", "")),
            # datetime 转秒级时间戳（store 的 created_at / updated_at 为 datetime）
            "createdAt": int(item.created_at.timestamp()) if item.created_at else 0,
            "updatedAt": int(item.updated_at.timestamp()) if item.updated_at else 0,
        }

    # user 级
    for item in await store.asearch(memstore.user_ns(user_id), limit=200):
        items.append(_to_item(item, "user", None))
    # 全部宠物级：按宠物命名空间前缀检索，namespace 第 4 段即 petId（字符串）
    for item in await store.asearch(memstore.pet_prefix(user_id), limit=200):
        pet_id = item.namespace[3] if len(item.namespace) > 3 else "0"
        items.append(_to_item(item, "pet", pet_id))
    # 最近更新在前（前端管理弹窗展示顺序）
    items.sort(key=lambda x: x["updatedAt"], reverse=True)
    return items


def memory_enabled() -> bool:
    """长期记忆功能是否启用（总开关 + store 可用；供路由层判断）"""
    return settings.MEMORY_ENABLED and memstore.available()
