"""Scheduled jobs (reminders, cleanup, recall, follow-up) and the waitlist reaction to cancellations."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.capabilities import CapabilityConfigError, build_capabilities
from app.domain.models import Appointment, AppointmentStatus, Conversation, Patient, WaitlistEntry
from tests.conftest import PATIENT, HarnessFactory
from tests.helpers import ScriptModel, at, echo_after, echo_summary, make_rules, mon

OTHER = "5581911112222"


def appt(**kw: object) -> Appointment:
    data: dict[str, object] = {
        "patient_phone": PATIENT,
        "patient_name": "Maria Silva",
        "procedure_id": "limpeza",
        "start": mon(9),
        "end": mon(10),
        "created_at": at(2026, 10, 1, 9),
    }
    data.update(kw)
    return Appointment.model_validate(data)


def templates_to(h: object, phone: str, name: str) -> list[object]:
    return [m for m in h.messaging.to(phone) if m.template and m.template.name == name]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- registry


def test_unknown_capability_in_config_fails() -> None:
    with pytest.raises(CapabilityConfigError):
        build_capabilities(make_rules(capabilities={"teleport": {"enabled": True}}))


def test_all_jobs_are_registered(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model)
    assert set(h.container.jobs) == {"reminders", "cleanup", "recall", "followup", "daily"}


# --------------------------------------------------------------------------- reminders


async def test_reminders_once_per_appointment(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model, now=at(2026, 10, 25, 9))  # Sunday; appointment Monday
    await h.storage.appointments.save(appt())
    await h.storage.appointments.save(appt(start=mon(14), end=mon(15), status=AppointmentStatus.CANCELLED))
    result = await h.container.jobs["reminders"](h.services)
    assert (result.sent, result.processed) == (1, 1)
    (msg,) = templates_to(h, PATIENT, "lembrete_consulta")
    assert msg.template.button_payloads[0].startswith("CONFIRM:")  # type: ignore[attr-defined]
    assert "segunda-feira, 26/10, às 09:00" in msg.text  # type: ignore[attr-defined]
    again = await h.container.jobs["reminders"](h.services)
    assert (again.sent, again.skipped) == (0, 1)


async def test_reminder_confirm_button(make_harness: HarnessFactory) -> None:
    script = ScriptModel()
    h = make_harness(script.model, now=at(2026, 10, 25, 9))
    a = appt()
    await h.storage.appointments.save(a)
    replies = await h.send(button=f"CONFIRM:{a.id}")
    assert "Presença confirmada" in replies[0] and script.calls == 0
    stored = await h.storage.appointments.get(a.id)
    assert stored is not None and stored.confirmed_at is not None


async def test_reminder_reschedule_button_goes_to_agent(make_harness: HarnessFactory) -> None:
    script = ScriptModel("Claro! Vamos remarcar.")
    h = make_harness(script.model, now=at(2026, 10, 25, 9))
    a = appt()
    await h.storage.appointments.save(a)
    await h.send(button=f"RESCHEDULE:{a.id}")
    assert script.calls == 1


# --------------------------------------------------------------------------- cleanup (LGPD)


async def test_cleanup_deletes_stale_conversations(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model, now=at(2027, 3, 1, 9))
    await h.storage.conversations.save(Conversation(phone=PATIENT, updated_at=at(2026, 10, 1, 9)))
    await h.storage.conversations.save(Conversation(phone=OTHER, updated_at=at(2027, 2, 20, 9)))
    await h.storage.processed.claim("old", at(2026, 10, 1))
    result = await h.container.jobs["cleanup"](h.services)
    assert result.processed == 2
    assert await h.storage.conversations.get(PATIENT) is None
    assert await h.storage.conversations.get(OTHER) is not None


# --------------------------------------------------------------------------- recall


async def test_recall_after_six_months_once(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model, now=at(2027, 5, 10, 9))
    await h.storage.patients.save(Patient(phone=PATIENT, name="Maria Silva", created_at=at(2026, 1, 1)))
    await h.storage.appointments.save(appt(start=at(2026, 10, 26, 9), end=at(2026, 10, 26, 10)))
    result = await h.container.jobs["recall"](h.services)
    assert result.sent == 1
    (msg,) = templates_to(h, PATIENT, "lembrete_retorno")
    assert "Maria" in msg.text  # type: ignore[attr-defined]
    assert (await h.container.jobs["recall"](h.services)).sent == 0  # idempotent


async def test_recall_skips_opted_out_and_patients_with_future_visit(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model, now=at(2027, 5, 10, 9))
    await h.storage.patients.save(Patient(phone=PATIENT, name="Maria", created_at=at(2026, 1, 1), opted_out=True))
    await h.storage.patients.save(Patient(phone=OTHER, name="Bia", created_at=at(2026, 1, 1)))
    await h.storage.appointments.save(appt(start=at(2026, 10, 26, 9), end=at(2026, 10, 26, 10)))
    await h.storage.appointments.save(appt(patient_phone=OTHER, start=at(2026, 10, 27, 9), end=at(2026, 10, 27, 10)))
    await h.storage.appointments.save(appt(patient_phone=OTHER, start=at(2027, 5, 20, 9), end=at(2027, 5, 20, 10)))
    assert (await h.container.jobs["recall"](h.services)).sent == 0


async def test_recall_not_too_early(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model, now=at(2027, 1, 10, 9))
    await h.storage.patients.save(Patient(phone=PATIENT, name="Maria", created_at=at(2026, 1, 1)))
    await h.storage.appointments.save(appt(start=at(2026, 10, 26, 9), end=at(2026, 10, 26, 10)))
    assert (await h.container.jobs["recall"](h.services)).sent == 0


# --------------------------------------------------------------------------- follow-up


async def test_followup_after_visit_once(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model, now=mon(18))
    await h.storage.patients.save(Patient(phone=PATIENT, name="Maria", created_at=at(2026, 1, 1)))
    await h.storage.appointments.save(appt())  # ended 10:00
    await h.storage.appointments.save(appt(patient_phone=OTHER, status=AppointmentStatus.CANCELLED))
    result = await h.container.jobs["followup"](h.services)
    assert result.sent == 1
    (msg,) = templates_to(h, PATIENT, "pos_consulta")
    assert "https://example.test/review" in msg.text  # type: ignore[attr-defined]
    assert (await h.container.jobs["followup"](h.services)).sent == 0


async def test_followup_respects_opt_out(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model, now=mon(18))
    await h.storage.patients.save(Patient(phone=PATIENT, name="Maria", created_at=at(2026, 1, 1), opted_out=True))
    await h.storage.appointments.save(appt())
    assert (await h.container.jobs["followup"](h.services)).sent == 0


# --------------------------------------------------------------------------- waitlist


async def test_cancellation_offers_slot_to_waitlist(make_harness: HarnessFactory) -> None:
    script = ScriptModel()
    h = make_harness(script.model)
    a = appt(start=at(2026, 10, 28, 9), end=at(2026, 10, 28, 10))
    a.calendar_event_id = h.calendar.add_busy(a.start, a.end)
    await h.storage.appointments.save(a)
    await h.storage.waitlist.save(
        WaitlistEntry(phone=OTHER, patient_name="Bia Souza", procedure_id="avaliacao", created_at=at(2026, 10, 20))
    )
    await h.storage.waitlist.save(  # wants afternoon only → not offered a 09:00 slot
        WaitlistEntry(
            phone="5581933334444",
            patient_name="Caio",
            procedure_id="avaliacao",
            period="tarde",
            created_at=at(2026, 10, 21),
        )
    )

    script.add([("list_my_appointments", {})], echo_after("Consultas futuras:"))
    await h.send("quero cancelar")
    script.add([("propose_cancel", {"appointment_id": a.id})], echo_summary)
    await h.send(f"a consulta {a.id}")
    script.add([("confirm_pending_action", {})], echo_after("exatamente:"))
    await h.send("sim")

    (offer,) = templates_to(h, OTHER, "vaga_disponivel")
    assert "quarta-feira, 28/10, às 09:00" in offer.text  # type: ignore[attr-defined]
    assert templates_to(h, "5581933334444", "vaga_disponivel") == []
    conv = await h.storage.conversations.get(OTHER)  # offer recorded so the agent has context if she replies
    assert conv is not None and "Abriu uma vaga" in conv.messages[-1].text


async def test_join_waitlist_tool_is_idempotent(make_harness: HarnessFactory) -> None:
    args = {"procedure_id": "limpeza", "patient_name": "Maria Silva", "period": "manha"}
    script = ScriptModel([("join_waitlist", args)], "Pronto, você está na lista de espera.")
    h = make_harness(script.model)
    await h.send("me coloca na lista de espera")
    script.add([("join_waitlist", args)], "Você já está na lista.")
    await h.send("me coloca de novo")
    assert len(await h.storage.waitlist.list_active()) == 1
    script.add([("leave_waitlist", {})], "Removido.")
    await h.send("pode me tirar da lista")
    assert await h.storage.waitlist.list_active() == []


async def test_waitlist_disabled_removes_tools(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model, capabilities={"waitlist": {"enabled": False}})
    names = {c.name for c in h.container.capabilities}
    assert "waitlist" not in names
    assert "recall" not in names  # capabilities={...} replaces the whole mapping in this fixture
    assert all("join_waitlist" not in c.tool_names() for c in h.container.capabilities)


async def test_no_followup_or_recall_if_unused_delay(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model, now=mon(9) + timedelta(days=5))
    await h.storage.appointments.save(appt())
    assert (await h.container.jobs["followup"](h.services)).sent == 0  # older than max_delay (48h)


async def test_daily_runs_every_job(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model, now=at(2026, 10, 25, 9))
    await h.storage.appointments.save(appt())
    result = await h.container.jobs["daily"](h.services)
    assert result.name == "daily" and result.sent == 1  # the reminder for Monday
    assert len(templates_to(h, PATIENT, "lembrete_consulta")) == 1
