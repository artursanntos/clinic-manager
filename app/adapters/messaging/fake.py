"""Records outbound messages instead of sending them."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Literal

from app.adapters.messaging.base import TemplateMessage


@dataclass(frozen=True)
class Outbound:
    to: str
    kind: Literal["text", "template"]
    text: str
    template: TemplateMessage | None = None


class FakeMessaging:
    def __init__(self) -> None:
        self.outbox: list[Outbound] = []
        self._ids = itertools.count(1)

    async def send_text(self, to: str, text: str) -> str:
        self.outbox.append(Outbound(to=to, kind="text", text=text))
        return f"wamid.fake.{next(self._ids)}"

    async def send_template(self, to: str, template: TemplateMessage) -> str:
        self.outbox.append(Outbound(to=to, kind="template", text=template.preview, template=template))
        return f"wamid.fake.{next(self._ids)}"

    def to(self, phone: str) -> list[Outbound]:
        return [m for m in self.outbox if m.to == phone]

    def clear(self) -> None:
        self.outbox.clear()
