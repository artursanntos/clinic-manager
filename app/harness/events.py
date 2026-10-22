"""In-process event bus. Handler failures are logged and never break the patient flow."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence

from app.domain.events import DomainEvent
from app.harness.capability import BaseCapability
from app.harness.context import Services

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, capabilities: Sequence[BaseCapability]) -> None:
        self.capabilities = list(capabilities)

    async def publish(self, events: Iterable[DomainEvent], services: Services) -> None:
        for event in events:
            for capability in self.capabilities:
                try:
                    await capability.on_event(event, services)
                except Exception:
                    log.exception("capability %s failed handling %s", capability.name, event.type)
