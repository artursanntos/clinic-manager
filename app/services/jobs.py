"""Core scheduled jobs (always on): reminders and LGPD retention cleanup. Capabilities add more jobs."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, time, timedelta

from app.domain.models import AppointmentStatus
from app.harness.capability import JobHandler, JobResult
from app.harness.context import Services
from app.harness.notify import Notifier
from app.services.messages import format_when

log = logging.getLogger(__name__)


async def send_reminders(services: Services) -> JobResult:
    """Reminder template for every active appointment tomorrow. Idempotent via ``reminder_sent_at``."""
    result = JobResult(name="reminders")
    now = services.now()
    tz = services.rules.tz
    tomorrow = now.astimezone(tz).date() + timedelta(days=1)
    start = datetime.combine(tomorrow, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    notifier = Notifier(services)
    for appointment in await services.storage.appointments.list_between(start, end, status=AppointmentStatus.BOOKED):
        result.processed += 1
        if appointment.reminder_sent_at is not None:
            result.skipped += 1
            continue
        procedure = services.rules.procedure(appointment.procedure_id)
        ok = await notifier.template_to_patient(
            appointment.patient_phone,
            "appointment_reminder",
            ref=appointment.id,
            patient_name=appointment.patient_name.split()[0],
            procedure=procedure.name if procedure else appointment.procedure_id,
            when=format_when(appointment.start, tz),
        )
        if ok:
            appointment.reminder_sent_at = now
            await services.storage.appointments.save(appointment)
            result.sent += 1
        else:
            result.errors += 1
    return result


async def cleanup(services: Services) -> JobResult:
    """LGPD retention: delete stale conversations and old dedupe ids (Firestore TTL is a second safety net)."""
    result = JobResult(name="cleanup")
    now = services.now()
    retention = services.rules.retention
    stale = await services.storage.conversations.list_updated_before(now - timedelta(days=retention.conversation_days))
    for conversation in stale:
        await services.storage.conversations.delete(conversation.phone)
        result.processed += 1
    result.processed += await services.storage.processed.purge_before(
        now - timedelta(days=retention.processed_message_days)
    )
    return result


def core_jobs() -> Mapping[str, JobHandler]:
    return {"reminders": send_reminders, "cleanup": cleanup}
