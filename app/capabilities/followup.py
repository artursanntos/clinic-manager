"""Optional capability: post-visit follow-up with the Google review link. No clinical questions, ever."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from app.domain.models import AppointmentStatus
from app.domain.rules import ClinicRules
from app.harness.capability import BaseCapability, JobHandler, JobResult
from app.harness.context import Services
from app.harness.notify import Notifier


class FollowupCapability(BaseCapability):
    name = "followup"

    def __init__(self, rules: ClinicRules, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(rules, config)
        self.review_url = str(self.config.get("review_url", ""))
        self.min_delay = timedelta(hours=float(self.config.get("min_delay_hours", 2)))
        self.max_delay = timedelta(hours=float(self.config.get("max_delay_hours", 48)))

    def jobs(self) -> Mapping[str, JobHandler]:
        return {"followup": self.run}

    async def run(self, services: Services) -> JobResult:
        """Appointments that ended between ``max_delay`` and ``min_delay`` ago. Idempotent via followup_sent_at."""
        result = JobResult(name="followup")
        if not self.review_url:
            return result
        now = services.now()
        notifier = Notifier(services)
        candidates = await services.storage.appointments.list_between(
            now - self.max_delay - timedelta(hours=8), now, status=None
        )
        for appointment in candidates:
            if not (now - self.max_delay <= appointment.end <= now - self.min_delay):
                continue
            result.processed += 1
            if appointment.status == AppointmentStatus.CANCELLED or appointment.followup_sent_at is not None:
                result.skipped += 1
                continue
            patient = await services.storage.patients.get(appointment.patient_phone)
            if patient is None or patient.opted_out:
                result.skipped += 1
                continue
            ok = await notifier.template_to_patient(
                appointment.patient_phone,
                "post_visit_followup",
                patient_name=appointment.patient_name.split()[0],
                review_url=self.review_url,
            )
            if ok:
                appointment.followup_sent_at = now
                await services.storage.appointments.save(appointment)
                result.sent += 1
            else:
                result.errors += 1
        return result
