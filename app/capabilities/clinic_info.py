"""Core capability: clinic information, only from clinic.yaml."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.domain.rules import ClinicRules, Weekday
from app.harness.capability import BaseCapability
from app.harness.context import TurnDeps
from app.services.messages import WEEKDAYS, format_price

Topic = Literal[
    "endereco",
    "horario_atendimento",
    "procedimentos_e_precos",
    "preparo",
    "convenios",
    "formas_pagamento",
    "politica_cancelamento",
    "outros",
]

_NOT_FOUND = "Essa informação não está cadastrada. Não responda por conta própria: faça handoff para a doutora."


def working_hours_text(rules: ClinicRules) -> str:
    lines = []
    for i, day in enumerate(Weekday):
        ranges = rules.working_hours.get(day, [])
        if ranges:
            spans = " e ".join(f"{r.start:%H:%M}–{r.end:%H:%M}" for r in ranges)
            lines.append(f"{WEEKDAYS[i]}: {spans}")
        else:
            lines.append(f"{WEEKDAYS[i]}: fechado")
    return "\n".join(lines)


def clinic_info_text(rules: ClinicRules, topic: Topic, procedure_id: str | None = None) -> str:
    clinic = rules.clinic
    if topic == "endereco":
        text = f"Endereço: {clinic.address}"
        return text + (f"\nMapa: {clinic.maps_url}" if clinic.maps_url else "")
    if topic == "horario_atendimento":
        return "Horário de atendimento:\n" + working_hours_text(rules)
    if topic == "procedimentos_e_precos":
        procedures = [p for p in rules.procedures if procedure_id in (None, p.id)]
        if not procedures:
            return _NOT_FOUND
        return "\n".join(f"{p.name} ({p.duration_minutes} min): {format_price(p)}" for p in procedures)
    if topic == "preparo":
        procedure = rules.procedure(procedure_id or "")
        if procedure is None:
            return "Informe o procedure_id para consultar o preparo."
        return procedure.preparation or f"Não há orientação de preparo cadastrada para {procedure.name}."
    if topic == "convenios":
        if not rules.insurance.accepted:
            return "A clínica não trabalha com convênios."
        text = "Convênios aceitos: " + ", ".join(rules.insurance.accepted)
        return text + (f"\n{rules.insurance.notes}" if rules.insurance.notes else "")
    if topic == "formas_pagamento":
        return ("Formas de pagamento: " + ", ".join(clinic.payment_methods)) if clinic.payment_methods else _NOT_FOUND
    if topic == "politica_cancelamento":
        return rules.cancellation_policy.text
    if clinic.extra_info:
        return "\n".join(f"{k}: {v}" for k, v in clinic.extra_info.items()) + f"\nSe nada disso responder: {_NOT_FOUND}"
    return _NOT_FOUND


async def get_clinic_info(ctx: RunContext[TurnDeps], topic: Topic, procedure_id: str | None = None) -> str:
    """Informações oficiais da clínica. Única fonte permitida para endereço, horários, preços, convênios etc.

    Args:
        topic: assunto da pergunta. Use "outros" para estacionamento, acessibilidade, atrasos e afins.
        procedure_id: opcional, para preço/preparo de um procedimento específico.
    """
    return clinic_info_text(ctx.deps.rules, topic, procedure_id)


class ClinicInfoCapability(BaseCapability):
    name = "clinic_info"

    def __init__(self, rules: ClinicRules, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(rules, config)
        self._toolset = FunctionToolset[TurnDeps](tools=[get_clinic_info])

    def toolset(self) -> FunctionToolset[TurnDeps]:
        return self._toolset

    def instructions(self) -> str:
        return (
            "## Informações da clínica\n"
            "- Endereço, horários, preços, convênios, pagamento, preparo e políticas: SOMENTE via get_clinic_info. "
            "Repita o conteúdo sem acrescentar nada.\n"
            "- Se a resposta não estiver no retorno da tool, diga que vai confirmar com a doutora e faça handoff."
        )
