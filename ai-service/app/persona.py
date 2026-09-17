"""咨询上下文构建

根据宠物档案信息 + RAG 知识检索结果 + 工具调用结果生成 System Prompt（纯函数，无副作用）。

定位说明：AI 对话用于用户咨询宠物健康、习性等养宠问题，
助手以中立的养宠顾问身份回答，不扮演宠物、不使用宠物语气。

LangGraph 编排中，本模块负责最终的 Prompt 组装（compose 节点调用）：
  - 长期记忆块：runtime store 沉淀的用户 / 宠物偏好与习性（跨会话，见 app/memstore.py）；
  - 宠物档案块：来自前端传入的宠物信息；
  - 知识块：RAG 检索到的养宠知识（带来源，供回答引用、降低幻觉）；
  - 业务块：Agent 工具查到的用户真实数据（宠物 / 订单 / 购物车 / 评论等）；
  - 医疗块：命中医疗意图时，强制附加就医免责话术（安全护栏）。
"""
from app.schemas import PetInfo

# 基础人设与回答要求
_SYSTEM_PROMPT_TEMPLATE = """你是一名专业、友善的养宠顾问，为用户提供宠物健康、习性、喂养、行为等方面的咨询解答。
{profile_block}{memory_block}{knowledge_block}{tool_block}要求：
1. 以专业顾问的身份回答，语气自然平实，不扮演宠物、不使用撒娇或拟声等语气化表达。
2. 回答准确、有条理，必要时分点说明；涉及疾病或用药等医疗问题时，提醒用户及时咨询专业宠物医生。
3. 可结合上方宠物档案（物种、品种、年龄等）给出更有针对性的建议；档案未提供的信息，先向用户确认再作答。
4. 优先基于「参考知识」作答；引用知识时可简要注明来源，不要编造知识库中没有的结论。
5. 若「用户数据」中提供了真实的宠物 / 订单 / 购物车 / 评论信息，请结合这些真实数据作答，不要凭空猜测。
6. 若「长期记忆」中记录了用户或宠物的偏好与习性，回答时自然结合（如推荐口粮时参考其口味偏好），
   不要在回复中提及「记忆」「系统记录」等实现细节；与当前表述冲突时以用户本轮所述为准。
{medical_rule}"""

# 宠物档案块模板（仅拼入非空字段）
_PROFILE_TEMPLATE = """当前用户正在咨询的宠物档案：
{profile_lines}
请在回答时结合上述宠物信息给出针对性建议。
"""

# 长期记忆块模板（runtime store 沉淀的跨会话记忆；recall_memory 节点已渲染为 bullet 行）
_MEMORY_TEMPLATE = """用户与宠物的长期记忆（历史对话沉淀，回答时自然结合）：
{memory}
"""

# 知识块模板
_KNOWLEDGE_TEMPLATE = """参考知识（来自养宠知识库，请优先参考）：
{knowledge}
"""

# 业务数据块模板
_TOOL_TEMPLATE = """用户数据（来自系统真实查询，可直接引用）：
{tool_context}
"""

# 医疗安全护栏：命中医疗 / 急症意图时追加
_MEDICAL_RULE = """6. 【重要】用户的问题可能涉及疾病、用药或急症。请明确说明你无法替代兽医诊断，务必尽快带宠物到正规宠物医院就诊，不要仅凭线上建议自行用药。
"""

# 健康信息类别键 -> 中文标签（与前端身份卡健康模块 / pet 表字段一致）
_HEALTH_LABELS = {
    "weight": "体重",
    "bcs": "BCS体况评分",
    "deworming": "驱虫",
    "specialPeriod": "特殊时期",
    "vaccine": "疫苗",
    "rearingMethod": "养育方式",
    "medicalHistory": "病史",
}


def build_system_prompt(pet: PetInfo, memory_context: str = "",
                        knowledge_context: str = "", tool_context: str = "",
                        medical: bool = False) -> str:
    """根据长期记忆、宠物信息、RAG 知识与工具结果构建养宠顾问 System Prompt

    各块内容为空时对应模板块整体省略，保证模板始终可完整渲染。
    """
    # 各档案字段：为空则跳过该行
    profile_lines = []
    name = (pet.name or "").strip()
    if name:
        profile_lines.append(f"- 名字：{name}")
    species = (pet.species or "").strip()
    if species:
        profile_lines.append(f"- 物种：{species}")
    breed = (pet.breed or "").strip()
    if breed:
        profile_lines.append(f"- 品种：{breed}")
    if pet.age is not None:
        profile_lines.append(f"- 年龄：{pet.age} 岁")

    # 健康信息（猫/狗身份卡维护）：仅拼入非空项，供顾问结合健康状况作答
    health = pet.health or {}
    for key, label in _HEALTH_LABELS.items():
        value = (health.get(key) or "").strip()
        if value:
            profile_lines.append(f"- {label}：{value}")

    # 档案全空则整块省略，避免出现只有标题没有内容的尴尬段落
    profile_block = (
        _PROFILE_TEMPLATE.format(profile_lines="\n".join(profile_lines))
        if profile_lines else ""
    )
    memory_block = (
        _MEMORY_TEMPLATE.format(memory=memory_context.strip())
        if memory_context and memory_context.strip() else ""
    )
    knowledge_block = (
        _KNOWLEDGE_TEMPLATE.format(knowledge=knowledge_context.strip())
        if knowledge_context and knowledge_context.strip() else ""
    )
    tool_block = (
        _TOOL_TEMPLATE.format(tool_context=tool_context.strip())
        if tool_context and tool_context.strip() else ""
    )

    return _SYSTEM_PROMPT_TEMPLATE.format(
        profile_block=profile_block,
        memory_block=memory_block,
        knowledge_block=knowledge_block,
        tool_block=tool_block,
        medical_rule=_MEDICAL_RULE if medical else "",
    )
