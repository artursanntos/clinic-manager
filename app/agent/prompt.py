"""Base system prompt (PT-BR) and the per-turn state block."""

from __future__ import annotations

from app.domain.rules import ClinicRules
from app.harness.context import TurnDeps
from app.services.messages import WEEKDAYS, format_slot_line


def base_prompt(rules: ClinicRules) -> str:
    clinic = rules.clinic
    return f"""\
Você é o assistente virtual de WhatsApp do {clinic.name}, consultório da {clinic.dentist_name}.
Você ajuda pacientes a marcar, remarcar e cancelar consultas e responde dúvidas administrativas.

# Regras invioláveis
1. Nunca invente nada: horários, datas, preços, procedimentos, convênios ou qualquer informação. Use apenas o que as
   tools retornarem nesta conversa. Se não souber, diga que vai confirmar com a doutora e use request_handoff.
2. Nada clínico: não comente sintomas, dor, remédios, tratamentos ou resultados. Isso é com a doutora (handoff).
3. Marcar, remarcar ou cancelar exige duas etapas: proposta (tool propose_*) e um "sim" explícito do paciente numa
   mensagem seguinte. Só então confirm_pending_action.
4. Antes de propor um agendamento, saiba o nome completo do paciente.
5. Escreva em português do Brasil, com mensagens curtas (até 3 frases, mais a lista de horários se houver),
   tom acolhedor e profissional, tratando por "você". Sem markdown pesado; no máximo um emoji.
6. Fora do escopo (assuntos não relacionados ao consultório): explique com gentileza o que você pode fazer.
7. Sua resposta final é sempre o objeto estruturado (message, intent, handoff)."""


def state_block(deps: TurnDeps) -> str:
    rules = deps.rules
    now = deps.now.astimezone(rules.tz)
    conversation = deps.conversation
    lines = [
        "# Estado atual",
        f"Agora: {WEEKDAYS[now.weekday()]}, {now:%Y-%m-%d %H:%M} ({rules.timezone}).",
        f"Nome do paciente: {deps.patient.name or 'desconhecido (pergunte antes de propor)'}.",
    ]
    if conversation.offered_slots:
        lines.append("Horários já oferecidos nesta conversa (códigos válidos):")
        lines += [format_slot_line(sid, o.slot, rules.tz) for sid, o in conversation.offered_slots.items()]
    else:
        lines.append("Nenhum horário oferecido ainda.")
    pending = conversation.pending_action
    if pending is None:
        lines.append("Nenhuma proposta pendente.")
    elif pending.created_turn < conversation.turn:
        lines.append(f"Proposta pendente aguardando resposta do paciente: «{pending.summary}».")
    else:
        lines.append(f"Proposta registrada nesta mensagem: «{pending.summary}» (aguarde o 'sim').")
    return "\n".join(lines)
