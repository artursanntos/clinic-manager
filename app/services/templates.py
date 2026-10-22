"""WhatsApp templates (config/templates.yaml). Templates must be approved in the Meta dashboard first."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.adapters.messaging.base import TemplateMessage


class TemplateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    """Template name as approved in Meta."""
    category: str = "UTILITY"
    params: list[str] = Field(default_factory=list)
    """Body parameter names, in the order of {{1}}, {{2}}…"""
    body: str
    """Preview text with ``{param}`` placeholders (for CLI/tests and as the submission text)."""
    buttons: list[str] = Field(default_factory=list)
    """Quick-reply payload prefixes; payload sent = ``PREFIX:<ref>``."""


class TemplatesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    language: str = "pt_BR"
    templates: dict[str, TemplateSpec]


class TemplateRegistry:
    def __init__(self, config: TemplatesConfig) -> None:
        self.config = config

    @classmethod
    def load(cls, path: str | Path) -> TemplateRegistry:
        with Path(path).open(encoding="utf-8") as fh:
            return cls(TemplatesConfig.model_validate(yaml.safe_load(fh)))

    def __contains__(self, key: str) -> bool:
        return key in self.config.templates

    def build(self, key: str, *, ref: str | None = None, **values: str) -> TemplateMessage:
        spec = self.config.templates.get(key)
        if spec is None:
            raise KeyError(f"unknown template: {key}")
        missing = [p for p in spec.params if p not in values]
        if missing:
            raise ValueError(f"template {key} missing params: {missing}")
        if spec.buttons and ref is None:
            raise ValueError(f"template {key} has buttons and needs a ref")
        return TemplateMessage(
            name=spec.name,
            language=self.config.language,
            body_params=[values[p] for p in spec.params],
            button_payloads=[f"{prefix}:{ref}" for prefix in spec.buttons],
            preview=spec.body.format(**values),
        )
