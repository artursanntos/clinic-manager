"""Builds the PydanticAI agent from capabilities. Vendor-agnostic: the model is passed in."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.toolsets import AbstractToolset

from app.agent.model import OutputMode, model_settings, output_spec
from app.agent.output import AgentReply
from app.agent.prompt import base_prompt, state_block
from app.agent.validators import check_reply
from app.domain.models import ChatMessage
from app.domain.rules import ClinicRules
from app.harness.capability import BaseCapability
from app.harness.context import TurnDeps
from app.harness.policies import PolicyEnforcer

ClinicAgent = Agent[TurnDeps, AgentReply]


def build_agent(
    *,
    model: Model,
    rules: ClinicRules,
    capabilities: Sequence[BaseCapability],
    enforcer: PolicyEnforcer,
    output_mode: OutputMode = "tool",
    settings: ModelSettings | None = None,
    retries: int = 2,
) -> ClinicAgent:
    instructions = [base_prompt(rules)] + [i for c in capabilities if (i := c.instructions())]
    toolsets: list[AbstractToolset[TurnDeps]] = [t for c in capabilities if (t := c.toolset()) is not None]

    agent: ClinicAgent = Agent(
        model,
        deps_type=TurnDeps,
        output_type=output_spec(output_mode),
        instructions="\n\n".join(instructions),
        toolsets=toolsets,
        capabilities=[enforcer],
        model_settings=settings or model_settings(),
        retries=retries,
        name="clinic-assistant",
    )

    @agent.instructions
    def _state(ctx: RunContext[TurnDeps]) -> str:
        return state_block(ctx.deps)

    @agent.output_validator
    def _validate(ctx: RunContext[TurnDeps], reply: AgentReply) -> AgentReply:
        problem = check_reply(reply.message, ctx.deps)
        if problem:
            raise ModelRetry(problem)
        return reply

    return agent


def to_model_history(messages: Sequence[ChatMessage], *, limit: int = 12) -> list[ModelMessage]:
    """Convert stored chat messages into PydanticAI history (consecutive same-side messages are grouped)."""
    history: list[ModelMessage] = []
    for msg in list(messages)[-limit:]:
        if msg.role == "system":
            continue
        if msg.role == "patient":
            part_u = UserPromptPart(content=msg.text)
            if history and isinstance(history[-1], ModelRequest):
                history[-1] = ModelRequest(parts=[*history[-1].parts, part_u])
            else:
                history.append(ModelRequest(parts=[part_u]))
        else:
            text = msg.text if msg.role == "bot" else f"(mensagem enviada pela doutora) {msg.text}"
            part_t = TextPart(content=text)
            if history and isinstance(history[-1], ModelResponse):
                history[-1] = ModelResponse(parts=[*history[-1].parts, part_t])
            else:
                history.append(ModelResponse(parts=[part_t]))
    # Providers expect the history to start with a request.
    while history and isinstance(history[0], ModelResponse):
        history.pop(0)
    return history
