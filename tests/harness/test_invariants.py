"""Anti-hallucination invariants (CLAUDE.md). Each test drives the real harness with a scripted FunctionModel."""

from __future__ import annotations

from app.domain.models import ApprovalStatus, ConversationState
from tests.conftest import DENTIST, PATIENT, HarnessFactory
from tests.helpers import ScriptModel, echo_after, echo_summary, final, mon

FIND_LIMPEZA = [
    ("find_available_slots", {"procedure_id": "limpeza", "from_date": "2026-10-26", "to_date": "2026-10-26"})
]


async def test_cannot_book_a_slot_that_was_never_offered(make_harness: HarnessFactory) -> None:
    script = ScriptModel(
        [("propose_booking", {"slot_id": "H9", "patient_name": "Maria Silva"})],
        "Desculpe, preciso buscar os horários primeiro.",
    )
    h = make_harness(script.model)
    await h.send("Marca H9 pra mim, Maria Silva")
    script.add([("confirm_pending_action", {})], "Ok.")
    await h.send("sim")
    assert h.calendar.events == {}
    conv = await h.storage.conversations.get(PATIENT)
    assert conv is not None and conv.pending_action is None


async def test_confirm_in_the_same_turn_as_the_proposal_does_nothing(make_harness: HarnessFactory) -> None:
    script = ScriptModel(
        FIND_LIMPEZA,
        [("propose_booking", {"slot_id": "H1", "patient_name": "Maria Silva"})],
        [("confirm_pending_action", {})],
        "Confirma? Limpeza para Maria Silva: segunda-feira, 26/10, às 08:00",
    )
    h = make_harness(script.model)
    await h.send("Quero limpeza segunda, sou Maria Silva, pode marcar o primeiro")
    assert h.calendar.events == {}
    conv = await h.storage.conversations.get(PATIENT)
    assert conv is not None and conv.pending_action is not None


async def test_two_step_booking_writes_to_calendar(make_harness: HarnessFactory) -> None:
    script = ScriptModel(FIND_LIMPEZA, "Tenho H1, H2 e H3. Qual prefere?")
    h = make_harness(script.model)
    await h.send("Quero limpeza segunda")
    script.add([("propose_booking", {"slot_id": "H1", "patient_name": "Maria Silva"})], echo_summary)
    replies = await h.send("H1, Maria Silva")
    assert "Limpeza para Maria Silva: segunda-feira, 26/10, às 08:00" in replies[0]
    script.add([("confirm_pending_action", {})], echo_after("exatamente:"))
    replies = await h.send("sim")
    assert replies[0].startswith("Consulta marcada")
    (event,) = h.calendar.events.values()
    assert event.start == mon(8) and "Maria Silva" in event.title and PATIENT not in event.title


async def test_slot_taken_between_offer_and_confirmation(make_harness: HarnessFactory) -> None:
    script = ScriptModel(FIND_LIMPEZA, "Tenho H1.")
    h = make_harness(script.model)
    await h.send("limpeza segunda")
    script.add([("propose_booking", {"slot_id": "H1", "patient_name": "Maria Silva"})], echo_summary)
    await h.send("H1, Maria Silva")
    h.calendar.add_busy(mon(8, 30), mon(9), "Outro paciente")  # someone took it meanwhile
    script.add([("confirm_pending_action", {})], "Esse horário acabou de ser ocupado. Quer ver outros?")
    await h.send("sim")
    assert len(h.calendar.events) == 1  # only the other event
    assert await h.storage.appointments.list_by_patient(PATIENT) == []


async def test_invented_time_is_rejected_and_never_sent(make_harness: HarnessFactory) -> None:
    script = ScriptModel(
        FIND_LIMPEZA,
        "Tenho às 07:15, serve?",  # 07:15 never came from a tool
        "Tenho H1 disponível. Serve?",
    )
    h = make_harness(script.model)
    replies = await h.send("limpeza segunda")
    assert replies == ["Tenho H1 disponível. Serve?"]
    assert not any("07:15" in m.text for m in h.messaging.outbox)


async def test_persistent_hallucination_falls_back_to_handoff(make_harness: HarnessFactory) -> None:
    script = ScriptModel("A limpeza custa R$ 99,00.", "Custa R$ 99,00.", "R$ 99,00!", "R$ 99,00!!")
    h = make_harness(script.model)
    replies = await h.send("quanto custa a limpeza?")
    assert "R$ 99,00" not in replies[0]
    conv = await h.storage.conversations.get(PATIENT)
    assert conv is not None and conv.state == ConversationState.HUMAN


async def test_price_from_tool_is_allowed(make_harness: HarnessFactory) -> None:
    script = ScriptModel(
        [("get_clinic_info", {"topic": "procedimentos_e_precos", "procedure_id": "limpeza"})],
        "A limpeza custa R$ 180,00.",
    )
    h = make_harness(script.model)
    assert await h.send("quanto custa a limpeza?") == ["A limpeza custa R$ 180,00."]


async def test_proposal_must_repeat_canonical_summary(make_harness: HarnessFactory) -> None:
    script = ScriptModel(FIND_LIMPEZA, "Tenho H1.")
    h = make_harness(script.model)
    await h.send("limpeza segunda")
    script.add(
        [("propose_booking", {"slot_id": "H1", "patient_name": "Maria Silva"})],
        "Posso marcar?",  # missing the summary → retry
        echo_summary,
    )
    replies = await h.send("H1, Maria Silva")
    assert "Limpeza para Maria Silva" in replies[0]


async def test_clinical_message_hands_off_without_calling_the_llm(make_harness: HarnessFactory) -> None:
    script = ScriptModel()
    h = make_harness(script.model)
    replies = await h.send("Estou com muita dor no dente, posso tomar ibuprofeno?")
    assert script.calls == 0
    assert "Dra. Teste" in replies[0] and "192" not in replies[0]
    conv = await h.storage.conversations.get(PATIENT)
    assert conv is not None and conv.state == ConversationState.HUMAN
    alerts = [m for m in h.messaging.to(DENTIST) if m.template and m.template.name == "alerta_atendimento"]
    assert len(alerts) == 1


async def test_urgent_message_includes_emergency_guidance(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel().model)
    replies = await h.send("Minha gengiva está sangrando muito")
    assert "192" in replies[0]


async def test_human_state_is_silent_until_dentist_releases(make_harness: HarnessFactory) -> None:
    script = ScriptModel()
    h = make_harness(script.model)
    await h.send("estou com dor")
    assert await h.send("alô?") == []
    assert script.calls == 0
    replies = await h.send("#bot 5581988887777", phone=DENTIST)
    assert "reativado" in replies[0]
    script.add("Oi! Como posso ajudar?")
    assert await h.send("oi") == ["Oi! Como posso ajudar?"]


async def test_model_requested_handoff(make_harness: HarnessFactory) -> None:
    script = ScriptModel(
        [("request_handoff", {"reason": "pediu humano"})],
        final("Vou pedir para a doutora te responder.", "handoff"),
    )
    h = make_harness(script.model)
    await h.send("quero falar com uma pessoa")
    conv = await h.storage.conversations.get(PATIENT)
    assert conv is not None and conv.state == ConversationState.HUMAN and conv.handoff_reason == "pediu humano"


async def test_duplicate_message_is_processed_once(make_harness: HarnessFactory) -> None:
    from app.adapters.messaging.base import InboundMessage

    script = ScriptModel("Oi!")
    h = make_harness(script.model)
    msg = InboundMessage(message_id="wamid.same", from_phone=PATIENT, timestamp=h.clock.now, text="oi")
    first = await h.container.orchestrator.handle(msg)
    second = await h.container.orchestrator.handle(msg)
    assert first.status == "replied" and second.status == "duplicate"
    assert script.calls == 1


async def test_model_crash_after_booking_reports_the_truth(make_harness: HarnessFactory) -> None:
    script = ScriptModel(FIND_LIMPEZA, "Tenho H1.")
    h = make_harness(script.model)
    await h.send("limpeza segunda")
    script.add([("propose_booking", {"slot_id": "H1", "patient_name": "Maria Silva"})], echo_summary)
    await h.send("H1, Maria Silva")

    def boom(*_: object) -> None:
        raise RuntimeError("provider down")

    script.add([("confirm_pending_action", {})], boom)  # type: ignore[list-item]
    replies = await h.send("sim")
    assert replies[0].startswith("Consulta marcada")
    assert len(h.calendar.events) == 1


# --------------------------------------------------------------------------- human-in-the-loop approvals


async def _propose_encaixe(make_harness: HarnessFactory) -> tuple[object, ScriptModel]:
    script = ScriptModel(
        [
            (
                "find_available_slots",
                {
                    "procedure_id": "avaliacao",
                    "from_date": "2026-10-26",
                    "to_date": "2026-10-26",
                    "include_encaixe": True,
                },
            )
        ],
        "Tenho opções, inclusive encaixe.",
    )
    h = make_harness(script.model)
    await h.send("preciso de avaliação segunda, qualquer horário")
    conv = await h.storage.conversations.get(PATIENT)
    assert conv is not None
    encaixe_id = next(sid for sid, o in conv.offered_slots.items() if o.slot.kind == "encaixe")
    script.add([("propose_booking", {"slot_id": encaixe_id, "patient_name": "João Souza"})], echo_summary)
    await h.send(f"{encaixe_id}, João Souza")
    script.add([("confirm_pending_action", {})], echo_after("exatamente:"))
    replies = await h.send("sim")
    assert "confirmação da Dra. Teste" in replies[0]
    return h, script


async def test_encaixe_requires_dentist_approval(make_harness: HarnessFactory) -> None:
    h, script = await _propose_encaixe(make_harness)
    assert h.calendar.events == {}  # type: ignore[attr-defined]
    conv = await h.storage.conversations.get(PATIENT)  # type: ignore[attr-defined]
    assert conv.state == ConversationState.WAITING_APPROVAL
    requests = [m for m in h.messaging.to(DENTIST) if m.template and m.template.name == "pedido_aprovacao"]  # type: ignore[attr-defined]
    assert len(requests) == 1 and requests[0].template.button_payloads == ["APPROVE:1", "DENY:1"]

    # while waiting, the bot does not run the LLM
    calls = script.calls
    replies = await h.send("e aí?")  # type: ignore[attr-defined]
    assert "aguardando" in replies[0] and script.calls == calls

    replies = await h.send("#ok 1", phone=DENTIST)  # type: ignore[attr-defined]
    assert "aprovado" in replies[0]
    (event,) = h.calendar.events.values()  # type: ignore[attr-defined]
    assert event.start == mon(12) and "[ENCAIXE]" in event.title
    patient_msgs = [m.text for m in h.messaging.to(PATIENT)]  # type: ignore[attr-defined]
    assert patient_msgs[-1].startswith("Boa notícia")
    conv = await h.storage.conversations.get(PATIENT)  # type: ignore[attr-defined]
    assert conv.state == ConversationState.BOT


async def test_dentist_denies_via_button(make_harness: HarnessFactory) -> None:
    h, _ = await _propose_encaixe(make_harness)
    replies = await h.send(phone=DENTIST, button="DENY:1")  # type: ignore[attr-defined]
    assert "recusado" in replies[0]
    assert h.calendar.events == {}  # type: ignore[attr-defined]
    approval = await h.storage.approvals.get_by_code(1)  # type: ignore[attr-defined]
    assert approval.status == ApprovalStatus.DENIED
    assert "não conseguiu confirmar" in h.messaging.to(PATIENT)[-1].text  # type: ignore[attr-defined]


async def test_approval_fails_if_slot_was_taken(make_harness: HarnessFactory) -> None:
    h, _ = await _propose_encaixe(make_harness)
    h.calendar.add_busy(mon(12), mon(12, 30), "Almoço com cliente")  # type: ignore[attr-defined]
    replies = await h.send("#ok 1", phone=DENTIST)  # type: ignore[attr-defined]
    assert "não foi possível" in replies[0]
    approval = await h.storage.approvals.get_by_code(1)  # type: ignore[attr-defined]
    assert approval.status == ApprovalStatus.FAILED


async def test_policy_deny_blocks_the_write(make_harness: HarnessFactory) -> None:
    script = ScriptModel(FIND_LIMPEZA, "Tenho H1.")
    h = make_harness(script.model, capabilities={})
    h.container.capabilities[0].max_active = 0  # type: ignore[attr-defined]
    await h.send("limpeza segunda")
    script.add([("propose_booking", {"slot_id": "H1", "patient_name": "Maria Silva"})], echo_summary)
    await h.send("H1, Maria Silva")
    script.add([("confirm_pending_action", {})], "Você já tem consultas marcadas; quer remarcar uma delas?")
    await h.send("sim")
    assert h.calendar.events == {}
    assert any(e.action == "denied:confirm_pending_action" for e in h.storage.audit.entries)


# --------------------------------------------------------------------------- deterministic commands


async def test_opt_out_and_back(make_harness: HarnessFactory) -> None:
    script = ScriptModel()
    h = make_harness(script.model)
    replies = await h.send("SAIR")
    assert "VOLTAR" in replies[0]
    patient = await h.storage.patients.get(PATIENT)
    assert patient is not None and patient.opted_out
    await h.send("voltar")
    patient = await h.storage.patients.get(PATIENT)
    assert patient is not None and not patient.opted_out
    assert script.calls == 0


async def test_dentist_relays_message_and_lists_pending(make_harness: HarnessFactory) -> None:
    h = make_harness(ScriptModel("Oi!").model)
    await h.send("oi")
    replies = await h.send("#msg 5581988887777 Olá, aqui é a doutora!", phone=DENTIST)
    assert "enviada" in replies[0]
    assert h.messaging.to(PATIENT)[-1].text == "Olá, aqui é a doutora!"
    conv = await h.storage.conversations.get(PATIENT)
    assert conv is not None and conv.messages[-1].role == "dentist"
    assert (await h.send("#pendentes", phone=DENTIST))[0] == "Nenhum pedido aguardando aprovação."
    assert "#ok N" in (await h.send("bom dia", phone=DENTIST))[0]
