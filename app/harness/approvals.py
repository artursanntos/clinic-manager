"""Human-in-the-loop approvals: the dentist approves or denies a domain action. See ADR 0004."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.events import DomainEvent
from app.domain.models import (
    ApprovalRequest,
    ApprovalStatus,
    AuditEntry,
    ConversationState,
    DomainAction,
    mask_phone,
)
from app.harness.context import Services
from app.harness.notify import Notifier


@dataclass
class Decision:
    reply_to_dentist: str
    events: list[DomainEvent] = field(default_factory=list)


class ApprovalService:
    def __init__(self, services: Services, notifier: Notifier) -> None:
        self.services = services
        self.notifier = notifier

    async def request(
        self, *, phone: str, patient_name: str | None, action: DomainAction, summary: str, reason: str
    ) -> ApprovalRequest:
        s = self.services
        approval = ApprovalRequest(
            code=await s.storage.approvals.next_code(),
            phone=phone,
            patient_name=patient_name,
            action=action,
            summary=summary,
            reason=reason,
            created_at=s.now(),
        )
        await s.storage.approvals.save(approval)
        await s.storage.audit.append(
            AuditEntry(at=s.now(), actor="bot", action="approval_requested", ref_id=approval.id, detail=reason)
        )
        await self.notifier.to_dentist(
            "approval_request",
            ref=str(approval.code),
            code=str(approval.code),
            patient=f"{patient_name or 'Paciente'} ({phone})",
            summary=summary,
            reason=reason,
        )
        return approval

    async def decide(self, code: int, *, approve: bool) -> Decision:
        s = self.services
        approval = await s.storage.approvals.get_by_code(code)
        if approval is None:
            return Decision(f"Não encontrei o pedido #{code}.")
        if approval.status != ApprovalStatus.PENDING:
            return Decision(f"O pedido #{code} já foi resolvido ({approval.status.value}).")

        now = s.now()
        events: list[DomainEvent] = []
        if approve:
            outcome = await s.booking.execute(approval.action, approval.phone, now)
            if outcome.ok:
                approval.status = ApprovalStatus.APPROVED
                patient_text = f"Boa notícia: a doutora confirmou! {outcome.message}"
                dentist_text = f"Pedido #{code} aprovado e registrado na agenda."
                events = outcome.events
            else:
                approval.status = ApprovalStatus.FAILED
                patient_text = (
                    f"A doutora aprovou, mas {outcome.message[0].lower()}{outcome.message[1:]} "
                    "Se quiser, me responda que eu procuro outros horários."
                )
                dentist_text = f"Pedido #{code} aprovado, mas não foi possível registrar: {outcome.message}"
        else:
            approval.status = ApprovalStatus.DENIED
            patient_text = (
                "A doutora não conseguiu confirmar esse horário. Se quiser, me responda que eu procuro outras opções."
            )
            dentist_text = f"Pedido #{code} recusado. Avisei o paciente."

        approval.decided_at = now
        await s.storage.approvals.save(approval)
        await s.storage.audit.append(
            AuditEntry(at=now, actor="dentist", action=f"approval_{approval.status.value}", ref_id=approval.id)
        )

        conversation = await s.storage.conversations.get(approval.phone)
        if conversation is not None and conversation.state == ConversationState.WAITING_APPROVAL:
            conversation.state = ConversationState.BOT
            conversation.pending_action = None
            await s.storage.conversations.save(conversation)
        await self.notifier.to_patient(approval.phone, patient_text)
        return Decision(f"{dentist_text} (paciente {mask_phone(approval.phone)})", events)
