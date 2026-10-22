from __future__ import annotations

import pytest

from app.harness.guard import check_message


@pytest.mark.parametrize(
    "text",
    [
        "Estou com dor de dente",
        "meu dente está doendo",
        "posso tomar dipirona?",
        "qual REMÉDIO devo usar",
        "a anestesia demora pra passar?",
        "estou grávida, posso fazer limpeza?",
        "tenho sensibilidade depois do clareamento",
    ],
)
def test_clinical(text: str) -> None:
    hit = check_message(text)
    assert hit is not None and hit.kind == "clinical"


@pytest.mark.parametrize(
    "text",
    ["minha gengiva está sangrando", "rosto muito inchado", "tive febre", "é urgente!", "meu dente quebrou"],
)
def test_urgent(text: str) -> None:
    hit = check_message(text)
    assert hit is not None and hit.kind == "urgent"


@pytest.mark.parametrize(
    "text",
    [
        "quero marcar uma limpeza",
        "falar com a doutora",
        "qual o endereço?",
        "tenho uma cárie e quero restaurar",
        "pode ser às 14h?",
        "aceita convênio?",
    ],
)
def test_administrative_messages_pass(text: str) -> None:
    assert check_message(text) is None
