"""Optional capability: recall ("time for your check-up") after N months without visits."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from app.domain.models import AppointmentStatus
from app.domain.rules import ClinicRules
from app.harness.capability import BaseCapability, JobHandler, JobResult
from app.harness.context import Services
from app.harness.notify import Notifier


class RecallCapability(BaseCapability):
    name = "recall"

    def __init__(self, rules: ClinicRules, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(rules, config)
        self.after = timedelta(days=30 * int(self.config.get("after_months", 6)))
        ids = self.config.get("procedure_ids")
        self.procedure_ids: set[str] | None = set(ids) if ids else None

    def jobs(self) -> Mapping[str, JobHandler]:
        return {"recall": self.run}

    async def run(self, services: Services) -> JobResult:
        """Idempotent: a patient is recalled at most once per last visit."""
        result = JobResult(name="recall")
        now = services.now()
        notifier = Notifier(services)
        for patient in await services.storage.patients.list_all():
            result.processed += 1
            if patient.opted_out:
                result.skipped += 1
                continue
            appointments = await services.storage.appointments.list_by_patient(patient.phone)
            if any(a.is_active and a.end > now for a in appointments):
                result.skipped += 1
                continue
            visits = [
                a
                for a in appointments
                if a.status in (AppointmentStatus.BOOKED, AppointmentStatus.COMPLETED)
                and a.end <= now
                and (self.procedure_ids is None or a.procedure_id in self.procedure_ids)
            ]
            if not visits:
                result.skipped += 1
                continue
            last_visit = max(a.end for a in visits)
            already = patient.last_recall_at is not None and patient.last_recall_at > last_visit
            if now - last_visit < self.after or already:
                result.skipped += 1
                continue
            name = (patient.name or "").split()[0] if patient.name else "tudo bem"
            if await notifier.template_to_patient(patient.phone, "recall_checkup", patient_name=name):
                patient.last_recall_at = now
                await services.storage.patients.save(patient)
                result.sent += 1
            else:
                result.errors += 1
        return result
