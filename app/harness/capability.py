"""Capability: the unit of extension of the harness (tools + instructions + policies + events + jobs)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic_ai.toolsets import FunctionToolset

from app.domain.events import DomainEvent
from app.domain.models import DomainAction
from app.domain.rules import ClinicRules
from app.harness.context import Services, TurnDeps

# --------------------------------------------------------------------------- policies


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Deny:
    reason: str
    """Shown to the model (PT-BR) so it can explain to the patient."""


@dataclass(frozen=True)
class RequireApproval:
    reason: str
    """Shown to the dentist."""
    action: DomainAction | None = None
    """Action to approve. Defaults to the conversation's pending action."""


PolicyDecision = Allow | Deny | RequireApproval
ToolPolicy = Callable[[TurnDeps, str, Mapping[str, Any]], Awaitable[PolicyDecision | None]]
"""``(deps, tool_name, args) -> decision``. ``None`` means "no opinion"."""

# --------------------------------------------------------------------------- jobs


@dataclass
class JobResult:
    name: str
    processed: int = 0
    sent: int = 0
    skipped: int = 0
    errors: int = 0


JobHandler = Callable[[Services], Awaitable[JobResult]]


# --------------------------------------------------------------------------- capability


class BaseCapability:
    """Override only what you need. Configuration comes from ``clinic.yaml > capabilities.<name>``."""

    name: ClassVar[str]

    def __init__(self, rules: ClinicRules, config: Mapping[str, Any] | None = None) -> None:
        self.rules = rules
        self.config: dict[str, Any] = dict(config or {})

    def instructions(self) -> str | None:
        """Short, static PT-BR prompt fragment. Dynamic state goes in the orchestrator's state block."""
        return None

    def toolset(self) -> FunctionToolset[TurnDeps] | None:
        return None

    def policies(self) -> Sequence[ToolPolicy]:
        return ()

    async def on_event(self, event: DomainEvent, services: Services) -> None:
        return None

    def jobs(self) -> Mapping[str, JobHandler]:
        return {}

    def tool_names(self) -> set[str]:
        toolset = self.toolset()
        return set(toolset.tools) if toolset is not None else set()
