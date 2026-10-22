"""In-memory storage. Stores deep copies so callers can't mutate persisted state by accident."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.domain.models import (
    Appointment,
    AppointmentStatus,
    ApprovalRequest,
    ApprovalStatus,
    AuditEntry,
    Conversation,
    Patient,
    WaitlistEntry,
)


def _copy[M: BaseModel](model: M) -> M:
    return model.model_copy(deep=True)


class _Patients:
    def __init__(self) -> None:
        self.data: dict[str, Patient] = {}

    async def get(self, phone: str) -> Patient | None:
        p = self.data.get(phone)
        return _copy(p) if p else None

    async def save(self, patient: Patient) -> None:
        self.data[patient.phone] = _copy(patient)

    async def list_all(self) -> list[Patient]:
        return [_copy(p) for p in self.data.values()]


class _Appointments:
    def __init__(self) -> None:
        self.data: dict[str, Appointment] = {}

    async def get(self, appointment_id: str) -> Appointment | None:
        a = self.data.get(appointment_id)
        return _copy(a) if a else None

    async def save(self, appointment: Appointment) -> None:
        self.data[appointment.id] = _copy(appointment)

    async def list_by_patient(self, phone: str) -> list[Appointment]:
        return sorted((_copy(a) for a in self.data.values() if a.patient_phone == phone), key=lambda a: a.start)

    async def list_between(
        self, start: datetime, end: datetime, *, status: AppointmentStatus | None = None
    ) -> list[Appointment]:
        return sorted(
            (_copy(a) for a in self.data.values() if start <= a.start < end and (status is None or a.status == status)),
            key=lambda a: a.start,
        )


class _Conversations:
    def __init__(self) -> None:
        self.data: dict[str, Conversation] = {}

    async def get(self, phone: str) -> Conversation | None:
        c = self.data.get(phone)
        return _copy(c) if c else None

    async def save(self, conversation: Conversation) -> None:
        self.data[conversation.phone] = _copy(conversation)

    async def delete(self, phone: str) -> None:
        self.data.pop(phone, None)

    async def list_updated_before(self, when: datetime) -> list[Conversation]:
        return [_copy(c) for c in self.data.values() if c.updated_at < when]


class _Processed:
    def __init__(self) -> None:
        self.data: dict[str, datetime] = {}

    async def claim(self, message_id: str, at: datetime) -> bool:
        if message_id in self.data:
            return False
        self.data[message_id] = at
        return True

    async def purge_before(self, when: datetime) -> int:
        old = [k for k, v in self.data.items() if v < when]
        for k in old:
            del self.data[k]
        return len(old)


class _Approvals:
    def __init__(self) -> None:
        self.data: dict[str, ApprovalRequest] = {}
        self._code = 0

    async def next_code(self) -> int:
        self._code += 1
        return self._code

    async def save(self, approval: ApprovalRequest) -> None:
        self.data[approval.id] = _copy(approval)

    async def get_by_code(self, code: int) -> ApprovalRequest | None:
        matches = [a for a in self.data.values() if a.code == code]
        return _copy(max(matches, key=lambda a: a.created_at)) if matches else None

    async def list_pending(self) -> list[ApprovalRequest]:
        return sorted(
            (_copy(a) for a in self.data.values() if a.status == ApprovalStatus.PENDING), key=lambda a: a.code
        )


class _Waitlist:
    def __init__(self) -> None:
        self.data: dict[str, WaitlistEntry] = {}

    async def save(self, entry: WaitlistEntry) -> None:
        self.data[entry.id] = _copy(entry)

    async def list_active(self, procedure_id: str | None = None) -> list[WaitlistEntry]:
        return sorted(
            (
                _copy(e)
                for e in self.data.values()
                if e.active and (procedure_id is None or e.procedure_id == procedure_id)
            ),
            key=lambda e: e.created_at,
        )


class _Audit:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    async def append(self, entry: AuditEntry) -> None:
        self.entries.append(_copy(entry))


class InMemoryStorage:
    def __init__(self) -> None:
        self._patients = _Patients()
        self._appointments = _Appointments()
        self._conversations = _Conversations()
        self._processed = _Processed()
        self._approvals = _Approvals()
        self._waitlist = _Waitlist()
        self._audit = _Audit()

    @property
    def patients(self) -> _Patients:
        return self._patients

    @property
    def appointments(self) -> _Appointments:
        return self._appointments

    @property
    def conversations(self) -> _Conversations:
        return self._conversations

    @property
    def processed(self) -> _Processed:
        return self._processed

    @property
    def approvals(self) -> _Approvals:
        return self._approvals

    @property
    def waitlist(self) -> _Waitlist:
        return self._waitlist

    @property
    def audit(self) -> _Audit:
        return self._audit
