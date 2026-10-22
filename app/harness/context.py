"""Runtime context objects shared by the harness, tools, capabilities and jobs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.adapters.calendar.base import CalendarProvider
from app.adapters.messaging.base import MessagingChannel
from app.adapters.storage.base import Storage
from app.domain.events import DomainEvent
from app.domain.models import ApprovalRequest, Conversation, Patient
from app.domain.rules import ClinicRules
from app.services.booking import ActionOutcome, BookingService
from app.services.templates import TemplateRegistry


@dataclass
class Services:
    """Long-lived dependencies (one per process)."""

    rules: ClinicRules
    storage: Storage
    calendar: CalendarProvider
    messaging: MessagingChannel
    templates: TemplateRegistry
    booking: BookingService
    clock: Callable[[], datetime]

    def now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("clock must return timezone-aware datetimes")
        return now.astimezone(self.rules.tz)


@dataclass
class TurnState:
    """What happened during one agent turn. Filled by tools/policies, consumed by the orchestrator/validators."""

    patient_text: str = ""
    offered_slot_ids: list[str] = field(default_factory=list)
    proposed_summary: str | None = None
    handoff_reason: str | None = None
    approval: ApprovalRequest | None = None
    outcome: ActionOutcome | None = None
    events: list[DomainEvent] = field(default_factory=list)
    loaded_skills: list[str] = field(default_factory=list)
    tool_texts: list[str] = field(default_factory=list)
    """Every tool result text of this turn; the output validator uses it to check for invented times/prices."""


@dataclass
class TurnDeps:
    """PydanticAI ``deps`` for one inbound message."""

    services: Services
    patient: Patient
    conversation: Conversation
    now: datetime
    turn: TurnState = field(default_factory=TurnState)

    @property
    def rules(self) -> ClinicRules:
        return self.services.rules
