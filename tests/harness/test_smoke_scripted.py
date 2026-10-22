from __future__ import annotations

from tests.conftest import PATIENT, HarnessFactory


async def test_full_booking_flow_with_scripted_model(make_harness: HarnessFactory) -> None:
    h = make_harness()
    replies = await h.send("Oi, quero marcar uma limpeza de manhã")
    assert "H1" in replies[0]
    replies = await h.send("H1, meu nome é Maria Silva")
    assert "Maria Silva" in replies[0] and "SIM" in replies[0]
    assert h.calendar.events == {}
    replies = await h.send("sim")
    assert "Consulta marcada" in replies[0]
    assert len(h.calendar.events) == 1
    appts = await h.storage.appointments.list_by_patient(PATIENT)
    assert len(appts) == 1 and appts[0].patient_name == "Maria Silva"
