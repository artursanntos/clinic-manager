"""Deterministic per-message pipeline. The LLM participates in exactly one step.

inbound → dedupe → dentist commands | patient:
  load state → deterministic commands (SAIR/VOLTAR, reminder buttons)
  → state gate (human: silent | waiting_approval: canned reply)
  → clinical guard (handoff, no LLM)
  → agent (tools behind PolicyEnforcer) → handoff/approval bookkeeping
  → send reply → persist → publish domain events
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from app.adapters.messaging.base import InboundMessage, MessagingError
from app.agent.factory import ClinicAgent, to_model_history
from app.agent.output import AgentReply
from app.domain.events import DomainEvent, HandoffRequested
from app.domain.models import (
    AuditEntry,
    Conversation,
    ConversationState,
    Patient,
    mask_phone,
    normalize_phone,
    phone_variants,
    same_phone,
)
from app.harness.approvals import ApprovalService
from app.harness.context import Services, TurnDeps
from app.harness.events import EventBus
from app.harness.guard import check_message, normalize
from app.harness.notify import Notifier
from app.services.messages import (
    approval_requested_message,
    attendance_confirmed_message,
    clinical_handoff_message,
    opt_in_message,
    opt_out_message,
    technical_error_message,
    waiting_approval_message,
)

log = logging.getLogger(__name__)

Status = Literal["duplicate", "dentist_command", "silent", "replied"]

_OPT_OUT = {"sair", "parar", "stop", "descadastrar"}
_OPT_IN = {"voltar"}
_TEXT_ONLY = "Por enquanto eu só consigo ler mensagens de texto. Pode escrever, por favor?"
_DENTIST_HELP = (
    "Comandos:\n"
    "#ok N — aprova o pedido N\n"
    "#nao N — recusa o pedido N\n"
    "#pendentes — lista pedidos aguardando aprovação\n"
    "#msg TELEFONE texto — envia uma mensagem sua ao paciente\n"
    "#bot TELEFONE — devolve a conversa ao assistente"
)


@dataclass
class HandleResult:
    status: Status
    replies: list[str] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        services: Services,
        agent: ClinicAgent,
        approvals: ApprovalService,
        events: EventBus,
        *,
        history_limit: int = 12,
    ) -> None:
        self.services = services
        self.agent = agent
        self.approvals = approvals
        self.events = events
        self.notifier = Notifier(services)
        self.history_limit = history_limit
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ entry point

    async def handle(self, msg: InboundMessage) -> HandleResult:
        s = self.services
        now = s.now()
        if not await s.storage.processed.claim(msg.message_id, now):
            return HandleResult("duplicate")
        try:
            phone = normalize_phone(msg.from_phone)
        except ValueError:
            log.warning("ignoring message with invalid sender")
            return HandleResult("silent")
        if same_phone(phone, normalize_phone(s.rules.clinic.dentist_phone)):
            return await self._dentist(msg)
        # One message at a time per patient (within this instance), so state updates never interleave.
        lock = self._locks.setdefault(phone, asyncio.Lock())
        async with lock:
            return await self._patient(phone, msg)

    # ------------------------------------------------------------------ patient flow

    async def _patient(self, phone: str, msg: InboundMessage) -> HandleResult:
        s = self.services
        now = s.now()
        patient = await s.storage.patients.get(phone) or Patient(phone=phone, created_at=now)
        conversation = await s.storage.conversations.get(phone) or Conversation(phone=phone, updated_at=now)
        text = msg.text.strip()

        conversation.turn += 1
        conversation.last_inbound_at = now
        conversation.add_message("patient", text or f"[botão: {msg.button_payload}]", now)

        # 1) deterministic commands
        command_reply, text = await self._patient_command(patient, msg, text)
        if command_reply is not None:
            return await self._finish(patient, conversation, command_reply, [])

        # 2) state gate
        if conversation.state == ConversationState.HUMAN:
            await self._save(patient, conversation)
            return HandleResult("silent")
        if conversation.state == ConversationState.WAITING_APPROVAL:
            return await self._finish(patient, conversation, waiting_approval_message(s.rules), [])

        if not text:
            return await self._finish(
                patient,
                conversation,
                _TEXT_ONLY,
                [],
            )

        # 3) clinical guard — before the LLM
        hit = check_message(text)
        if hit is not None:
            reason = "urgência relatada pelo paciente" if hit.kind == "urgent" else "assunto clínico"
            handoff_events = await self._handoff(conversation, reason)
            return await self._finish(
                patient, conversation, clinical_handoff_message(s.rules, urgent=hit.kind == "urgent"), handoff_events
            )

        # 4) agent
        deps = TurnDeps(services=s, patient=patient, conversation=conversation, now=now)
        deps.turn.patient_text = text
        history = to_model_history(conversation.messages[:-1], limit=self.history_limit)
        try:
            result = await self.agent.run(text, deps=deps, message_history=history)
            reply = result.output
        except Exception:
            log.exception("agent run failed for %s", mask_phone(phone))
            reply = self._fallback_reply(deps)

        events: list[DomainEvent] = list(deps.turn.events)
        handoff_reason = deps.turn.handoff_reason or (
            (reply.handoff_reason or "solicitado pelo assistente") if reply.handoff else None
        )
        if handoff_reason and deps.turn.approval is None:
            events += await self._handoff(conversation, handoff_reason)
        return await self._finish(patient, conversation, reply.message, events)

    def _fallback_reply(self, deps: TurnDeps) -> AgentReply:
        """When the model fails, still tell the truth about what the code already did."""
        rules = deps.rules
        if deps.turn.outcome is not None and deps.turn.outcome.ok:
            return AgentReply(message=deps.turn.outcome.message, intent="confirmado")
        if deps.turn.approval is not None:
            return AgentReply(message=approval_requested_message(rules, deps.turn.approval.summary), intent="proposta")
        return AgentReply(
            message=technical_error_message(rules), intent="handoff", handoff=True, handoff_reason="erro técnico"
        )

    async def _patient_command(self, patient: Patient, msg: InboundMessage, text: str) -> tuple[str | None, str]:
        """Returns (reply, text). A reply short-circuits the pipeline; ``text`` may be rewritten for the agent."""
        s = self.services
        norm = normalize(text).strip(" .!")
        if norm in _OPT_OUT:
            patient.opted_out = True
            return opt_out_message(), text
        if norm in _OPT_IN:
            patient.opted_out = False
            return opt_in_message(), text

        payload = msg.button_payload or ""
        kind, _, ref = payload.partition(":")
        if kind == "CONFIRM" and ref:
            appointment = await s.storage.appointments.get(ref)
            if appointment and appointment.patient_phone == patient.phone and appointment.is_active:
                appointment.confirmed_at = s.now()
                await s.storage.appointments.save(appointment)
                return attendance_confirmed_message(appointment, s.rules), text
            return "Não encontrei essa consulta ativa. Quer que eu verifique suas consultas?", text
        if kind == "RESCHEDULE" and ref:
            return None, f"Preciso remarcar minha consulta (código {ref})."
        return None, text

    async def _handoff(self, conversation: Conversation, reason: str) -> list[DomainEvent]:
        s = self.services
        conversation.state = ConversationState.HUMAN
        conversation.handoff_reason = reason
        conversation.pending_action = None
        await s.storage.audit.append(AuditEntry(at=s.now(), actor="bot", action="handoff", detail=reason))
        await self.notifier.to_dentist("handoff_alert", patient=conversation.phone, reason=reason)
        return [HandoffRequested(at=s.now(), phone=conversation.phone, reason=reason)]

    async def _finish(
        self, patient: Patient, conversation: Conversation, reply: str, events: list[DomainEvent]
    ) -> HandleResult:
        s = self.services
        try:
            await s.messaging.send_text(patient.phone, reply)
        except MessagingError:
            log.exception("failed to send reply to %s", mask_phone(patient.phone))
        conversation.add_message("bot", reply, s.now())
        await self._save(patient, conversation)
        await self.events.publish(events, s)
        return HandleResult("replied", [reply])

    async def _save(self, patient: Patient, conversation: Conversation) -> None:
        s = self.services
        conversation.trim(s.rules.retention.max_messages)
        conversation.expires_at = s.now() + timedelta(days=s.rules.retention.conversation_days)
        await s.storage.patients.save(patient)
        await s.storage.conversations.save(conversation)

    # ------------------------------------------------------------------ dentist commands

    async def _dentist(self, msg: InboundMessage) -> HandleResult:
        reply, events = await self._dentist_command(msg)
        await self.notifier.reply_dentist(reply)
        await self.events.publish(events, self.services)
        return HandleResult("dentist_command", [reply])

    async def _dentist_command(self, msg: InboundMessage) -> tuple[str, list[DomainEvent]]:
        s = self.services
        payload_kind, _, payload_ref = (msg.button_payload or "").partition(":")
        if payload_kind in ("APPROVE", "DENY") and payload_ref.isdigit():
            decision = await self.approvals.decide(int(payload_ref), approve=payload_kind == "APPROVE")
            return decision.reply_to_dentist, decision.events

        text = msg.text.strip()
        if m := re.fullmatch(r"#(ok|sim|nao|não)\s+(\d+)", text, re.IGNORECASE):
            decision = await self.approvals.decide(int(m.group(2)), approve=m.group(1).lower() in ("ok", "sim"))
            return decision.reply_to_dentist, decision.events
        if re.fullmatch(r"#pendentes", text, re.IGNORECASE):
            pending = await s.storage.approvals.list_pending()
            if not pending:
                return "Nenhum pedido aguardando aprovação.", []
            return "\n".join(f"#{a.code}: {a.summary} ({a.reason})" for a in pending), []
        if m := re.fullmatch(r"#bot\s+([\d+()\s-]+)", text, re.IGNORECASE):
            conversation = await self._find_conversation(m.group(1))
            if conversation is None:
                return "Não encontrei conversa com esse telefone.", []
            phone = conversation.phone
            conversation.state = ConversationState.BOT
            conversation.handoff_reason = None
            await s.storage.conversations.save(conversation)
            await s.storage.audit.append(AuditEntry(at=s.now(), actor="dentist", action="release_to_bot"))
            return f"Assistente reativado para {mask_phone(phone)}.", []
        if m := re.fullmatch(r"#msg\s+(\+?[\d()\s-]{8,20}?)\s+(.+)", text, re.IGNORECASE | re.DOTALL):
            conversation = await self._find_conversation(m.group(1))
            phone = conversation.phone if conversation else normalize_phone(m.group(1))
            await self.notifier.to_patient(phone, m.group(2).strip(), role="dentist")
            return f"Mensagem enviada para {mask_phone(phone)}.", []
        return _DENTIST_HELP, []

    async def _find_conversation(self, raw_phone: str) -> Conversation | None:
        try:
            phone = normalize_phone(raw_phone)
        except ValueError:
            return None
        for candidate in phone_variants(phone):
            conversation = await self.services.storage.conversations.get(candidate)
            if conversation is not None:
                return conversation
        return None
