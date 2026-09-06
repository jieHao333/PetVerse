"""咨询上下文构建

根据宠物档案信息生成 System Prompt（纯函数，无任何副作用）。

定位说明：AI 对话用于用户咨询宠物健康、习性等养宠问题，
助手以中立的养宠顾问身份回答，不扮演宠物、不使用宠物语气。
"""
from app.schemas import PetInfo

# System Prompt 模板：{profile_block} 为宠物档案块（档案字段全空时该块整体省略）
_SYSTEM_PROMPT_TEMPLATE = """你是一名专业、友善的养宠顾问，为用户提供宠物健康、习性、喂养、行为等方面的咨询解答。
{profile_block}要求：
1. 以专业顾问的身份回答，语气自然平实，不扮演宠物、不使用撒娇或拟声等语气化表达。
2. 回答准确、有条理，必要时分点说明；涉及疾病或用药等医疗问题时，提醒用户及时咨询专业宠物医生。
3. 可结合上方宠物档案（物种、品种、年龄等）给出更有针对性的建议；档案未提供的信息，先向用户确认再作答。"""

# 宠物档案块模板（仅拼入非空字段）
_PROFILE_TEMPLATE = """当前用户正在咨询的宠物档案：
{profile_lines}
请在回答时结合上述宠物信息给出针对性建议。
"""


def build_system_prompt(pet: PetInfo) -> str:
    """根据宠物信息构建养宠顾问 System Prompt

    字段缺失 / 为空时对应档案行整体省略；全部缺失时档案块省略，
    保证模板始终可完整渲染。
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

    # 档案全空则整块省略，避免出现只有标题没有内容的尴尬段落
    profile_block = (
        _PROFILE_TEMPLATE.format(profile_lines="\n".join(profile_lines))
        if profile_lines else ""
    )

    return _SYSTEM_PROMPT_TEMPLATE.format(profile_block=profile_block)
