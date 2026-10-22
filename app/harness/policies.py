"""PolicyEnforcer: applies every capability's policies to every tool call (PydanticAI capability hook)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, ValidatedToolArgs, WrapToolExecuteHandler
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from app.domain.models import AuditEntry, ConversationState
from app.harness.approvals import ApprovalService
from app.harness.capability import Allow, Deny, PolicyDecision, RequireApproval, ToolPolicy
from app.harness.context import TurnDeps
from app.services.messages import approval_requested_message

log = logging.getLogger(__name__)

WRITE_TOOLS = frozenset({"confirm_pending_action"})


class PolicyEnforcer(AbstractCapability[TurnDeps]):
    def __init__(self, policies: Sequence[ToolPolicy], approvals: ApprovalService) -> None:
        self.policies = list(policies)
        self.approvals = approvals

    async def decide(self, deps: TurnDeps, tool_name: str, args: dict[str, Any]) -> PolicyDecision:
        """First Deny wins; otherwise first RequireApproval; otherwise Allow."""
        approval: RequireApproval | None = None
        for policy in self.policies:
            decision = await policy(deps, tool_name, args)
            if isinstance(decision, Deny):
                return decision
            if isinstance(decision, RequireApproval) and approval is None:
                approval = decision
        return approval or Allow()

    async def wrap_tool_execute(
        self,
        ctx: RunContext[TurnDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        deps = ctx.deps
        decision = await self.decide(deps, call.tool_name, args)
        audit = deps.services.storage.audit

        if isinstance(decision, Deny):
            await audit.append(
                AuditEntry(at=deps.now, actor="bot", action=f"denied:{call.tool_name}", detail=decision.reason)
            )
            result = f"NÃO PERMITIDO: {decision.reason}"
            deps.turn.tool_texts.append(result)
            return result

        if isinstance(decision, RequireApproval):
            pending = deps.conversation.pending_action
            action = decision.action or (pending.action if pending else None)
            if action is None:
                log.error("RequireApproval without an action for tool %s", call.tool_name)
                return "NÃO PERMITIDO: ação indisponível no momento."
            summary = pending.summary if pending else decision.reason
            approval = await self.approvals.request(
                phone=deps.patient.phone,
                patient_name=deps.patient.name,
                action=action,
                summary=summary,
                reason=decision.reason,
            )
            deps.turn.approval = approval
            deps.conversation.pending_action = None
            deps.conversation.state = ConversationState.WAITING_APPROVAL
            result = (
                "PEDIDO ENVIADO PARA APROVAÇÃO DA DOUTORA (nada foi marcado ainda). "
                f"Diga ao paciente exatamente: {approval_requested_message(deps.rules, summary)}"
            )
            deps.turn.tool_texts.append(result)
            return result

        result = await handler(args)
        if call.tool_name in WRITE_TOOLS:
            await audit.append(AuditEntry(at=deps.now, actor="bot", action=f"tool:{call.tool_name}"))
        if isinstance(result, str):
            deps.turn.tool_texts.append(result)
        return result
