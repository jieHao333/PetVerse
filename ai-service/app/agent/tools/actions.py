"""用户可执行的写操作工具（加购、点赞、评论、档案维护、发布动态）。

职责：把「用户能在 App 里点的按钮」变成模型可调用的动作。与只读工具的区别不只是
HTTP 方法——写操作有副作用，必须处理**重复执行**、**越权**、**不可逆**三件事。

主要导出：
    - 购物车：``add_to_cart`` / ``update_cart_quantity`` / ``remove_from_cart``
    - 互动：``like_post`` / ``unlike_post`` / ``comment_post``
    - 发布：``publish_post``
    - 档案：``register_pet`` / ``update_pet_health``

外部依赖：``app.clients.biz.request``（保留业务错误）、``app.agent.idempotency``、
``app.agent.approval``（人工审批）、``langgraph.prebuilt.InjectedState``（取用户身份与 run_id）。

**能力边界（重要）**：AI 的电商能力止于「加入购物车」。**下单、支付、取消订单一律不开放给模型**——
交易动作必须由用户在 App 里完成。原因不是「审批不够严」，而是资金类操作压根不该由模型发起：
即使加了人工审批，模型仍可能把用户的一句「帮我买」理解成授权，而真实的付款意愿只有用户自己清楚。
AI 能做的是把商品找好、放进购物车，然后把「去结算」这一步交还给用户。

三条安全约定：
    1. **身份来自状态，不来自参数**：``user_id`` 从 ``InjectedState`` 读，模型无法指定
       「替谁操作」；下游再用 ``X-User-Id`` 做一次归属校验。
    2. **重复执行有保护**：同一轮内相同工具 + 相同参数只真正执行一次（见
       ``app.agent.idempotency``）。加购是累加语义，重复不是小事。
    3. **公开发布走人工审批**：``publish_post`` 在写库前调用 ``request_approval``，
       整张图挂起等用户确认；无 checkpointer 时直接拒绝执行。

刻意不实现的动作：下单 / 支付 / 取消订单（交易闭环留给 App）、删除动态 / 评论 / 宠物（不可逆）。
"""

import json
import logging
from typing import Annotated, Any, Dict, Literal, Optional

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from pydantic import Field

from app.agent import idempotency as idem
from app.agent.approval import ApprovalOutcome, request_approval
from app.clients import biz
from app.config import settings

logger = logging.getLogger(__name__)

# 点赞 / 评论的对象类型：0 = 圈子动态（与前端 LIKE_TARGET_SPACE / COMMENT_TARGET_SPACE 一致）。
_TARGET_SPACE = 0

# 健康档案类别 → 中文名。键必须与 PetHealthUpdateDTO 的 category 取值一致。
_HEALTH_LABELS = {
    "weight": "体重",
    "bcs": "BCS 体况评分",
    "deworming": "驱虫",
    "specialPeriod": "特殊时期",
    "vaccine": "疫苗",
    "rearingMethod": "养育方式",
    "medicalHistory": "病史",
}


def _result(ok: bool, message: str, **extra: Any) -> str:
    """构造统一的动作结果（JSON 文本）。

    Args:
        ok: 是否执行成功。
        message: 给模型看的一句话结论（可直接转述给用户）。
        **extra: 附加字段（如 cartItemId / spaceId）；值为 None 的会被丢弃。

    Returns:
        ``{"ok": bool, "message": str, ...}`` 的 JSON 字符串。

    Raises:
        无。
    """
    payload: Dict[str, Any] = {"ok": ok, "message": message}
    payload.update({k: v for k, v in extra.items() if v is not None})
    return json.dumps(payload, ensure_ascii=False)


def _fail(prefix: str, resp: dict) -> str:
    """把下游失败转成可展示的失败结果。

    Args:
        prefix: 动作名（如「加入购物车失败」）。
        resp: ``biz.request`` 的返回。

    Returns:
        ``{"ok": false, "message": "...", "code": n}``。

    Raises:
        无。
    """
    detail = str(resp.get("msg") or "").strip() or "服务暂时不可用"
    return _result(False, f"{prefix}：{detail}", code=resp.get("code"))


def _blocked(action: str, outcome: ApprovalOutcome) -> str:
    """把「审批未通过」转成给模型的结果。

    Args:
        action: 动作名，如「发布动态」。
        outcome: ``request_approval`` 的返回。

    Returns:
        ``{"ok": false, "approval": "rejected"|"unavailable", "message": "..."}``。

    Raises:
        无。

    文案里明确写「不要重试」：模型看到拒绝后常见的错误反应是换个参数再调一次，
    那等于绕过用户意愿。
    """
    if outcome.unavailable:
        return _result(False, f"人工审批通道暂不可用，{action}未执行。请引导用户到 App 内完成。",
                       approval="unavailable")
    note = f"（用户说明：{outcome.reason}）" if outcome.reason else ""
    return _result(False, f"用户未批准本次{action}，操作未执行{note}。不要重试，按用户意愿继续。",
                   approval="rejected")


def _user_id(state: Dict[str, Any]) -> int:
    """从图状态里取当前用户 ID。

    Args:
        state: 注入的图状态。

    Returns:
        正整数用户 ID；未登录时为 0。

    Raises:
        无。
    """
    try:
        return int(state.get("user_id") or 0)
    except (TypeError, ValueError):
        return 0


async def _run_action(state: Dict[str, Any], tool_name: str, method: str, service_name: str,
                      path: str, payload: Optional[dict], formatter, params: Optional[dict] = None) -> str:
    """执行一次写操作：身份校验 → 幂等检查 → 请求下游 → 格式化 → 记录幂等。

    Args:
        state: 注入的图状态（提供 user_id 与 run_id）。
        tool_name: 工具名（幂等指纹的一部分，同时用于审计日志）。
        method: HTTP 方法。
        service_name: Nacos 中的目标服务名。
        path: 接口路径。
        payload: 请求体；None 表示无 body。
        formatter: 把 ``biz.request`` 的返回转成结果文本的函数。
        params: 查询参数。

    Returns:
        给模型看的结果文本。

    Raises:
        无——下游异常已被 ``biz.request`` 收敛成结构体。
    """
    user_id = _user_id(state)
    if user_id <= 0:
        return _result(False, "当前未登录，无法执行该操作")

    run_id = str(state.get("run_id") or "")
    args = {"payload": payload, "params": params, "path": path}

    cached = idem.get(run_id, tool_name, args)
    if cached is not None:
        logger.info("写操作幂等命中，跳过重复执行: tool=%s user=%s", tool_name, user_id)
        return cached

    resp = await biz.request(method, service_name, path, json=payload, params=params, user_id=user_id)
    text = formatter(resp)
    logger.info("写操作: tool=%s user=%s path=%s ok=%s", tool_name, user_id, path, resp.get("ok"))
    if resp.get("ok"):
        idem.put(run_id, tool_name, args, text)
    return text


# ── 购物车 ────────────────────────────────────────────────────────────────

@tool(parse_docstring=True)
async def add_to_cart(product_id: Annotated[int, Field(gt=0)],
                      state: Annotated[dict, InjectedState],
                      quantity: Annotated[int, Field(ge=1, le=999)] = 1) -> str:
    """把指定商品加入当前用户的购物车；同一商品重复加购会在原数量上累加。

    用户明确说「加购 / 放进购物车 / 帮我把这个买了」时调用。商品 ID 必须来自
    search_products 或 get_my_cart 的返回，不要自行编造。

    Args:
        product_id: 商品 ID（正整数），取自 search_products 返回的 id 字段。
        quantity: 加购数量，1-999，默认 1。
    """
    payload = {"productId": product_id, "quantity": quantity}

    def fmt(resp: dict) -> str:
        """把加购结果转成「商品名 + 当前数量」。"""
        if not resp.get("ok"):
            return _fail("加入购物车失败", resp)
        item = resp.get("data") or {}
        name = item.get("productName") or f"商品 {product_id}"
        total = item.get("quantity")
        suffix = f"，购物车中该商品共 {total} 件" if total else ""
        return _result(True, f"已把「{name}」加入购物车{suffix}",
                       cartItemId=item.get("id"), productId=product_id, quantity=total or quantity)

    return await _run_action(state, "add_to_cart", "POST",
                             settings.SHOP_SERVICE_NAME, "/shop/cart", payload, fmt)


@tool(parse_docstring=True)
async def update_cart_quantity(cart_item_id: Annotated[int, Field(gt=0)],
                               quantity: Annotated[int, Field(ge=1, le=999)],
                               state: Annotated[dict, InjectedState]) -> str:
    """修改购物车中某条商品的数量（覆盖，不是累加）。

    用户说「把购物车里那个改成 2 件」时调用。cart_item_id 是**购物车条目 ID**，
    取自 get_my_cart 返回的 id，不是商品 ID。

    Args:
        cart_item_id: 购物车条目 ID，取自 get_my_cart 的 id 字段。
        quantity: 目标数量，1-999。
    """
    payload = {"id": cart_item_id, "quantity": quantity}

    def fmt(resp: dict) -> str:
        """把布尔结果转成一句确认。"""
        if not resp.get("ok"):
            return _fail("修改购物车数量失败", resp)
        return _result(True, f"已把购物车条目 {cart_item_id} 的数量改为 {quantity} 件",
                       cartItemId=cart_item_id, quantity=quantity)

    return await _run_action(state, "update_cart_quantity", "PUT",
                             settings.SHOP_SERVICE_NAME, "/shop/cart", payload, fmt)


@tool(parse_docstring=True)
async def remove_from_cart(cart_item_id: Annotated[int, Field(gt=0)],
                           state: Annotated[dict, InjectedState]) -> str:
    """把某条商品从购物车移除。

    用户说「从购物车删掉 / 不要这个了」时调用。cart_item_id 取自 get_my_cart 的 id。

    Args:
        cart_item_id: 购物车条目 ID，取自 get_my_cart 的 id 字段。
    """
    def fmt(resp: dict) -> str:
        """把布尔结果转成一句确认。"""
        if not resp.get("ok"):
            return _fail("移出购物车失败", resp)
        return _result(True, f"已把购物车条目 {cart_item_id} 移出购物车", cartItemId=cart_item_id)

    return await _run_action(state, "remove_from_cart", "DELETE",
                             settings.SHOP_SERVICE_NAME, f"/shop/cart/{cart_item_id}", None, fmt)


# ── 社区互动 ──────────────────────────────────────────────────────────────

@tool(parse_docstring=True)
async def like_post(post_id: Annotated[int, Field(gt=0)],
                    state: Annotated[dict, InjectedState]) -> str:
    """给社区动态点赞（幂等，已点赞再调用不会报错）。

    用户明确要求「点赞 / 赞一下这条」时调用。post_id 取自 get_hot_posts 返回的 id。

    Args:
        post_id: 动态 ID，取自 get_hot_posts 返回的 id 字段。
    """
    def fmt(resp: dict) -> str:
        """点赞接口只返回布尔值，直接给一句确认。"""
        if not resp.get("ok"):
            return _fail("点赞失败", resp)
        return _result(True, "已点赞该动态", postId=post_id)

    return await _run_action(state, "like_post", "POST",
                             settings.REMARK_SERVICE_NAME, f"/remark/like/{_TARGET_SPACE}/{post_id}", None, fmt)


@tool(parse_docstring=True)
async def unlike_post(post_id: Annotated[int, Field(gt=0)],
                      state: Annotated[dict, InjectedState]) -> str:
    """取消对社区动态的点赞（幂等）。

    用户说「取消点赞 / 别赞了」时调用。

    Args:
        post_id: 动态 ID，取自 get_hot_posts 返回的 id 字段。
    """
    def fmt(resp: dict) -> str:
        """取消点赞接口只返回布尔值，直接给一句确认。"""
        if not resp.get("ok"):
            return _fail("取消点赞失败", resp)
        return _result(True, "已取消点赞", postId=post_id)

    return await _run_action(state, "unlike_post", "DELETE",
                             settings.REMARK_SERVICE_NAME, f"/remark/like/{_TARGET_SPACE}/{post_id}", None, fmt)


@tool(parse_docstring=True)
async def comment_post(post_id: Annotated[int, Field(gt=0)], content: str,
                       state: Annotated[dict, InjectedState]) -> str:
    """在社区动态下发表一条评论（公开发布，所有用户可见）。

    只有在用户明确要求评论、并且给出了要发的内容时才调用。评论内容必须是用户的原话或
    用户明确同意的表述，**不要自行发挥或替他表达观点**。
    本工具默认不走人工审批（评论可删除、影响面小）；若业务流程要求逐条审批，
    把工具名加入 ``AGENT_APPROVAL_TOOLS`` 即可。

    Args:
        post_id: 动态 ID，取自 get_hot_posts 返回的 id 字段。
        content: 评论正文，最多 500 字，必须是用户明确给出的内容。
    """
    text = (content or "").strip()
    if not text:
        return _result(False, "评论内容为空，请先向用户确认要发布的内容")

    outcome = request_approval(
        state, "comment_post", "发布评论",
        [{"label": "动态", "value": f"#{post_id}"},
         {"label": "内容", "value": text[:80]}],
        resource=post_id,
    )
    if outcome.blocked:
        return _blocked("评论发布", outcome)

    payload = {"targetType": _TARGET_SPACE, "targetId": post_id, "content": text}

    def fmt(resp: dict) -> str:
        """评论成功返回 CommentVO，取 id 做回执。"""
        if not resp.get("ok"):
            return _fail("评论失败", resp)
        data = resp.get("data") or {}
        return _result(True, "评论已发布", commentId=data.get("id"), postId=post_id)

    return await _run_action(state, "comment_post", "POST",
                             settings.REMARK_SERVICE_NAME, "/remark/comment", payload, fmt)


@tool(parse_docstring=True)
async def publish_post(content: str, state: Annotated[dict, InjectedState],
                       title: str = "", visibility: int = 0) -> str:
    """发布一条社区动态（文字，可带标题与话题）。

    只有在用户明确要求发布、并且给出了内容时才调用。内容必须是用户的原话或明确同意的
    表述，不要替用户编造。当前不支持带图片/视频的动态——用户要发图时请引导他在 App 里发布。
    公开发布会挂起等待用户人工审批，用户拒绝则不会发布。

    Args:
        content: 动态正文，必填。
        title: 动态标题，可空，最多 100 字。
        visibility: 可见性，0-公开（默认）、1-仅好友、2-仅自己；用户没说时用 0。
    """
    text = (content or "").strip()
    if not text:
        return _result(False, "动态内容为空，请先向用户确认要发布的内容")

    scope = {0: "公开", 1: "仅好友", 2: "仅自己"}.get(int(visibility), "公开")
    summary = [{"label": "可见性", "value": scope}, {"label": "内容", "value": text[:80]}]
    if title.strip():
        summary.insert(0, {"label": "标题", "value": title.strip()[:40]})

    outcome = request_approval(state, "publish_post", "发布动态", summary)
    if outcome.blocked:
        return _blocked("动态发布", outcome)

    payload: Dict[str, Any] = {"content": text, "visibility": int(visibility)}
    if title.strip():
        payload["title"] = title.strip()

    def fmt(resp: dict) -> str:
        """发布成功返回 spaceId。"""
        if not resp.get("ok"):
            return _fail("发布动态失败", resp)
        data = resp.get("data") or {}
        return _result(True, "动态已发布", postId=data.get("spaceId"))

    return await _run_action(state, "publish_post", "POST",
                             settings.SPACE_SERVICE_NAME, "/space", payload, fmt)


# ── 宠物档案 ──────────────────────────────────────────────────────────────

@tool(parse_docstring=True)
async def register_pet(name: str, species: str, state: Annotated[dict, InjectedState],
                       adoption_date: str = "") -> str:
    """登记一只真实宠物（名称 + 物种，可选收养日期）。

    用户说「帮我登记一只猫，叫豆豆」时调用。名称与物种必须来自用户，不要自己起名。

    Args:
        name: 宠物名字，最多 50 字。
        species: 物种，如「猫」「狗」。
        adoption_date: 收养日期，格式 YYYY-MM-DD；用户没提就留空。
    """
    if not name.strip() or not species.strip():
        return _result(False, "登记宠物需要名称和物种，请先向用户确认")

    payload: Dict[str, Any] = {"name": name.strip(), "species": species.strip()}
    if adoption_date.strip():
        payload["adoptionDate"] = adoption_date.strip()

    def fmt(resp: dict) -> str:
        """登记接口返回 PetVO，取 id 做回执。"""
        if not resp.get("ok"):
            return _fail("登记宠物失败", resp)
        data = resp.get("data") or {}
        return _result(True, f"已登记宠物「{data.get('name') or name}」（{species}）",
                       petId=data.get("id"), name=data.get("name") or name)

    return await _run_action(state, "register_pet", "POST",
                             settings.PET_SERVICE_NAME, "/pet/register", payload, fmt)


@tool(parse_docstring=True)
async def update_pet_health(pet_id: Annotated[int, Field(gt=0)],
                            category: Literal["weight", "bcs", "deworming", "specialPeriod",
                                              "vaccine", "rearingMethod", "medicalHistory"],
                            value: str, state: Annotated[dict, InjectedState],
                            unit: str = "") -> str:
    """更新宠物健康档案中的某一项（每次只改一项）。

    用户说「把豆豆的体重记成 4.2kg」「疫苗记录补上猫三联」时调用。
    pet_id 取自 get_my_pets 返回的 id；不确定是哪只宠物时先问用户，不要猜。

    Args:
        pet_id: 宠物 ID，取自 get_my_pets 的 id 字段。
        category: 健康项：weight 体重 / bcs 体况评分 / deworming 驱虫 / specialPeriod 特殊时期 /
            vaccine 疫苗 / rearingMethod 养育方式 / medicalHistory 病史。
        value: 该项内容，最多 500 字；传空串表示清空该项。
        unit: 单位，如 kg、g、分；无单位可留空。
    """
    payload: Dict[str, Any] = {"petId": pet_id, "category": category, "value": value or ""}
    if unit.strip():
        payload["unit"] = unit.strip()

    label = _HEALTH_LABELS.get(category, category)

    def fmt(resp: dict) -> str:
        """更新接口返回 PetVO，回执里带上宠物名与中文类别。"""
        if not resp.get("ok"):
            return _fail(f"更新{label}失败", resp)
        data = resp.get("data") or {}
        pet_name = data.get("name") or f"宠物 {pet_id}"
        content = f"{value}{unit}".strip() or "（已清空）"
        return _result(True, f"已把{pet_name}的{label}更新为 {content}", petId=pet_id, category=category)

    return await _run_action(state, "update_pet_health", "PUT",
                             settings.PET_SERVICE_NAME, "/pet/health", payload, fmt)


