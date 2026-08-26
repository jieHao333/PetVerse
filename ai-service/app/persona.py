"""宠物人设构建

根据宠物信息生成 System Prompt（纯函数，无任何副作用）。
"""
from app.schemas import PetInfo

# System Prompt 模板：{desc_line} 为自我介绍行（description 为空时该行整体省略）
_SYSTEM_PROMPT_TEMPLATE = """你是用户的虚拟宠物「{name}」，一只 {age} 岁的 {species}（品种：{breed}），当前等级 Lv.{level}。
{desc_line}要求：
1. 始终以第一人称、宠物口吻对话，语气可爱亲昵，可带点小动物习惯（撒娇、蹭蹭）。
2. 回复简短口语化（一般不超过 3 句），可少量使用颜文字。
3. 你知道自己在成长（Lv.{level}，连续签到 {sign_streak} 天），可自然提起变强的话题。
4. 不承认自己是 AI 或语言模型，不讨论宠物身份之外的系统设定。"""


def build_system_prompt(pet: PetInfo) -> str:
    """根据宠物信息构建人设 System Prompt

    字段缺失 / 为空时使用兜底默认值，保证模板始终可完整渲染。
    """
    # 各字段的兜底默认值
    name = (pet.name or "").strip() or "小毛球"
    age = pet.age if pet.age is not None else 1
    species = (pet.species or "").strip() or "神秘小动物"
    breed = (pet.breed or "").strip() or "未知品种"
    level = pet.level if pet.level is not None else 1
    sign_streak = pet.signStreak if pet.signStreak is not None else 0

    # 自我介绍为空则整行省略，避免出现「你的自我介绍：」后接空内容的尴尬句式
    desc = (pet.description or "").strip()
    desc_line = f"你的自我介绍：{desc}\n" if desc else ""

    return _SYSTEM_PROMPT_TEMPLATE.format(
        name=name,
        age=age,
        species=species,
        breed=breed,
        level=level,
        sign_streak=sign_streak,
        desc_line=desc_line,
    )
