from __future__ import annotations

from datetime import timedelta

from app.agent.factory import to_model_history
from app.agent.validators import dates_in, prices_in, times_in
from app.domain.models import ChatMessage
from tests.helpers import mon


def test_extractors() -> None:
    assert times_in("às 14h, 9:30 ou 15h30") == {(14, 0), (9, 30), (15, 30)}
    assert times_in("com 24 horas de antecedência e 12 horas antes") == set()
    assert dates_in("dia 26/10 ou 3/11") == {(26, 10), (3, 11)}
    assert prices_in("custa R$ 1.180,00 ou R$ 90") == {"1180,00", "90"}


def test_history_groups_and_starts_with_request() -> None:
    t = mon(9)
    msgs = [
        ChatMessage(role="bot", text="lembrete", at=t),
        ChatMessage(role="patient", text="oi", at=t + timedelta(minutes=1)),
        ChatMessage(role="patient", text="tudo bem?", at=t + timedelta(minutes=2)),
        ChatMessage(role="bot", text="olá", at=t + timedelta(minutes=3)),
        ChatMessage(role="dentist", text="aqui é a doutora", at=t + timedelta(minutes=4)),
        ChatMessage(role="system", text="interno", at=t + timedelta(minutes=5)),
    ]
    history = to_model_history(msgs)
    assert [m.kind for m in history] == ["request", "response"]
    assert len(history[0].parts) == 2 and len(history[1].parts) == 2
