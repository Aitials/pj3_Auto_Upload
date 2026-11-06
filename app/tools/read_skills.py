"""技能读取工具：按名称加载 app/skills 下的技能规范。

Agent 需要执行某个已封装技能时，先调用 read_skill 取得完整规范，
再按规范处理用户输入。
"""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.tools import tool

from app.config import PROJECT_ROOT

SKILLS_DIR = (PROJECT_ROOT / "app" / "skills").resolve()

# 技能名只允许字母、数字、下划线、连字符和中文，防止路径穿越。
_SKILL_NAME_RE = re.compile(r"^[0-9A-Za-z_\-\u4e00-\u9fff]+$")


def _skill_name_of(path: Path, text: str) -> str:
    """优先读 frontmatter 的 name，没有则退回文件名。"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                key, sep, value = line.partition(":")
                if sep and key.strip() == "name":
                    name = value.strip().strip("'\"")
                    if name:
                        return name
    return path.stem


def _skill_files() -> list[Path]:
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(SKILLS_DIR.rglob("*.md"))


def _available_skills() -> dict[str, Path]:
    """返回 {技能名: 文件路径}，供查找与错误提示使用。"""
    skills: dict[str, Path] = {}
    for path in _skill_files():
        try:
            text = path.read_text("utf-8-sig")
        except OSError:
            continue
        skills[_skill_name_of(path, text)] = path
    return skills


def _resolve(skill_name: str) -> tuple[str, Path] | None:
    """按技能名定位文件，支持 <name>/SKILL.md、<name>.md 与 frontmatter 匹配。"""
    name = skill_name.strip()
    if not name:
        return None
    if name.lower().endswith(".md"):
        name = name[:-3]

    if _SKILL_NAME_RE.fullmatch(name):
        for candidate in (
            SKILLS_DIR / name / "SKILL.md",
            SKILLS_DIR / f"{name}.md",
        ):
            if candidate.is_file():
                resolved = candidate.resolve()
                if resolved.is_relative_to(SKILLS_DIR):
                    return name, resolved

    for available_name, path in _available_skills().items():
        if available_name.lower() == name.lower() or path.stem.lower() == name.lower():
            return available_name, path

    return None


@tool
def read_skill(skill_name: str) -> str:
    """加载指定技能的完整执行规范。

    Args:
        skill_name: 技能名，例如 video-script-splitter；传空字符串可列出当前可用技能。
    """
    name = skill_name.strip()
    if not name:
        available = sorted(_available_skills())
        if not available:
            return "当前没有可用技能。"
        return "可用技能：\n" + "\n".join(f"- {item}" for item in available)

    found = _resolve(name)
    if found is None:
        available = sorted(_available_skills())
        hint = "、".join(available) if available else "无"
        return f"未找到技能：{name}。当前可用技能：{hint}"

    resolved_name, path = found
    try:
        text = path.read_text("utf-8-sig")
    except OSError as exc:
        return f"读取技能 {resolved_name} 失败：{exc}"
    return text
