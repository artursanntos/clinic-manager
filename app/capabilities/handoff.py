"""Core capability: hand the conversation to the dentist."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.domain.rules import ClinicRules
from app.harness.capability import BaseCapability
from app.harness.context import TurnDeps
from app.services.messages import handoff_message


async def request_handoff(ctx: RunContext[TurnDeps], reason: str) -> str:
    """Passa a conversa para a doutora (o assistente para de responder este paciente).

    Args:
        reason: motivo curto, sem dados clínicos detalhados (ex.: "pergunta sobre tratamento", "pediu humano").
    """
    ctx.deps.turn.handoff_reason = reason.strip()[:200] or "solicitado pelo assistente"
    return f"Handoff registrado. Diga ao paciente: {handoff_message(ctx.deps.rules)}"


class HandoffCapability(BaseCapability):
    name = "handoff"

    def __init__(self, rules: ClinicRules, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(rules, config)
        self._toolset = FunctionToolset[TurnDeps](tools=[request_handoff])

    def toolset(self) -> FunctionToolset[TurnDeps]:
        return self._toolset

    def instructions(self) -> str:
        return (
            "## Quando passar para a doutora (request_handoff)\n"
            "- Qualquer assunto clínico: sintomas, dor, medicação, tratamento, resultado, receita.\n"
            "- Perguntas cuja resposta não veio de uma tool.\n"
            "- O paciente pede para falar com a doutora/uma pessoa, reclama, ou a conversa não avança."
        )
