from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic_ai.models import Model

from app.adapters.calendar.fake import FakeCalendar
from app.adapters.messaging.base import InboundMessage
from app.adapters.messaging.fake import FakeMessaging
from app.adapters.storage.memory import InMemoryStorage
from app.agent.scripted import scripted_model
from app.config import Settings
from app.container import Container, build_container
from app.domain.rules import ClinicRules
from tests.helpers import at, make_rules, rules_dict

ROOT = Path(__file__).resolve().parents[1]
PATIENT = "5581988887777"
DENTIST = "5581900000001"


@pytest.fixture
def rules() -> ClinicRules:
    return make_rules()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@dataclass
class Harness:
    container: Container
    calendar: FakeCalendar
    messaging: FakeMessaging
    storage: InMemoryStorage
    clock: Clock
    _counter: int = 0

    async def send(self, text: str = "", *, phone: str = PATIENT, button: str | None = None) -> list[str]:
        self._counter += 1
        msg = InboundMessage(
            message_id=f"wamid.test.{self._counter}",
            from_phone=phone,
            timestamp=self.clock.now,
            text=text,
            button_payload=button,
        )
        result = await self.container.orchestrator.handle(msg)
        return result.replies

    @property
    def services(self) -> Any:
        return self.container.services


def write_rules(tmp_path: Path, **overrides: Any) -> Path:
    path = tmp_path / "clinic.yaml"
    data = rules_dict(**overrides)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    return path


HarnessFactory = Callable[..., Harness]


@pytest.fixture
def make_harness(tmp_path: Path) -> HarnessFactory:
    def factory(
        model: Model | None = None,
        *,
        now: datetime | None = None,
        skills_dir: str | None = None,
        **rule_overrides: Any,
    ) -> Harness:
        clock = Clock(now or at(2026, 10, 25, 20, 0))  # Sunday night; Monday 26/10 is free
        calendar, messaging, storage = FakeCalendar(), FakeMessaging(), InMemoryStorage()
        rules_path = write_rules(tmp_path, **rule_overrides)
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            clinic_config_path=str(rules_path),
            templates_path=str(ROOT / "config" / "templates.yaml"),
            skills_dir=skills_dir or str(ROOT / "skills"),
        )
        container = build_container(
            settings,
            model=model or scripted_model(ClinicRules.model_validate(rules_dict(**rule_overrides))),
            calendar=calendar,
            storage=storage,
            messaging=messaging,
            clock=clock,
        )
        return Harness(container, calendar, messaging, storage, clock)

    return factory
