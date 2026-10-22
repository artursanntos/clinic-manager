"""Composition root: builds every component from Settings. The only place that picks implementations.

External SDKs are imported lazily inside the builder functions, so the core never depends on them and an
unused provider's extra doesn't need to be installed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic_ai.models import Model

from app.adapters.calendar.base import CalendarProvider
from app.adapters.messaging.base import MessagingChannel
from app.adapters.storage.base import Storage
from app.agent.factory import ClinicAgent, build_agent
from app.agent.model import model_settings, parse_model_list, resolve_model
from app.capabilities import build_capabilities
from app.config import Settings
from app.domain.rules import ClinicRules, load_rules
from app.harness.approvals import ApprovalService
from app.harness.capability import BaseCapability, JobHandler, JobResult
from app.harness.context import Services
from app.harness.events import EventBus
from app.harness.notify import Notifier
from app.harness.orchestrator import Orchestrator
from app.harness.policies import PolicyEnforcer
from app.harness.skills import SkillLibrary
from app.services.booking import BookingService
from app.services.jobs import core_jobs
from app.services.templates import TemplateRegistry


class ConfigurationError(RuntimeError):
    pass


def _require[T](value: T | None, name: str) -> T:
    if value is None or value == "":
        raise ConfigurationError(f"missing required setting: {name}")
    return value


# --------------------------------------------------------------------------- adapters


def build_calendar(settings: Settings, timezone: str) -> CalendarProvider:
    match settings.calendar_provider:
        case "fake":
            from app.adapters.calendar.fake import FakeCalendar

            return FakeCalendar()
        case "google":
            from app.adapters.calendar.google import GoogleCalendar

            return GoogleCalendar(
                calendar_id=_require(settings.google_calendar_id, "GOOGLE_CALENDAR_ID"),
                timezone=timezone,
                credentials_file=settings.google_credentials_file,
            )
        case "microsoft":
            from app.adapters.calendar.microsoft import MicrosoftCalendar, MicrosoftTokenProvider

            secret = settings.ms_client_secret.get_secret_value() if settings.ms_client_secret else None
            refresh = settings.ms_refresh_token.get_secret_value() if settings.ms_refresh_token else None
            tokens = MicrosoftTokenProvider(
                mode=settings.ms_auth_mode,
                tenant_id=settings.ms_tenant_id,
                client_id=_require(settings.ms_client_id, "MS_CLIENT_ID"),
                client_secret=secret,
                refresh_token=refresh,
            )
            return MicrosoftCalendar(
                tokens,
                user_id=settings.ms_user_id if settings.ms_auth_mode == "client_credentials" else None,
                calendar_id=settings.ms_calendar_id,
                timezone=timezone,
            )
        case "caldav":
            from app.adapters.calendar.caldav import CalDavCalendar

            return CalDavCalendar(
                url=_require(settings.caldav_url, "CALDAV_URL"),
                username=_require(settings.caldav_username, "CALDAV_USERNAME"),
                password=_require(settings.caldav_password, "CALDAV_PASSWORD").get_secret_value(),
                calendar_name=settings.caldav_calendar_name,
                timezone=timezone,
            )


def build_storage(settings: Settings) -> Storage:
    match settings.storage_backend:
        case "memory":
            from app.adapters.storage.memory import InMemoryStorage

            return InMemoryStorage()
        case "firestore":
            from app.adapters.storage.firestore import FirestoreStorage

            return FirestoreStorage.create(
                project=settings.firestore_project,
                database=settings.firestore_database,
                prefix=settings.firestore_collection_prefix,
            )


def build_messaging(settings: Settings) -> MessagingChannel:
    match settings.messaging_provider:
        case "fake":
            from app.adapters.messaging.fake import FakeMessaging

            return FakeMessaging()
        case "whatsapp_cloud":
            from app.adapters.messaging.whatsapp_cloud import WhatsAppCloudClient

            return WhatsAppCloudClient(
                access_token=_require(settings.whatsapp_access_token, "WHATSAPP_ACCESS_TOKEN").get_secret_value(),
                phone_number_id=_require(settings.whatsapp_phone_number_id, "WHATSAPP_PHONE_NUMBER_ID"),
                api_version=settings.whatsapp_api_version,
            )


# --------------------------------------------------------------------------- container


def _daily(jobs: Mapping[str, JobHandler]) -> JobHandler:
    """One Cloud Scheduler job for everything (free tier covers 3 jobs per billing account)."""

    async def run(services: Services) -> JobResult:
        total = JobResult(name="daily")
        for handler in jobs.values():
            try:
                result = await handler(services)
            except Exception:
                logging.getLogger(__name__).exception("job failed inside daily run")
                total.errors += 1
                continue
            total.processed += result.processed
            total.sent += result.sent
            total.skipped += result.skipped
            total.errors += result.errors
        return total

    return run


@dataclass
class Container:
    settings: Settings
    services: Services
    capabilities: list[BaseCapability]
    agent: ClinicAgent
    orchestrator: Orchestrator
    approvals: ApprovalService
    events: EventBus
    jobs: Mapping[str, JobHandler]

    @property
    def rules(self) -> ClinicRules:
        return self.services.rules


def build_container(
    settings: Settings,
    *,
    model: Model | None = None,
    calendar: CalendarProvider | None = None,
    storage: Storage | None = None,
    messaging: MessagingChannel | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Container:
    """Build everything. Keyword overrides are for tests and the CLI."""
    rules = load_rules(settings.clinic_config_path)
    templates = TemplateRegistry.load(settings.templates_path)
    skills_dir = Path(settings.skills_dir)
    skills = SkillLibrary.load_dir(skills_dir) if skills_dir.is_dir() else SkillLibrary()

    calendar = calendar or build_calendar(settings, rules.timezone)
    storage = storage or build_storage(settings)
    messaging = messaging or build_messaging(settings)
    services = Services(
        rules=rules,
        storage=storage,
        calendar=calendar,
        messaging=messaging,
        templates=templates,
        booking=BookingService(rules, calendar, storage),
        clock=clock or (lambda: datetime.now(UTC)),
    )

    capabilities = build_capabilities(rules, skills)
    approvals = ApprovalService(services, Notifier(services))
    enforcer = PolicyEnforcer([p for c in capabilities for p in c.policies()], approvals)

    if model is None:
        spec = _require(settings.agent_model, "AGENT_MODEL")
        model = resolve_model(spec, parse_model_list(settings.agent_fallback_models), rules=rules)
    agent = build_agent(
        model=model,
        rules=rules,
        capabilities=capabilities,
        enforcer=enforcer,
        output_mode=settings.agent_output_mode,
        settings=model_settings(temperature=settings.agent_temperature, max_tokens=settings.agent_max_tokens),
        retries=settings.agent_retries,
    )
    events = EventBus(capabilities)
    orchestrator = Orchestrator(services, agent, approvals, events, history_limit=settings.agent_history_messages)

    jobs: dict[str, JobHandler] = dict(core_jobs())
    for capability in capabilities:
        for name, handler in capability.jobs().items():
            if name in jobs:
                raise ConfigurationError(f"duplicate job name: {name}")
            jobs[name] = handler

    jobs["daily"] = _daily(dict(jobs))

    return Container(
        settings=settings,
        services=services,
        capabilities=capabilities,
        agent=agent,
        orchestrator=orchestrator,
        approvals=approvals,
        events=events,
        jobs=jobs,
    )
