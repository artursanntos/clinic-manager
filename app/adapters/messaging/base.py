"""Messaging channel protocol (WhatsApp Cloud API in production)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


class MessagingError(Exception):
    """Send failure. Messages must not contain patient data."""


@dataclass(frozen=True)
class InboundMessage:
    """A channel-agnostic inbound message."""

    message_id: str
    from_phone: str
    timestamp: datetime
    text: str = ""
    button_payload: str | None = None
    """Quick-reply button payload (e.g. ``CONFIRM:<appointment_id>``)."""
    profile_name: str | None = None


@dataclass(frozen=True)
class TemplateMessage:
    """A pre-approved template (required outside the 24h customer-service window)."""

    name: str
    language: str
    body_params: list[str] = field(default_factory=list)
    button_payloads: list[str] = field(default_factory=list)
    preview: str = ""
    """Rendered text for logs/CLI/tests only; the channel sends ``name`` + params."""


@runtime_checkable
class MessagingChannel(Protocol):
    async def send_text(self, to: str, text: str) -> str:
        """Free-form text (only inside the 24h window). Returns the provider message id."""
        ...

    async def send_template(self, to: str, template: TemplateMessage) -> str: ...
