"""The ONLY place that knows which LLM vendor is in use. See ADR 0007.

``AGENT_MODEL`` uses PydanticAI's ``provider:model`` strings, e.g. ``openrouter:<model>``, ``anthropic:<model>``,
``google:<model>``, ``openai:<model>``. API keys come from each provider's standard env var.
``test`` selects the deterministic scripted model (no network), used by the CLI demo, CI evals and tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.output import NativeOutput, OutputSpec, PromptedOutput, ToolOutput
from pydantic_ai.settings import ModelSettings

from app.agent.output import AgentReply
from app.domain.rules import ClinicRules

OutputMode = Literal["tool", "native", "prompted"]
TEST_MODEL = "test"


def parse_model_list(raw: str | None) -> list[str]:
    return [m.strip() for m in (raw or "").split(",") if m.strip()]


def resolve_model(spec: str, fallbacks: Sequence[str] = (), *, rules: ClinicRules | None = None) -> Model:
    """Build the model (with optional fallbacks). Raises if a provider's API key is missing."""
    specs = [spec, *fallbacks]
    models: list[Model] = []
    for s in specs:
        if s == TEST_MODEL:
            if rules is None:
                raise ValueError("the scripted test model needs the clinic rules")
            from app.agent.scripted import scripted_model

            models.append(scripted_model(rules))
        else:
            models.append(infer_model(s))
    if len(models) == 1:
        return models[0]
    return FallbackModel(models[0], *models[1:])


def output_spec(mode: OutputMode) -> OutputSpec[AgentReply]:
    if mode == "native":
        return NativeOutput(AgentReply)
    if mode == "prompted":
        return PromptedOutput(AgentReply)
    return ToolOutput(AgentReply, name="final_reply", description="Envia a resposta final ao paciente.")


def model_settings(*, temperature: float = 0.0, max_tokens: int = 800) -> ModelSettings:
    return ModelSettings(temperature=temperature, max_tokens=max_tokens)
