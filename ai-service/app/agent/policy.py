"""AgentLoop 的确定性护栏与上下文工程工具。

把「不能交给模型自由发挥」的少数判断收敛到一处——医疗/急症识别、是否需要知识依据、
历史窗口裁剪。这些判断**不决定流程走向**（走哪条路由由模型决定），只影响两件事：
提示词的强化措辞、以及送进模型的上下文长度。

设计取舍：为什么不在这里做意图路由？
    改造前的 ``_rule_intents`` 直接决定走哪条分支，词表一漏就整轮走错。现在词表只用来
    **补充提示词**（例如「本轮请先检索」），即使判断错了，模型仍可按自己的判断纠正——
    决策权在模型，词表只做兜底建议。
"""

import logging
from typing import List, Sequence, Tuple
from uuid import uuid4

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

logger = logging.getLogger(__name__)

# 急症词：命中最严重的几类情况，提示词里必须出现「立即就医」。
_URGENT_WORDS = ["急救", "抽搐", "呼吸困难", "中毒", "大量出血", "尿不出", "尿闭",
                 "误食", "车祸", "昏迷", "高烧", "窒息", "站不起来"]
# 疾病 / 症状 / 用药词：触发医疗护栏（不替代兽医诊断）。
_MEDICAL_WORDS = ["病", "呕吐", "腹泻", "拉稀", "发烧", "体温", "症状", "用药", "吃药",
                  "治疗", "就医", "咳嗽", "打喷嚏", "不吃", "便血", "精神差", "皮肤病",
                  "寄生虫", "藓", "耳螨"]
# 养护类词：属于知识咨询，但不需要医疗护栏。
_HEALTH_WORDS = ["健康", "体检", "体重", "疫苗", "驱虫", "评分", "bcs", "养护", "护理"]
# 明确指向「查我自己的数据 / 商城真实商品」的词：这类问题优先查业务库，而不是知识库。
_TOOL_WORDS = ["我的订单", "我的购物车", "购物车", "订单", "我的宠物", "推荐", "评论", "口碑",
               "买到", "买什么"]
# 明确的购物动作词：命中时这一轮是「办事」而不是「咨询」，不该再追加「先检索知识库」的提示。
# 少了这一组，「帮我买一款肠胃敏感的猫粮」会被判成健康咨询，模型就会去写医嘱而不是选商品。
_SHOP_WORDS = ["买", "下单", "加购", "加入购物车", "结算", "来一袋", "来一盒", "来一包", "囤",
               "入手", "有货", "库存", "多少钱", "价格", "包邮"]
# 寒暄词：不该触发任何检索。
_CHITCHAT_WORDS = ["你好", "您好", "hi", "hello", "在吗", "谢谢", "再见", "哈喽"]


def classify_hints(query: str) -> Tuple[bool, bool]:
    """从用户原句里抽取两类提示信号。

    Args:
        query: 用户本轮问题原文（可为空，例如只发了图片）。

    Returns:
        ``(medical, needs_evidence)``：
        ``medical`` 表示需要追加「及时就医」护栏；``needs_evidence`` 表示问题偏知识/健康，
        提示词里应引导模型先检索知识库。两者都只是建议，不是路由条件。
    """
    q = (query or "").lower().strip()
    if not q:
        return False, False

    medical = any(w in q for w in _URGENT_WORDS) or any(w in q for w in _MEDICAL_WORDS)
    health = any(w in q for w in _HEALTH_WORDS)
    # 工具词与购物动作词都说明「这一轮想办事」，不该再引导模型去检索知识库。
    wants_tool = any(w in q for w in _TOOL_WORDS) or any(w in q for w in _SHOP_WORDS)
    chitchat = any(w in q for w in _CHITCHAT_WORDS) or len(q) <= 2

    needs_evidence = medical or health or (not wants_tool and not chitchat)
    return medical, needs_evidence


def trim_history(messages: Sequence[AnyMessage], limit: int) -> List[AnyMessage]:
    """截取最近若干条消息作为模型输入。

    Args:
        messages: 完整消息流（可能含 AI 的 tool_calls 消息与成组的 ToolMessage）。
        limit: 最多保留的条数；``<= 0`` 表示不裁剪。

    Returns:
        裁剪后的消息列表。

    注意：不能简单取 ``messages[-limit:]``——窗口首条若落在 ToolMessage 上，它对应的
    ``AIMessage(tool_calls=...)`` 就被切掉了，OpenAI 兼容接口会直接报
    「tool message without preceding tool_calls」。因此这里从窗口头部向前补齐整组调用。
    """
    if not messages:
        return []
    if limit <= 0 or len(messages) <= limit:
        return list(messages)

    start = len(messages) - limit
    kept = list(messages[start:])
    # 向前回溯：直到窗口首条不再是 ToolMessage（即把发起调用的 AI 消息一并带上）。
    while start > 0 and kept and isinstance(kept[0], ToolMessage):
        start -= 1
        kept.insert(0, messages[start])
    return kept


# 中断补偿用的工具结果文案：模型看到它就知道「这次调用没拿到数据」，不会当成空结果。
_INTERRUPTED_TOOL_NOTE = "（该工具调用被用户中断，未返回结果。请基于已有信息作答，或告知用户需要重新查询。）"


def repair_dangling_tool_calls(messages: Sequence[AnyMessage]) -> List[AnyMessage]:
    """补齐「有调用、无结果」的工具调用，保证消息序列对接口合法。

    Returns:
        修复后的新列表（不修改入参）。

    背景：OpenAI 兼容接口要求每个 ``tool_calls`` 都必须有对应 ``tool_call_id`` 的
    ToolMessage。用户点「停止生成」时，图可能恰好停在「AI 已请求工具、工具还没返回」的
    中间态，checkpoint 里就会留下半截调用；下一轮直接送给模型会被判 400。
    这里在**构造模型输入时**做一次幂等修复，比在路由层到处补写更稳。
    """
    repaired: List[AnyMessage] = []
    index = 0
    total = len(messages)
    while index < total:
        msg = messages[index]
        repaired.append(msg)
        index += 1
        if not isinstance(msg, AIMessage):
            continue
        calls = getattr(msg, "tool_calls", None) or []
        if not calls:
            continue
        answered = set()
        lookahead = index
        while lookahead < total and isinstance(messages[lookahead], ToolMessage):
            answered.add(getattr(messages[lookahead], "tool_call_id", None))
            lookahead += 1
        for call in calls:
            call_id = call.get("id")
            if call_id in answered:
                continue
            logger.info("补齐中断的工具调用: name=%s id=%s", call.get("name"), call_id)
            repaired.append(ToolMessage(
                content=_INTERRUPTED_TOOL_NOTE,
                tool_call_id=str(call_id or uuid4().hex),
                name=str(call.get("name") or "tool"),
            ))
    return repaired
