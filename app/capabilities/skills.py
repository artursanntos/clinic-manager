"""Core capability: exposes the skill catalog and the load_skill tool (progressive disclosure)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.domain.rules import ClinicRules
from app.harness.capability import BaseCapability
from app.harness.context import TurnDeps
from app.harness.skills import SkillLibrary


class SkillsCapability(BaseCapability):
    name = "skills"

    def __init__(
        self, rules: ClinicRules, config: Mapping[str, Any] | None = None, *, library: SkillLibrary | None = None
    ) -> None:
        super().__init__(rules, config)
        self.library = library or SkillLibrary()
        library_ref = self.library

        async def load_skill(ctx: RunContext[TurnDeps], name: str) -> str:
            """Carrega as instruções detalhadas de uma skill do catálogo.

            Args:
                name: nome exato da skill no catálogo.
            """
            skill = library_ref.skills.get(name.strip())
            if skill is None:
                return f"Skill '{name}' não existe. Catálogo: {', '.join(library_ref.skills) or 'vazio'}."
            ctx.deps.turn.loaded_skills.append(skill.name)
            return skill.body

        self._toolset = FunctionToolset[TurnDeps](tools=[load_skill])

    def toolset(self) -> FunctionToolset[TurnDeps] | None:
        return self._toolset if self.library.skills else None

    def instructions(self) -> str | None:
        catalog = self.library.catalog()
        if not catalog:
            return None
        return (
            "## Skills\nAntes de conduzir um fluxo, carregue a skill correspondente com load_skill(nome). "
            f"Catálogo:\n{catalog}"
        )
