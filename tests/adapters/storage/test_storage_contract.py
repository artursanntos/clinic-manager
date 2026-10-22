"""Same behaviour for every Storage implementation. Firestore runs only against the emulator (opt-in)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from app.adapters.storage.base import Storage
from app.adapters.storage.memory import InMemoryStorage
from app.domain.models import (
    Appointment,
    AppointmentStatus,
    ApprovalRequest,
    ApprovalStatus,
    CancelAction,
    Conversation,
    Patient,
    WaitlistEntry,
)
from tests.helpers import mon

PHONE = "5581955554444"


@pytest.fixture(params=["memory", pytest.param("firestore", marks=pytest.mark.integration)])
async def storage(request: pytest.FixtureRequest) -> AsyncIterator[Storage]:
    if request.param == "memory":
        yield InMemoryStorage()
        return
    if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        pytest.skip("FIRESTORE_EMULATOR_HOST not set")
    from app.adapters.storage.firestore import FirestoreStorage

    yield FirestoreStorage.create(project="clinic-test", prefix=f"t{uuid.uuid4().hex[:6]}_")


async def test_patients(storage: Storage) -> None:
    assert await storage.patients.get(PHONE) is None
    await storage.patients.save(Patient(phone=PHONE, name="Ana", created_at=mon(8)))
    got = await storage.patients.get(PHONE)
    assert got is not None and got.name == "Ana" and got.created_at == mon(8)
    assert [p.phone for p in await storage.patients.list_all()] == [PHONE]


async def test_appointments(storage: Storage) -> None:
    a1 = Appointment(
        patient_phone=PHONE, patient_name="Ana", procedure_id="limpeza", start=mon(9), end=mon(10), created_at=mon(8)
    )
    a2 = Appointment(
        patient_phone=PHONE,
        patient_name="Ana",
        procedure_id="limpeza",
        start=mon(14),
        end=mon(15),
        created_at=mon(8),
        status=AppointmentStatus.CANCELLED,
    )
    for a in (a2, a1):
        await storage.appointments.save(a)
    assert [a.id for a in await storage.appointments.list_by_patient(PHONE)] == [a1.id, a2.id]
    between = await storage.appointments.list_between(mon(0), mon(23))
    assert [a.id for a in between] == [a1.id, a2.id]
    booked = await storage.appointments.list_between(mon(0), mon(23), status=AppointmentStatus.BOOKED)
    assert [a.id for a in booked] == [a1.id]
    later = await storage.appointments.list_between(mon(9, 30), mon(23))  # start in [from, to)
    assert [a.id for a in later] == [a2.id]
    got = await storage.appointments.get(a1.id)
    assert got is not None and got.start == mon(9)


async def test_conversations_round_trip_with_nested_models(storage: Storage) -> None:
    from app.domain.models import PendingAction, Slot

    conv = Conversation(phone=PHONE, updated_at=mon(8))
    conv.offer(Slot(start=mon(9), end=mon(10)), "limpeza")
    conv.pending_action = PendingAction(action=CancelAction(appointment_id="x"), summary="s", created_turn=1)
    conv.add_message("patient", "oi", mon(8))
    await storage.conversations.save(conv)
    got = await storage.conversations.get(PHONE)
    assert got is not None and got.offered_slots["H1"].slot.start == mon(9)
    assert got.pending_action is not None and got.pending_action.action.type == "cancel"
    assert [c.phone for c in await storage.conversations.list_updated_before(mon(9))] == [PHONE]
    assert await storage.conversations.list_updated_before(mon(7)) == []
    await storage.conversations.delete(PHONE)
    assert await storage.conversations.get(PHONE) is None


async def test_processed_claim_is_atomic_once(storage: Storage) -> None:
    assert await storage.processed.claim("wamid.1", mon(8)) is True
    assert await storage.processed.claim("wamid.1", mon(8)) is False
    assert await storage.processed.purge_before(mon(9)) == 1
    assert await storage.processed.claim("wamid.1", mon(10)) is True


async def test_approvals(storage: Storage) -> None:
    c1, c2 = await storage.approvals.next_code(), await storage.approvals.next_code()
    assert c2 == c1 + 1
    approval = ApprovalRequest(
        code=c1, phone=PHONE, action=CancelAction(appointment_id="x"), summary="s", reason="r", created_at=mon(8)
    )
    await storage.approvals.save(approval)
    assert [a.code for a in await storage.approvals.list_pending()] == [c1]
    approval.status = ApprovalStatus.APPROVED
    await storage.approvals.save(approval)
    assert await storage.approvals.list_pending() == []
    got = await storage.approvals.get_by_code(c1)
    assert got is not None and got.status == ApprovalStatus.APPROVED


async def test_waitlist(storage: Storage) -> None:
    older = WaitlistEntry(phone=PHONE, patient_name="Ana", procedure_id="limpeza", created_at=mon(8))
    newer = WaitlistEntry(
        phone="5581900001111", patient_name="Bia", procedure_id="avaliacao", created_at=mon(8) + timedelta(minutes=5)
    )
    inactive = WaitlistEntry(
        phone="5581900002222", patient_name="Caio", procedure_id="limpeza", created_at=mon(7), active=False
    )
    for e in (newer, inactive, older):
        await storage.waitlist.save(e)
    assert [e.id for e in await storage.waitlist.list_active()] == [older.id, newer.id]
    assert [e.id for e in await storage.waitlist.list_active("limpeza")] == [older.id]
