"""System Prompt 组装。

职责：把「人设与规则」「宠物档案」「长期记忆」「参考知识」「用户真实数据」「就医护栏」
拼成一条完整的 System Prompt。所有可变内容都做成「有则插入、无则整段省略」，
避免出现空标题干扰模型。

主要导出：
    - ``build_system_prompt``  组装对话用 System Prompt

外部依赖：``app.schemas.PetInfo``（仅取字段，不触发任何 IO）。

使用示例：
    from app.persona import build_system_prompt

    prompt = build_system_prompt(
        pet=PetInfo(name="豆豆", species="猫"),
        knowledge_context=kb_context,
        medical=True,
    )

注意：这里的文案直接决定回答风格与引用格式（``[1] [2]`` 标注规则），
改动前请确认与前端「引用来源」面板的解析逻辑一致。
"""

from app.schemas import PetInfo

# 模板占位符必须与 build_system_prompt 的 format 参数一一对应；
# 模板里出现的花括号都会触发格式化，新增示例文本时注意转义。
_SYSTEM_PROMPT_TEMPLATE = """你是一名专业、友善的养宠顾问，为用户提供宠物健康、习性、喂养、行为等方面的咨询解答。
{profile_block}{memory_block}{knowledge_block}{tool_block}要求：
1. 以专业顾问的身份回答，语气自然平实，不扮演宠物、不使用撒娇或拟声等语气化表达。
2. 回答准确、有条理，必要时分点说明；涉及疾病或用药等医疗问题时，提醒用户及时咨询专业宠物医生。
3. 可结合上方宠物档案（物种、品种、年龄等）给出更有针对性的建议；档案未提供的信息，先向用户确认再作答。
4. 优先基于「参考知识」作答；采纳某条参考知识的具体结论时，在该句句末用方括号标注其序号（如 [1]、[2]，序号取自参考知识块每条开头的 [n]，一处引用最多标两个序号）；
   不要编造知识库中没有的结论，也不要为了显得有据而给常识性内容强行加引用。
5. 若「用户数据」中提供了真实的宠物 / 订单 / 购物车 / 评论信息，请结合这些真实数据作答，不要凭空猜测。
6. 若「长期记忆」中记录了用户或宠物的偏好与习性，回答时自然结合（如推荐口粮时参考其口味偏好），
   不要在回复中提及「记忆」「系统记录」等实现细节；与当前表述冲突时以用户本轮所述为准。
{medical_rule}"""

_PROFILE_TEMPLATE = """当前用户正在咨询的宠物档案：
{profile_lines}
请在回答时结合上述宠物信息给出针对性建议。
"""

_MEMORY_TEMPLATE = """用户与宠物的长期记忆（历史对话沉淀，回答时自然结合）：
{memory}
"""

_KNOWLEDGE_TEMPLATE = """参考知识（来自养宠知识库，请优先参考）：
{knowledge}
"""

_TOOL_TEMPLATE = """用户数据（来自系统真实查询，可直接引用）：
{tool_context}
"""

# 命中医疗 / 急症意图时追加：这段是合规底线，避免模型给出用药剂量等可致害建议。
_MEDICAL_RULE = """6. 【重要】用户的问题可能涉及疾病、用药或急症。请明确说明你无法替代兽医诊断，务必尽快带宠物到正规宠物医院就诊，不要仅凭线上建议自行用药。
"""

# 健康档案字段的中文标签；键名与前端 health 对象的约定一致，缺一个就会在档案里整行丢失。
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
    """组装本轮对话的 System Prompt。

    Args:
        pet: 当前咨询的宠物档案；字段全空时整段「宠物档案」都会省略。
        memory_context: 长期记忆文本（每行一条，已带 ``- [归属]`` 前缀）。
        knowledge_context: 知识库检索结果（已格式化，每条以 ``[n]`` 开头）。
        tool_context: 工具查询到的真实业务数据（宠物 / 订单 / 购物车 / 评论等）。
        medical: 是否命中医疗 / 急症意图；True 时追加就医护栏。

    Returns:
        完整的 System Prompt 字符串。

    Raises:
        无（所有输入都做了空值与类型兜底）。
    """
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
    # 优先用前端给的文字年龄（如「3 岁 2 个月」），它比整型 age 更能表达幼宠月龄。
    age_text = (pet.ageText or "").strip()
    if age_text:
        profile_lines.append(f"- 年龄：{age_text}")
    elif pet.age is not None:
        profile_lines.append(f"- 年龄：{pet.age} 岁")

    health = pet.health or {}
    for key, label in _HEALTH_LABELS.items():
        value = (health.get(key) or "").strip()
        if value:
            profile_lines.append(f"- {label}：{value}")

    # 四段上下文都是「有内容才插入」：空标题会让模型以为该段信息缺失并主动追问，
    # 反而制造出「请问您的宠物叫什么」这类无意义反问。
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
