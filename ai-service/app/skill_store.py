"""Skill 技能包存储（标准 Agent Skills 范式）。

以磁盘上的标准技能包管理 Skill——每个技能是 ``skills/<name>/SKILL.md``，
由 YAML frontmatter（``name`` / ``description``，可选 ``enabled``）+ Markdown 正文组成，
可附 ``scripts/`` ``references/`` ``assets/`` 资源目录。``name`` 为 hyphen-case，
既是唯一标识也是目录名。本模块只做包的读写与格式校验；「元数据常驻 + 按需加载正文」
的渐进式披露由运行时（``app.agent.extensions`` + ``load_skill`` 工具）负责。
"""

import logging
import re
import shutil
from pathlib import Path
from typing import List, Optional

import yaml

from app.config import settings

logger = logging.getLogger(__name__)

# 技能名：hyphen-case（小写字母/数字，单个连字符分隔，不允许首尾或连续连字符）。
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DESC_MAX = 1024
_BODY_MAX_LINES = 500
_CONTENT_MAX_CHARS = 20000
# 标准技能包允许的资源子目录。
_RESOURCE_DIRS = ("scripts", "references", "assets")


class SkillError(ValueError):
    """技能包格式 / 操作错误（消息可直接展示给管理员）。"""


def _root() -> Path:
    return settings.skills_dir


def _validate_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise SkillError("技能名须为 hyphen-case（小写字母/数字/连字符，如 petcare-calc）")
    return name


def _skill_dir(name: str) -> Path:
    """技能目录路径（name 已经正则约束，不含路径分隔符，杜绝穿越）。"""
    name = _validate_name(name)
    root = _root().resolve()
    target = (root / name).resolve()
    if not target.is_relative_to(root):
        raise SkillError("非法技能路径")
    return target


def validate_skill(data: dict) -> None:
    """校验技能包字段约束。

    Args:
        data: {name, description, content, enabled}。

    Raises:
        SkillError: 任一约束不满足（消息可直接展示）。
    """
    _validate_name(data.get("name", ""))
    desc = (data.get("description") or "").strip()
    if not desc:
        raise SkillError("description 不能为空（须说明做什么 + 何时触发）")
    if "\n" in desc or "\r" in desc:
        raise SkillError("description 必须为单行")
    if len(desc) > _DESC_MAX:
        raise SkillError(f"description 不能超过 {_DESC_MAX} 字符")
    content = (data.get("content") or "").strip()
    if not content:
        raise SkillError("技能正文不能为空")
    if len(content) > _CONTENT_MAX_CHARS:
        raise SkillError(f"技能正文不能超过 {_CONTENT_MAX_CHARS} 字符")
    if len(content.splitlines()) > _BODY_MAX_LINES:
        raise SkillError(f"技能正文不能超过 {_BODY_MAX_LINES} 行")


def _parse_skill_md(text: str) -> tuple[dict, str]:
    """拆解 SKILL.md 为 (frontmatter 字典, 正文)。

    Returns:
        (meta, body)。

    Raises:
        SkillError: 缺少 frontmatter 或 YAML 非法。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md 缺少 YAML frontmatter（首行须为 ---）")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise SkillError("SKILL.md frontmatter 未闭合（缺少结束的 ---）")
    fm_text = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1:]).strip()
    try:
        meta = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"frontmatter YAML 解析失败：{exc}") from exc
    if not isinstance(meta, dict):
        raise SkillError("frontmatter 须为键值映射")
    return meta, body


def _render_skill_md(meta: dict, body: str) -> str:
    """渲染 SKILL.md（frontmatter + 正文）。description 强制单行（width 放宽避免折行）。"""
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False,
                        default_flow_style=False, width=4096).strip()
    return f"---\n{fm}\n---\n\n{body.strip()}\n"


def _resources_of(skill_dir: Path) -> List[str]:
    """列出技能包内存在的标准资源子目录。"""
    return [d for d in _RESOURCE_DIRS if (skill_dir / d).is_dir()]


def _to_dict(skill_dir: Path, include_content: bool) -> Optional[dict]:
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return None
    try:
        meta, body = _parse_skill_md(md.read_text(encoding="utf-8"))
    except (SkillError, OSError):
        logger.warning("跳过非法技能包: %s", skill_dir.name, exc_info=True)
        return None
    name = str(meta.get("name") or skill_dir.name)
    item = {
        "name": name,
        "description": str(meta.get("description") or ""),
        "enabled": bool(meta.get("enabled", True)),
        "bodyLines": len(body.splitlines()),
        "resources": _resources_of(skill_dir),
        "updateTime": int(md.stat().st_mtime),
    }
    if include_content:
        item["content"] = body
    return item


def list_skills() -> List[dict]:
    """全部技能包（含正文，管理页用；按名称排序）。

    Returns:
        技能列表；根目录不存在时为空列表。
    """
    root = _root()
    if not root.is_dir():
        return []
    items = []
    for child in sorted(root.iterdir()):
        if child.is_dir():
            item = _to_dict(child, include_content=True)
            if item:
                items.append(item)
    return items


def get_skill(name: str) -> Optional[dict]:
    """按名称取单个技能包（含正文与资源清单）。

    Args:
        name: 技能名（hyphen-case）。

    Returns:
        技能字典；不存在时返回 None。

    Raises:
        SkillError: 名称非法。
    """
    return _to_dict(_skill_dir(name), include_content=True)


def create_skill(data: dict) -> dict:
    """新建技能包（写 ``<name>/SKILL.md``）。

    Args:
        data: {name, description, content, enabled}。

    Returns:
        创建后的技能字典。

    Raises:
        SkillError: 格式非法或名称已存在。
        OSError: 写盘失败。
    """
    validate_skill(data)
    name = _validate_name(data["name"])
    skill_dir = _skill_dir(name)
    if skill_dir.exists():
        raise SkillError(f"技能「{name}」已存在")
    meta = {"name": name, "description": data["description"].strip(),
            "enabled": bool(data.get("enabled", True))}
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_render_skill_md(meta, data["content"]), encoding="utf-8")
    logger.info("Skill 包已创建: %s", name)
    return get_skill(name)


def update_skill(name: str, data: dict) -> Optional[dict]:
    """整体更新技能包（含启停）；改名时迁移目录。

    Args:
        name: 原技能名（路径标识）。
        data: {name(新名), description, content, enabled}。

    Returns:
        更新后的技能字典；原技能不存在时返回 None。

    Raises:
        SkillError: 格式非法或新名已被占用。
        OSError: 写盘失败。
    """
    validate_skill(data)
    old_dir = _skill_dir(name)
    if not (old_dir / "SKILL.md").is_file():
        return None
    new_name = _validate_name(data["name"])
    new_dir = _skill_dir(new_name)
    if new_name != name and new_dir.exists():
        raise SkillError(f"技能「{new_name}」已存在")
    meta = {"name": new_name, "description": data["description"].strip(),
            "enabled": bool(data.get("enabled", True))}
    if new_name != name:
        old_dir.rename(new_dir)
    (new_dir / "SKILL.md").write_text(_render_skill_md(meta, data["content"]), encoding="utf-8")
    logger.info("Skill 包已更新: %s%s", name, f" -> {new_name}" if new_name != name else "")
    return get_skill(new_name)


def delete_skill(name: str) -> bool:
    """删除技能包（整目录，含资源）。

    Returns:
        True 表示删掉了；False 表示不存在。

    Raises:
        SkillError: 名称非法。
        OSError: 删除失败。
    """
    skill_dir = _skill_dir(name)
    if not skill_dir.is_dir():
        return False
    shutil.rmtree(skill_dir)
    logger.info("Skill 包已删除: %s", name)
    return True


def list_enabled_metadata() -> List[dict]:
    """启用中技能的元数据目录（name + description，不含正文；运行时渐进式披露用）。

    Returns:
        ``[{name, description}]``；无启用技能时为空列表。
    """
    return [{"name": s["name"], "description": s["description"]}
            for s in list_skills() if s["enabled"]]


def load_body(name: str) -> Optional[str]:
    """按需加载技能正文（load_skill 工具用）。

    Returns:
        正文 Markdown；技能不存在 / 未启用 / 非法时返回 None。
    """
    try:
        item = _to_dict(_skill_dir(name), include_content=True)
    except SkillError:
        return None
    if not item or not item["enabled"]:
        return None
    return item["content"]