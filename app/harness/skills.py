"""Skills: PT-BR markdown instructions loaded on demand (progressive disclosure). See ADR 0003.

A skill shapes how the conversation goes. It never grants permissions: invariants live in tools and policies.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    tools: tuple[str, ...] = ()
    path: Path | None = None


def parse_skill(text: str, *, path: Path | None = None) -> Skill:
    where = f" ({path})" if path else ""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise SkillError(f"skill without YAML frontmatter{where}")
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise SkillError(f"invalid frontmatter{where}")
    unknown = set(meta) - {"name", "description", "tools"}
    if unknown:
        raise SkillError(f"unknown frontmatter keys {sorted(unknown)}{where}")
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise SkillError(f"invalid or missing skill name{where}")
    if not isinstance(description, str) or not description.strip():
        raise SkillError(f"missing description{where}")
    tools = meta.get("tools") or []
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise SkillError(f"'tools' must be a list of tool names{where}")
    body = match.group(2).strip()
    if not body:
        raise SkillError(f"empty skill body{where}")
    return Skill(name=name, description=description.strip(), body=body, tools=tuple(tools), path=path)


@dataclass
class SkillLibrary:
    skills: dict[str, Skill] = field(default_factory=dict)

    @classmethod
    def load_dir(cls, directory: str | Path) -> SkillLibrary:
        root = Path(directory)
        library = cls()
        for path in sorted(root.glob("*/SKILL.md")):
            skill = parse_skill(path.read_text(encoding="utf-8"), path=path)
            if skill.name != path.parent.name:
                raise SkillError(f"skill name '{skill.name}' must match its folder '{path.parent.name}'")
            library.add(skill)
        return library

    def add(self, skill: Skill) -> None:
        if skill.name in self.skills:
            raise SkillError(f"duplicate skill: {skill.name}")
        self.skills[skill.name] = skill

    def validate_tools(self, available: Iterable[str]) -> None:
        """Fail fast at startup if a skill references a tool no active capability provides."""
        available_set = set(available)
        problems = [
            f"{s.name}: {sorted(set(s.tools) - available_set)}"
            for s in self.skills.values()
            if set(s.tools) - available_set
        ]
        if problems:
            raise SkillError("skills reference unknown tools: " + "; ".join(problems))

    def available_with(self, tools: Iterable[str]) -> SkillLibrary:
        """Only skills whose tools are all provided (skills of a disabled capability are left out)."""
        tool_set = set(tools)
        return SkillLibrary({n: s for n, s in self.skills.items() if set(s.tools) <= tool_set})

    def catalog(self) -> str:
        if not self.skills:
            return ""
        lines = [f"- {s.name}: {s.description}" for s in sorted(self.skills.values(), key=lambda s: s.name)]
        return "\n".join(lines)
