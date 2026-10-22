"""Canonical PT-BR texts built by code. The LLM repeats these; it never formats dates, times or prices itself."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.models import Appointment, Slot, SlotKind
from app.domain.rules import ClinicRules, Procedure

WEEKDAYS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]


def format_when(dt: datetime, tz: ZoneInfo) -> str:
    """``segunda-feira, 26/10, às 09:00``"""
    local = dt.astimezone(tz)
    return f"{WEEKDAYS[local.weekday()]}, {local:%d/%m}, às {local:%H:%M}"


def format_slot_line(slot_id: str, slot: Slot, tz: ZoneInfo) -> str:
    line = f"{slot_id} — {format_when(slot.start, tz)}"
    if slot.kind == SlotKind.ENCAIXE:
        line += " (encaixe, sujeito à confirmação da doutora)"
    return line


def format_price(procedure: Procedure) -> str:
    if procedure.price_cents is None:
        return procedure.price_note or "valor sob consulta com a doutora"
    if procedure.price_cents == 0:
        return procedure.price_note or "gratuita"
    reais, cents = divmod(procedure.price_cents, 100)
    value = f"R$ {reais:,}".replace(",", ".") + f",{cents:02d}"
    return f"{value} ({procedure.price_note})" if procedure.price_note else value


def booking_summary(procedure: Procedure, slot: Slot, patient_name: str, tz: ZoneInfo) -> str:
    text = f"{procedure.name} para {patient_name}: {format_when(slot.start, tz)}"
    if slot.kind == SlotKind.ENCAIXE:
        text += " (encaixe, sujeito à confirmação da doutora)"
    return text


def reschedule_summary(procedure: Procedure, old: Appointment, slot: Slot, tz: ZoneInfo) -> str:
    return f"{procedure.name}: de {format_when(old.start, tz)} para {format_when(slot.start, tz)}"


def cancel_summary(procedure: Procedure, appointment: Appointment, tz: ZoneInfo) -> str:
    return f"Cancelamento de {procedure.name} de {format_when(appointment.start, tz)}"


def appointment_line(appointment: Appointment, rules: ClinicRules) -> str:
    procedure = rules.procedure(appointment.procedure_id)
    name = procedure.name if procedure else appointment.procedure_id
    return f"{name} — {format_when(appointment.start, rules.tz)} (código {appointment.id})"


# --------------------------------------------------------------------------- fixed messages


def handoff_message(rules: ClinicRules) -> str:
    return (
        f"Vou pedir para a {rules.clinic.dentist_name} te responder pessoalmente por aqui, tudo bem? "
        "Ela retorna assim que possível."
    )


def clinical_handoff_message(rules: ClinicRules, *, urgent: bool) -> str:
    text = (
        "Sinto muito que você esteja passando por isso. Não consigo orientar sobre sintomas ou tratamentos por aqui, "
        f"então já avisei a {rules.clinic.dentist_name} para falar com você o quanto antes."
    )
    if urgent:
        text += f"\n\n{rules.emergency_guidance}"
    return text


def waiting_approval_message(rules: ClinicRules) -> str:
    return (
        f"Seu pedido está aguardando a confirmação da {rules.clinic.dentist_name}. "
        "Assim que ela responder, eu te aviso por aqui."
    )


def approval_requested_message(rules: ClinicRules, summary: str) -> str:
    return (
        f"Esse horário precisa da confirmação da {rules.clinic.dentist_name}: {summary}. "
        "Já enviei o pedido para ela e te aviso assim que ela responder."
    )


def opt_out_message() -> str:
    return (
        "Pronto, você não vai mais receber lembretes de retorno nem mensagens de acompanhamento. "
        "Para voltar, envie VOLTAR."
    )


def opt_in_message() -> str:
    return "Combinado! Você voltará a receber lembretes de retorno e mensagens de acompanhamento."


def technical_error_message(rules: ClinicRules) -> str:
    return (
        f"Tive um problema técnico agora. Já avisei a {rules.clinic.dentist_name}, que vai te responder por aqui. "
        "Desculpe o transtorno!"
    )


def attendance_confirmed_message(appointment: Appointment, rules: ClinicRules) -> str:
    return f"Presença confirmada para {format_when(appointment.start, rules.tz)}. Até lá!"
