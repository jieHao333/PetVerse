"""技能正文按需加载工具（渐进式披露的第二级）。

系统提示词里只常驻技能的元数据目录（name + description）；模型判断某技能与
当前问题相关时，调用本工具传入技能名取回完整正文，再按其指令作答。这样避免把全部
技能正文每轮塞进上下文（标准 Agent Skills 的三级加载：元数据 → 正文 → 资源）。
"""

from langchain_core.tools import tool

from app import skill_store


@tool
def load_skill(name: str) -> str:
    """加载指定技能的完整指令正文，用于按该技能约定的流程与口径回答用户。

    系统提示词的「可用技能」目录列出了每个技能的名称与适用场景描述。当某个技能与
    当前问题相关时，调用本工具并传入其名称（hyphen-case，如 petcare-calc）获取正文；
    不相关的技能无需加载。一次只加载当前需要的那一个技能。

    Args:
        name: 技能名称（取自「可用技能」目录，hyphen-case）。

    Returns:
        技能的 Markdown 正文；技能不存在或未启用时返回说明文字。
    """
    body = skill_store.load_body((name or "").strip())
    if body is None:
        return f"技能「{name}」不存在或未启用，请核对「可用技能」目录中的名称。"
    return body