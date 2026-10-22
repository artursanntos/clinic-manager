"""Core capability: find slots, propose, confirm (two-step), reschedule, cancel. See ADR 0005."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any, Literal

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.domain.models import (
    BookAction,
    CancelAction,
    PendingAction,
    RescheduleAction,
    SlotKind,
)
from app.domain.rules import ClinicRules, Weekday
from app.harness.capability import BaseCapability, Deny, PolicyDecision, RequireApproval, ToolPolicy
from app.harness.context import TurnDeps
from app.scheduling import SlotQuery
from app.services.messages import (
    appointment_line,
    booking_summary,
    cancel_summary,
    format_slot_line,
    reschedule_summary,
)

MAX_SEARCH_DAYS = 31
DEFAULT_SEARCH_DAYS = 14


async def find_available_slots(
    ctx: RunContext[TurnDeps],
    procedure_id: str,
    from_date: date | None = None,
    to_date: date | None = None,
    period: Literal["manha", "tarde", "noite", "qualquer"] = "qualquer",
    weekdays: list[Weekday] | None = None,
    include_encaixe: bool = False,
) -> str:
    """Busca horários livres REAIS na agenda. É a ÚNICA fonte de horários que você pode oferecer.

    Args:
        procedure_id: id do procedimento (veja a lista de procedimentos nas instruções).
        from_date: primeiro dia da busca (AAAA-MM-DD). Padrão: hoje.
        to_date: último dia da busca (AAAA-MM-DD). Padrão: 14 dias depois de from_date.
        period: manha, tarde, noite ou qualquer.
        weekdays: filtrar por dias da semana (monday..sunday).
        include_encaixe: incluir horários de encaixe (precisam de confirmação da doutora). Use só se o paciente
            precisar de algo mais cedo do que os horários normais disponíveis.
    """
    deps = ctx.deps
    rules = deps.rules
    procedure = rules.procedure(procedure_id)
    if procedure is None:
        ids = ", ".join(p.id for p in rules.procedures)
        return f"Procedimento '{procedure_id}' não existe. Ids válidos: {ids}."
    if not procedure.bookable_by_bot:
        return (
            f"{procedure.name} não pode ser agendado pelo assistente: precisa de avaliação/combinação com a doutora. "
            "Ofereça marcar uma avaliação ou faça handoff."
        )
    today = deps.now.date()
    start = max(from_date or today, today)
    end = to_date or start + timedelta(days=DEFAULT_SEARCH_DAYS)
    end = min(max(end, start), start + timedelta(days=MAX_SEARCH_DAYS))
    query = SlotQuery(
        duration=timedelta(minutes=procedure.duration_minutes),
        from_date=start,
        to_date=end,
        period=period,
        weekdays=frozenset(weekdays) if weekdays else None,
        include_encaixe=include_encaixe,
    )
    slots = await deps.services.booking.find_slots(query, deps.now)
    if not slots:
        return (
            f"Nenhum horário livre para {procedure.name} entre {start:%d/%m} e {end:%d/%m} com esses filtros. "
            "Sugira outro período/dia, a opção de encaixe, ou a lista de espera se estiver disponível."
        )
    lines = []
    for slot in slots:
        slot_id = deps.conversation.offer(slot, procedure.id)
        deps.turn.offered_slot_ids.append(slot_id)
        lines.append(format_slot_line(slot_id, slot, rules.tz))
    return (
        f"Horários disponíveis para {procedure.name} ({procedure.duration_minutes} min):\n"
        + "\n".join(lines)
        + "\nOfereça ao paciente exatamente estas opções (com o código H de cada uma)."
    )


async def propose_booking(ctx: RunContext[TurnDeps], slot_id: str, patient_name: str) -> str:
    """Registra uma PROPOSTA de agendamento (não marca nada). Depois, peça um "sim" explícito ao paciente.

    Args:
        slot_id: código de um horário oferecido por find_available_slots (ex.: H3).
        patient_name: nome completo do paciente, como ele informou.
    """
    deps = ctx.deps
    offered = deps.conversation.offered_slots.get(slot_id.strip().upper())
    if offered is None:
        return _invalid_slot(deps)
    name = " ".join(patient_name.split())
    if not 2 <= len(name) <= 80:
        return "Nome inválido. Pergunte o nome completo do paciente."
    procedure = deps.rules.procedure(offered.procedure_id)
    if procedure is None:
        return "Procedimento indisponível. Faça handoff."
    summary = booking_summary(procedure, offered.slot, name, deps.rules.tz)
    action = BookAction(slot_id=slot_id, slot=offered.slot, procedure_id=procedure.id, patient_name=name)
    return _register(deps, PendingAction(action=action, summary=summary, created_turn=deps.conversation.turn))


async def list_my_appointments(ctx: RunContext[TurnDeps]) -> str:
    """Lista as consultas futuras ativas deste paciente (com o código de cada uma)."""
    deps = ctx.deps
    appointments = await deps.services.booking.upcoming_for(deps.patient.phone, deps.now)
    if not appointments:
        return "O paciente não tem consultas futuras marcadas."
    return "Consultas futuras:\n" + "\n".join(appointment_line(a, deps.rules) for a in appointments)


async def propose_reschedule(ctx: RunContext[TurnDeps], appointment_id: str, slot_id: str) -> str:
    """Registra uma PROPOSTA de remarcação (não altera nada). Depois, peça um "sim" explícito.

    Args:
        appointment_id: código da consulta (de list_my_appointments).
        slot_id: código do novo horário (de find_available_slots, buscado com o mesmo procedimento).
    """
    deps = ctx.deps
    appointment = await deps.services.storage.appointments.get(appointment_id.strip())
    if appointment is None or appointment.patient_phone != deps.patient.phone or not appointment.is_active:
        return "Consulta não encontrada. Use list_my_appointments para obter o código correto."
    offered = deps.conversation.offered_slots.get(slot_id.strip().upper())
    if offered is None:
        return _invalid_slot(deps)
    if offered.procedure_id != appointment.procedure_id:
        return "Esse horário foi buscado para outro procedimento. Busque horários com o procedimento da consulta."
    procedure = deps.rules.procedure(appointment.procedure_id)
    if procedure is None:
        return "Procedimento indisponível. Faça handoff."
    summary = reschedule_summary(procedure, appointment, offered.slot, deps.rules.tz)
    action = RescheduleAction(appointment_id=appointment.id, slot_id=slot_id, slot=offered.slot)
    return _register(deps, PendingAction(action=action, summary=summary, created_turn=deps.conversation.turn))


async def propose_cancel(ctx: RunContext[TurnDeps], appointment_id: str) -> str:
    """Registra uma PROPOSTA de cancelamento (não cancela nada). Depois, peça um "sim" explícito.

    Args:
        appointment_id: código da consulta (de list_my_appointments).
    """
    deps = ctx.deps
    appointment = await deps.services.storage.appointments.get(appointment_id.strip())
    if appointment is None or appointment.patient_phone != deps.patient.phone or not appointment.is_active:
        return "Consulta não encontrada. Use list_my_appointments para obter o código correto."
    procedure = deps.rules.procedure(appointment.procedure_id)
    if procedure is None:
        return "Procedimento indisponível. Faça handoff."
    summary = cancel_summary(procedure, appointment, deps.rules.tz)
    result = _register(
        deps,
        PendingAction(
            action=CancelAction(appointment_id=appointment.id), summary=summary, created_turn=deps.conversation.turn
        ),
    )
    policy = deps.rules.cancellation_policy
    if appointment.start - deps.now < timedelta(hours=policy.min_notice_hours):
        result += f"\nAtenção: cancelamento com menos de {policy.min_notice_hours}h. Informe a política: {policy.text}"
    return result


async def confirm_pending_action(ctx: RunContext[TurnDeps]) -> str:
    """Executa a ação proposta. Use SOMENTE depois que o paciente respondeu "sim" à proposta, em uma nova mensagem."""
    deps = ctx.deps
    conversation = deps.conversation
    pending = conversation.pending_action
    if pending is None:
        return "Não há proposta pendente. Primeiro use uma tool propose_* e peça confirmação ao paciente."
    if pending.created_turn >= conversation.turn:
        return (
            "A proposta acabou de ser feita nesta mesma mensagem. Mostre o resumo e aguarde o paciente responder "
            "'sim' na próxima mensagem."
        )
    outcome = await deps.services.booking.execute(pending.action, deps.patient.phone, deps.now)
    deps.turn.outcome = outcome
    deps.turn.events.extend(outcome.events)
    conversation.pending_action = None
    if outcome.ok:
        conversation.offered_slots.clear()
        if outcome.appointment is not None:
            deps.patient.name = outcome.appointment.patient_name
        return f"FEITO. Informe ao paciente exatamente: {outcome.message}"
    slot_id = getattr(pending.action, "slot_id", None)
    if slot_id:
        conversation.offered_slots.pop(slot_id, None)
    return f"NÃO FOI POSSÍVEL: {outcome.message} Ofereça buscar novos horários."


async def cancel_pending_action(ctx: RunContext[TurnDeps]) -> str:
    """Descarta a proposta pendente (o paciente disse não ou mudou de ideia)."""
    ctx.deps.conversation.pending_action = None
    return "Proposta descartada. Nada foi alterado."


def _invalid_slot(deps: TurnDeps) -> str:
    valid = ", ".join(deps.conversation.offered_slots) or "nenhum"
    return (
        f"Código de horário inválido. Códigos válidos nesta conversa: {valid}. "
        "Se o paciente quer outro horário, use find_available_slots. Nunca invente horários."
    )


def _register(deps: TurnDeps, pending: PendingAction) -> str:
    deps.conversation.pending_action = pending
    deps.turn.proposed_summary = pending.summary
    return (
        "PROPOSTA REGISTRADA (nada foi alterado ainda). Mostre ao paciente exatamente este resumo e peça um 'sim' "
        f"explícito: «{pending.summary}»"
    )


class SchedulingCapability(BaseCapability):
    name = "scheduling"

    def __init__(self, rules: ClinicRules, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(rules, config)
        self.max_active = int(self.config.get("max_active_appointments", 3))
        self._toolset = FunctionToolset[TurnDeps](
            tools=[
                find_available_slots,
                propose_booking,
                list_my_appointments,
                propose_reschedule,
                propose_cancel,
                confirm_pending_action,
                cancel_pending_action,
            ]
        )

    def toolset(self) -> FunctionToolset[TurnDeps]:
        return self._toolset

    def instructions(self) -> str:
        procedures = "\n".join(
            f"- {p.id}: {p.name} ({p.duration_minutes} min)"
            + ("" if p.bookable_by_bot else " — NÃO agendável pelo bot")
            for p in self.rules.procedures
        )
        return (
            "## Agendamento\n"
            "- Horários: use SOMENTE find_available_slots e ofereça as opções com seus códigos (H1, H2…). "
            "Nunca escreva um dia/horário que não veio de uma tool.\n"
            "- Para marcar, remarcar ou cancelar: 1) propose_* 2) mostre o resumo exato e pergunte se pode confirmar "
            "3) só depois que o paciente responder 'sim' em uma nova mensagem, chame confirm_pending_action.\n"
            "- Se o paciente recusar ou mudar de ideia, chame cancel_pending_action.\n"
            "- Para remarcar/cancelar, use list_my_appointments para achar o código da consulta.\n"
            f"Procedimentos (id: nome):\n{procedures}"
        )

    def policies(self) -> Sequence[ToolPolicy]:
        return (self._encaixe_policy, self._new_patient_policy, self._late_cancel_policy, self._limit_policy)

    async def _encaixe_policy(self, deps: TurnDeps, tool: str, args: Mapping[str, Any]) -> PolicyDecision | None:
        pending = deps.conversation.pending_action
        if tool != "confirm_pending_action" or pending is None or not deps.rules.approvals.encaixe:
            return None
        action = pending.action
        if isinstance(action, BookAction | RescheduleAction) and action.slot.kind == SlotKind.ENCAIXE:
            return RequireApproval("horário de encaixe (fora da grade normal)")
        return None

    async def _new_patient_policy(self, deps: TurnDeps, tool: str, args: Mapping[str, Any]) -> PolicyDecision | None:
        pending = deps.conversation.pending_action
        if tool != "confirm_pending_action" or pending is None or not deps.rules.approvals.new_patient:
            return None
        if isinstance(pending.action, BookAction):
            history = await deps.services.storage.appointments.list_by_patient(deps.patient.phone)
            if not history:
                return RequireApproval("primeira consulta de paciente novo")
        return None

    async def _late_cancel_policy(self, deps: TurnDeps, tool: str, args: Mapping[str, Any]) -> PolicyDecision | None:
        pending = deps.conversation.pending_action
        if tool != "confirm_pending_action" or pending is None or not deps.rules.approvals.late_cancellation:
            return None
        if isinstance(pending.action, CancelAction):
            appointment = await deps.services.storage.appointments.get(pending.action.appointment_id)
            notice = timedelta(hours=deps.rules.cancellation_policy.min_notice_hours)
            if appointment is not None and appointment.start - deps.now < notice:
                return RequireApproval("cancelamento em cima da hora")
        return None

    async def _limit_policy(self, deps: TurnDeps, tool: str, args: Mapping[str, Any]) -> PolicyDecision | None:
        pending = deps.conversation.pending_action
        if tool != "confirm_pending_action" or pending is None or not isinstance(pending.action, BookAction):
            return None
        upcoming = await deps.services.booking.upcoming_for(deps.patient.phone, deps.now)
        if len(upcoming) >= self.max_active:
            return Deny(
                f"o paciente já tem {len(upcoming)} consultas futuras (limite {self.max_active}). "
                "Ofereça remarcar uma existente ou faça handoff."
            )
        return None
