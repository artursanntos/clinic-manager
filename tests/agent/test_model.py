"""Vendor-agnostic model resolution (ADR 0007). No network: models are only constructed."""

from __future__ import annotations

import pytest
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.output import NativeOutput, PromptedOutput, ToolOutput

from app.agent.model import output_spec, parse_model_list, resolve_model
from app.domain.rules import ClinicRules


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(var, "test-key-not-real")


@pytest.mark.parametrize(
    ("spec", "system"),
    [
        ("openrouter:meta-llama/llama-3.3-70b-instruct:free", "openrouter"),
        ("anthropic:claude-haiku-4-5", "anthropic"),
        ("google:gemini-2.5-flash", "google"),
        ("openai:gpt-4.1-mini", "openai"),
    ],
)
def test_resolves_each_vendor(spec: str, system: str, rules: ClinicRules) -> None:
    model = resolve_model(spec, rules=rules)
    assert system in model.system


def test_fallback_chain(rules: ClinicRules) -> None:
    model = resolve_model("openrouter:a/b:free", ["openrouter:c/d:free", "anthropic:claude-haiku-4-5"], rules=rules)
    assert isinstance(model, FallbackModel)
    assert len(model.models) == 3


def test_scripted_test_model(rules: ClinicRules) -> None:
    assert isinstance(resolve_model("test", rules=rules), FunctionModel)


def test_parse_model_list() -> None:
    assert parse_model_list(" a:b , c:d ,,") == ["a:b", "c:d"]
    assert parse_model_list(None) == []


def test_output_modes() -> None:
    assert isinstance(output_spec("tool"), ToolOutput)
    assert isinstance(output_spec("native"), NativeOutput)
    assert isinstance(output_spec("prompted"), PromptedOutput)
