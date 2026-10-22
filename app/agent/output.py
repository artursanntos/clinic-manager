"""Structured output of the agent. The harness never parses free text."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["conversa", "oferecer_horarios", "proposta", "confirmado", "informacao", "handoff"]


class AgentReply(BaseModel):
    message: str = Field(description="Mensagem para o paciente, em português do Brasil, curta e cordial.")
    intent: Intent = Field(description="O que esta mensagem faz.")
    handoff: bool = Field(default=False, description="true se a conversa deve passar para a doutora.")
    handoff_reason: str | None = Field(default=None, description="Motivo curto do handoff, sem detalhes clínicos.")
