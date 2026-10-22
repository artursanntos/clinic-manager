"""Firestore storage (production). Install with ``uv sync --extra firestore``.

Collections (optionally prefixed): patients/{phone}, appointments/{id}, conversations/{phone},
processed_messages/{message_id}, approvals/{id}, waitlist/{id}, audit/{id}, meta/approval_counter.

LGPD retention: configure Firestore TTL policies on ``conversations.expires_at`` and
``processed_messages.expires_at`` (see deploy/README.md). ``/jobs/cleanup`` is the second safety net.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from app.adapters.storage.base import StorageError
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

PROCESSED_TTL = timedelta(days=7)


def _dump(model: BaseModel) -> dict[str, Any]:
    # python mode keeps datetimes as datetimes (Firestore Timestamps → range queries and TTL work).
    return model.model_dump(mode="python")


class _Base:
    def __init__(self, client: Any, name: str) -> None:
        self._client = client
        self._col = client.collection(name)

    @staticmethod
    def _filter(field: str, op: str, value: Any) -> Any:
        from google.cloud.firestore_v1.base_query import FieldFilter

        return FieldFilter(field, op, value)

    async def _stream(self, query: Any) -> list[dict[str, Any]]:
        return [doc.to_dict() async for doc in query.stream()]


class _Patients(_Base):
    async def get(self, phone: str) -> Patient | None:
        snap = await self._col.document(phone).get()
        return Patient.model_validate(snap.to_dict()) if snap.exists else None

    async def save(self, patient: Patient) -> None:
        await self._col.document(patient.phone).set(_dump(patient))

    async def list_all(self) -> list[Patient]:
        return [Patient.model_validate(d) for d in await self._stream(self._col)]


class _Appointments(_Base):
    async def get(self, appointment_id: str) -> Appointment | None:
        snap = await self._col.document(appointment_id).get()
        return Appointment.model_validate(snap.to_dict()) if snap.exists else None

    async def save(self, appointment: Appointment) -> None:
        await self._col.document(appointment.id).set(_dump(appointment))

    async def list_by_patient(self, phone: str) -> list[Appointment]:
        docs = await self._stream(self._col.where(filter=self._filter("patient_phone", "==", phone)))
        return sorted((Appointment.model_validate(d) for d in docs), key=lambda a: a.start)

    async def list_between(
        self, start: datetime, end: datetime, *, status: AppointmentStatus | None = None
    ) -> list[Appointment]:
        query = self._col.where(filter=self._filter("start", ">=", start)).where(filter=self._filter("start", "<", end))
        items = [Appointment.model_validate(d) for d in await self._stream(query)]
        # status filtered in memory: avoids a composite index for a tiny result set
        return sorted((a for a in items if status is None or a.status == status), key=lambda a: a.start)


class _Conversations(_Base):
    async def get(self, phone: str) -> Conversation | None:
        snap = await self._col.document(phone).get()
        return Conversation.model_validate(snap.to_dict()) if snap.exists else None

    async def save(self, conversation: Conversation) -> None:
        await self._col.document(conversation.phone).set(_dump(conversation))

    async def delete(self, phone: str) -> None:
        await self._col.document(phone).delete()

    async def list_updated_before(self, when: datetime) -> list[Conversation]:
        docs = await self._stream(self._col.where(filter=self._filter("updated_at", "<", when)))
        return [Conversation.model_validate(d) for d in docs]


class _Processed(_Base):
    async def claim(self, message_id: str, at: datetime) -> bool:
        from google.api_core.exceptions import AlreadyExists

        try:
            await self._col.document(message_id).create({"at": at, "expires_at": at + PROCESSED_TTL})
        except AlreadyExists:
            return False
        return True

    async def purge_before(self, when: datetime) -> int:
        count = 0
        async for doc in self._col.where(filter=self._filter("at", "<", when)).stream():
            await doc.reference.delete()
            count += 1
        return count


class _Approvals(_Base):
    def __init__(self, client: Any, name: str, counter_path: tuple[str, str]) -> None:
        super().__init__(client, name)
        self._counter = client.collection(counter_path[0]).document(counter_path[1])

    async def next_code(self) -> int:
        from google.cloud import firestore

        @firestore.async_transactional
        async def bump(transaction: Any) -> int:
            snap = await self._counter.get(transaction=transaction)
            value = int((snap.to_dict() or {}).get("value", 0)) + 1 if snap.exists else 1
            transaction.set(self._counter, {"value": value})
            return value

        result: int = await bump(self._client.transaction())
        return result

    async def save(self, approval: ApprovalRequest) -> None:
        await self._col.document(approval.id).set(_dump(approval))

    async def get_by_code(self, code: int) -> ApprovalRequest | None:
        docs = await self._stream(self._col.where(filter=self._filter("code", "==", code)))
        items = [ApprovalRequest.model_validate(d) for d in docs]
        return max(items, key=lambda a: a.created_at) if items else None

    async def list_pending(self) -> list[ApprovalRequest]:
        docs = await self._stream(self._col.where(filter=self._filter("status", "==", ApprovalStatus.PENDING.value)))
        return sorted((ApprovalRequest.model_validate(d) for d in docs), key=lambda a: a.code)


class _Waitlist(_Base):
    async def save(self, entry: WaitlistEntry) -> None:
        await self._col.document(entry.id).set(_dump(entry))

    async def list_active(self, procedure_id: str | None = None) -> list[WaitlistEntry]:
        docs = await self._stream(self._col.where(filter=self._filter("active", "==", True)))
        items = [WaitlistEntry.model_validate(d) for d in docs]
        return sorted(
            (e for e in items if procedure_id is None or e.procedure_id == procedure_id), key=lambda e: e.created_at
        )


class _Audit(_Base):
    async def append(self, entry: AuditEntry) -> None:
        await self._col.document(entry.id).set(_dump(entry))


class FirestoreStorage:
    def __init__(self, client: Any, prefix: str = "") -> None:
        p = prefix
        self._patients = _Patients(client, f"{p}patients")
        self._appointments = _Appointments(client, f"{p}appointments")
        self._conversations = _Conversations(client, f"{p}conversations")
        self._processed = _Processed(client, f"{p}processed_messages")
        self._approvals = _Approvals(client, f"{p}approvals", (f"{p}meta", "approval_counter"))
        self._waitlist = _Waitlist(client, f"{p}waitlist")
        self._audit = _Audit(client, f"{p}audit")

    @classmethod
    def create(cls, *, project: str | None, database: str = "(default)", prefix: str = "") -> FirestoreStorage:
        try:
            from google.cloud import firestore
        except ImportError as exc:  # pragma: no cover
            raise StorageError("install the firestore extra: uv sync --extra firestore") from exc
        return cls(firestore.AsyncClient(project=project, database=database), prefix)

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
