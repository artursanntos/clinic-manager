"""Deterministic scripted model (``AGENT_MODEL=test``): no network, no API key.

Used by ``clinic chat --model test``, the CI eval run and tests. It follows the same tool protocol as a real model,
so it exercises the whole harness (tools, policies, validators, orchestrator). It is intentionally simple and
keyword-based; it is NOT meant to be a good conversational agent.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.domain.rules import ClinicRules
from app.harness.guard import normalize

_SLOT_RE = re.compile(r"\bH(\d+)\b", re.IGNORECASE)
_NAME_RE = re.compile(r"(?:meu nome (?:e|é)|me chamo|sou (?:a|o))\s+([A-Za-zÀ-ÿ' ]{2,60})", re.IGNORECASE)
_CODE_RE = re.compile(r"\(código ([0-9a-f]{12})\)")
_YES = {"sim", "confirmo", "confirmar", "pode", "pode sim", "isso", "ok", "fechado", "certo", "pode confirmar"}
_NO = {"nao", "não", "nao quero", "mudei de ideia"}


def _last_user_text(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return ""


def _reply(info: AgentInfo, message: str, intent: str = "conversa", handoff: bool = False) -> ModelResponse:
    payload: dict[str, Any] = {"message": message, "intent": intent, "handoff": handoff}
    if info.output_tools:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])
    return ModelResponse(parts=[TextPart(json.dumps(payload, ensure_ascii=False))])


def _call(tool: str, **args: Any) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, args)])


class ScriptedBrain:
    def __init__(self, rules: ClinicRules) -> None:
        self.rules = rules

    def _procedure_for(self, text: str) -> str | None:
        norm = normalize(text)
        for procedure in self.rules.procedures:
            terms = [procedure.id, procedure.name, *procedure.aliases]
            if any(normalize(t) in norm for t in terms):
                return procedure.id
        return None

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        user_text = _last_user_text(messages)
        if isinstance(last, ModelRequest):
            retries = [p for p in last.parts if isinstance(p, RetryPromptPart)]
            returns = [p for p in last.parts if isinstance(p, ToolReturnPart)]
            if retries:
                return _reply(info, "Desculpe, vou pedir para a doutora te responder.", "handoff", handoff=True)
            if returns:
                return self._after_tool(returns[-1], user_text, info)
        return self._on_user(user_text, info)

    # ------------------------------------------------------------------ user message → tool call
    def _on_user(self, text: str, info: AgentInfo) -> ModelResponse:
        norm = normalize(text).strip(" .!?")
        if norm in _YES:
            return _call("confirm_pending_action")
        if norm in _NO:
            return _call("cancel_pending_action")
        if slot := _SLOT_RE.search(text):
            name = _NAME_RE.search(text)
            if not name:
                return _reply(info, "Perfeito! Qual é o seu nome completo?")
            return _call("propose_booking", slot_id=f"H{slot.group(1)}", patient_name=name.group(1).strip())
        if any(w in norm for w in ("endereco", "onde fica", "localizacao")):
            return _call("get_clinic_info", topic="endereco")
        if any(w in norm for w in ("preco", "valor", "quanto custa")):
            return _call("get_clinic_info", topic="procedimentos_e_precos")
        if "convenio" in norm or "plano" in norm:
            return _call("get_clinic_info", topic="convenios")
        if any(w in norm for w in ("horario de atendimento", "que horas abre", "funciona")):
            return _call("get_clinic_info", topic="horario_atendimento")
        if "cancelar" in norm or "minhas consultas" in norm or "remarcar" in norm:
            return _call("list_my_appointments")
        if "humano" in norm or "falar com a doutora" in norm or "atendente" in norm:
            return _call("request_handoff", reason="paciente pediu atendimento humano")
        procedure = self._procedure_for(text)
        if procedure or any(w in norm for w in ("marcar", "agendar", "horario", "consulta")):
            period = "tarde" if "tarde" in norm else "manha" if "manha" in norm else "qualquer"
            return _call("find_available_slots", procedure_id=procedure or "avaliacao", period=period)
        return _reply(
            info,
            "Olá! Posso ajudar a marcar, remarcar ou cancelar consultas e tirar dúvidas sobre o consultório. "
            "O que você precisa?",
        )

    # ------------------------------------------------------------------ tool result → next step / reply
    def _after_tool(self, ret: ToolReturnPart, user_text: str, info: AgentInfo) -> ModelResponse:
        content = str(ret.content)
        tool = ret.tool_name
        if tool == "find_available_slots":
            if "H" in content and "Horários disponíveis" in content:
                lines = [ln for ln in content.splitlines() if re.match(r"H\d+ ", ln)]
                return _reply(
                    info,
                    "Tenho estes horários:\n"
                    + "\n".join(lines)
                    + "\nQual prefere? Me diga o código e seu nome completo.",
                    "oferecer_horarios",
                )
            return _reply(info, "Não encontrei horários livres nesse período. Quer tentar outro período?")
        if tool in ("propose_booking", "propose_cancel", "propose_reschedule") and "«" in content:
            summary = content.split("«", 1)[1].split("»", 1)[0]
            return _reply(info, f"Confirma? {summary}. Responda SIM para confirmar.", "proposta")
        if tool == "confirm_pending_action":
            if "Informe ao paciente exatamente:" in content:
                return _reply(info, content.split("exatamente:", 1)[1].strip(), "confirmado")
            if "exatamente:" in content:  # sent for approval
                return _reply(info, content.split("exatamente:", 1)[1].strip(), "proposta")
            return _reply(info, "Não consegui concluir. Quer que eu procure outros horários?")
        if tool == "list_my_appointments":
            code = _CODE_RE.search(content)
            if code and "cancelar" in normalize(user_text):
                return _call("propose_cancel", appointment_id=code.group(1))
            return _reply(info, content, "informacao")
        if tool == "request_handoff":
            return _reply(info, content.split("Diga ao paciente:", 1)[-1].strip(), "handoff", handoff=True)
        if tool == "cancel_pending_action":
            return _reply(info, "Tudo bem, não alterei nada. Posso ajudar com mais alguma coisa?")
        return _reply(info, content, "informacao")


def scripted_model(rules: ClinicRules) -> FunctionModel:
    return FunctionModel(ScriptedBrain(rules), model_name="scripted")
