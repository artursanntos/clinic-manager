"""Core domain models. Pure data; no I/O."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

_DIGITS = re.compile(r"\D+")


def normalize_phone(raw: str) -> str:
    """Keep digits only (WhatsApp wa_id format, e.g. ``5581999990000``)."""
    digits = _DIGITS.sub("", raw)
    if not 8 <= len(digits) <= 15:
        raise ValueError("invalid phone number")
    return digits


def mask_phone(phone: str) -> str:
    """Phone safe for logs: ``5581*****0000``."""
    if len(phone) <= 8:
        return "*" * len(phone)
    return f"{phone[:4]}{'*' * (len(phone) - 8)}{phone[-4:]}"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- scheduling primitives


class SlotKind(StrEnum):
    REGULAR = "regular"
    ENCAIXE = "encaixe"
    """Inside an encaixe window (outside the normal grid); booking may need the dentist's approval."""


class TimeInterval(_Model):
    model_config = ConfigDict(frozen=True, extra="forbid")

    start: AwareDatetime
    end: AwareDatetime

    @model_validator(mode="after")
    def _check_order(self) -> TimeInterval:
        if self.end <= self.start:
            raise ValueError("interval end must be after start")
        return self

    def overlaps(self, other: TimeInterval, *, margin: timedelta = timedelta(0)) -> bool:
        return self.start < other.end + margin and other.start < self.end + margin

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


class BusyInterval(TimeInterval):
    """A time range blocked in the dentist's calendar."""

    event_id: str | None = None


class Slot(TimeInterval):
    kind: SlotKind = SlotKind.REGULAR


# --------------------------------------------------------------------------- patients & appointments


class Patient(_Model):
    phone: str
    name: str | None = None
    opted_out: bool = False
    """Patient sent "SAIR": no recall/follow-up/marketing-like messages."""
    created_at: AwareDatetime


class AppointmentStatus(StrEnum):
    BOOKED = "booked"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class Appointment(_Model):
    id: str = Field(default_factory=new_id)
    patient_phone: str
    patient_name: str
    procedure_id: str
    start: AwareDatetime
    end: AwareDatetime
    kind: SlotKind = SlotKind.REGULAR
    status: AppointmentStatus = AppointmentStatus.BOOKED
    calendar_event_id: str | None = None
    created_at: AwareDatetime
    confirmed_at: AwareDatetime | None = None
    """Patient confirmed attendance (reminder button)."""
    reminder_sent_at: AwareDatetime | None = None
    followup_sent_at: AwareDatetime | None = None
    cancelled_at: AwareDatetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status == AppointmentStatus.BOOKED

    def as_slot(self) -> Slot:
        return Slot(start=self.start, end=self.end, kind=self.kind)


# --------------------------------------------------------------------------- pending actions (two-step confirmation)


class BookAction(_Model):
    type: Literal["book"] = "book"
    slot_id: str
    slot: Slot
    procedure_id: str
    patient_name: str


class RescheduleAction(_Model):
    type: Literal["reschedule"] = "reschedule"
    appointment_id: str
    slot_id: str
    slot: Slot


class CancelAction(_Model):
    type: Literal["cancel"] = "cancel"
    appointment_id: str


DomainAction = Annotated[BookAction | RescheduleAction | CancelAction, Field(discriminator="type")]


class PendingAction(_Model):
    action: DomainAction
    summary: str
    """Canonical PT-BR summary built by code; the agent must repeat it to the patient."""
    created_turn: int


# --------------------------------------------------------------------------- conversations


class ConversationState(StrEnum):
    BOT = "bot"
    HUMAN = "human"
    """Handed off to the dentist; the bot stays silent until she releases it."""
    WAITING_APPROVAL = "waiting_approval"


class ChatMessage(_Model):
    role: Literal["patient", "bot", "dentist", "system"]
    text: str
    at: AwareDatetime


class Conversation(_Model):
    phone: str
    state: ConversationState = ConversationState.BOT
    turn: int = 0
    """Incremented on every inbound patient message."""
    messages: list[ChatMessage] = Field(default_factory=list)
    offered_slots: dict[str, Slot] = Field(default_factory=dict)
    offered_procedure_id: str | None = None
    pending_action: PendingAction | None = None
    handoff_reason: str | None = None
    last_inbound_at: AwareDatetime | None = None
    updated_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    """For storage TTL (LGPD retention)."""

    def add_message(self, role: Literal["patient", "bot", "dentist", "system"], text: str, at: datetime) -> None:
        self.messages.append(ChatMessage(role=role, text=text, at=at))
        self.updated_at = at

    def trim(self, max_messages: int) -> None:
        if len(self.messages) > max_messages:
            self.messages = self.messages[-max_messages:]

    def within_service_window(self, now: datetime) -> bool:
        """WhatsApp 24h customer-service window: free-form messages allowed."""
        return self.last_inbound_at is not None and now - self.last_inbound_at < timedelta(hours=24)


# --------------------------------------------------------------------------- human-in-the-loop


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    FAILED = "failed"
    """Approved, but the action was no longer valid (e.g. slot taken meanwhile)."""


class ApprovalRequest(_Model):
    id: str = Field(default_factory=new_id)
    code: int
    """Short number the dentist types: ``#ok 12`` / ``#nao 12``."""
    phone: str
    patient_name: str | None = None
    action: DomainAction
    summary: str
    reason: str
    """Why approval is needed (policy name / explanation), shown to the dentist."""
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: AwareDatetime
    decided_at: AwareDatetime | None = None


# --------------------------------------------------------------------------- waitlist & audit


class WaitlistEntry(_Model):
    id: str = Field(default_factory=new_id)
    phone: str
    patient_name: str
    procedure_id: str
    period: Literal["manha", "tarde", "qualquer"] = "qualquer"
    active: bool = True
    created_at: AwareDatetime
    last_offered_at: AwareDatetime | None = None


class AuditEntry(_Model):
    id: str = Field(default_factory=new_id)
    at: AwareDatetime
    actor: Literal["bot", "dentist", "system", "patient"]
    action: str
    ref_id: str | None = None
    detail: str | None = None
    """Never contains PII."""
