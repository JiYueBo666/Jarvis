from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# 匹配并提取 Markdown 文件最顶部的 --- 包裹的 YAML Front Matter 内容
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
SKILL_FILE_CREATION_GUIDE = """When creating Jarvis skill files at .jarvis/skills/<name>/SKILL.md or skills/<name>/SKILL.md, use frontmatter:
---
name: audit
description: Audit a file
user-invocable: true
---
Audit $ARGUMENTS for risky changes."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str = ""
    prompt: str = ""
    source: str = "builtin"
    skill_root: str = ""
    when_to_use: str = ""
    context: str = "inline"
    allowed_tools: tuple[str, ...] = ()
    argument_hint: str = ""
    user_invocable: bool = True
    disable_model_invocation: bool = False
    model: str = ""
    paths: tuple[str, ...] = ()
    prompt_fn: Callable[[str], str] | None = None


def discover_skills(root, home=None):
    from .skills_bundled import bundled_skills

    skills = {skill.name: skill for skill in bundled_skills()}
    search_roots = [
        (Path(home or Path.home()) / ".jarvis" / "skills", "user"),
        (Path(root) / "skills", "project"),
        (Path(root) / ".jarvis" / "skills", "project"),
    ]
    for directory, source in search_roots:
        for skill in load_skills_from_dir(directory, source=source):
            skills[skill.name] = skill
    return dict(sorted(skills.items()))


def load_skills_from_dir(skills_dir, source):
    skills_dir = Path(skills_dir).expanduser()
    if not skills_dir.exists():
        return []
    files = []
    for path in sorted(skills_dir.iterdir()):
        if path.is_dir() and (path / "SKILL.md").is_file():
            files.append(path / "SKILL.md")
        elif path.is_file() and path.suffix.lower() == ".md":
            files.append(path)
    return [skill for path in files if (skill := load_skill_file(path, source=source))]
