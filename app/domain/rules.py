"""Clinic rules loaded from ``config/clinic.yaml``.

Everything the bot is allowed to say about the clinic (address, procedures, prices, insurance, policies) comes
from here. If it is not in this file, the bot does not know it and must hand off to the dentist.
"""

from __future__ import annotations

from datetime import date, time
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Weekday(StrEnum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"

    @classmethod
    def from_date(cls, day: date) -> Weekday:
        return list(cls)[day.weekday()]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimeRange(_Strict):
    start: time
    end: time

    @model_validator(mode="after")
    def _check_order(self) -> TimeRange:
        if self.end <= self.start:
            raise ValueError(f"time range end ({self.end}) must be after start ({self.start})")
        return self


WeeklyHours = dict[Weekday, list[TimeRange]]


class Holiday(_Strict):
    date: date
    name: str


class Closure(_Strict):
    """A closed period, e.g. vacation. Both ends inclusive."""

    start: date
    end: date
    reason: str = "Consultório fechado"

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


class Procedure(_Strict):
    id: str
    name: str
    duration_minutes: int = Field(gt=0, le=8 * 60)
    price_cents: int | None = Field(default=None, ge=0)
    price_note: str | None = None
    """Shown instead of (or next to) the price, e.g. "sob avaliação"."""
    preparation: str | None = None
    aliases: list[str] = Field(default_factory=list)
    bookable_by_bot: bool = True
    """False → the bot never books it; it hands off (e.g. surgeries that need an evaluation first)."""


class SchedulingSettings(_Strict):
    slot_step_minutes: int = Field(default=30, gt=0, le=240)
    buffer_minutes: int = Field(default=0, ge=0, le=120)
    """Free time kept before and after every existing event."""
    min_lead_minutes: int = Field(default=120, ge=0)
    max_lead_days: int = Field(default=60, gt=0)
    max_options: int = Field(default=6, gt=0, le=20)
    max_options_per_day: int = Field(default=3, gt=0, le=20)
    fill_gaps: bool = True
    """Also offer slots that start right after an existing event (+ buffer), not only on the grid."""


class CancellationPolicy(_Strict):
    min_notice_hours: int = Field(default=24, ge=0)
    text: str


class Approvals(_Strict):
    encaixe: bool = True
    """Booking a slot inside an encaixe window needs the dentist's OK."""
    late_cancellation: bool = False
    """Cancelling with less notice than the policy needs the dentist's OK."""
    new_patient: bool = False
    """First booking of a patient never seen before needs the dentist's OK."""


class Retention(_Strict):
    conversation_days: int = Field(default=90, gt=0)
    max_messages: int = Field(default=30, gt=0)
    processed_message_days: int = Field(default=7, gt=0)


class Insurance(_Strict):
    accepted: list[str] = Field(default_factory=list)
    notes: str | None = None


class ClinicInfo(_Strict):
    name: str
    dentist_name: str
    dentist_phone: str
    """WhatsApp number of the dentist (digits, with country code). Receives handoffs and approvals."""
    address: str
    maps_url: str | None = None
    payment_methods: list[str] = Field(default_factory=list)
    extra_info: dict[str, str] = Field(default_factory=dict)
    """Free-form Q&A the bot may repeat verbatim, e.g. {"estacionamento": "Há vagas na rua."}."""


class ClinicRules(_Strict):
    clinic: ClinicInfo
    timezone: str = "America/Recife"
    scheduling: SchedulingSettings = SchedulingSettings()
    working_hours: WeeklyHours
    encaixe_windows: WeeklyHours = Field(default_factory=dict)
    holidays: list[Holiday] = Field(default_factory=list)
    closures: list[Closure] = Field(default_factory=list)
    procedures: list[Procedure]
    insurance: Insurance = Insurance()
    cancellation_policy: CancellationPolicy
    approvals: Approvals = Approvals()
    retention: Retention = Retention()
    emergency_guidance: str
    capabilities: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Optional capabilities and their settings, keyed by name. ``enabled: false`` turns one off."""

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def _unique_procedures(self) -> ClinicRules:
        ids = [p.id for p in self.procedures]
        if len(ids) != len(set(ids)):
            raise ValueError("procedure ids must be unique")
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def procedure(self, procedure_id: str) -> Procedure | None:
        return next((p for p in self.procedures if p.id == procedure_id), None)

    def holiday_on(self, day: date) -> Holiday | None:
        return next((h for h in self.holidays if h.date == day), None)

    def closure_on(self, day: date) -> Closure | None:
        return next((c for c in self.closures if c.covers(day)), None)

    def capability_config(self, name: str) -> dict[str, Any] | None:
        """Settings of an enabled optional capability, or ``None`` if absent/disabled."""
        cfg = self.capabilities.get(name)
        if cfg is None or not cfg.get("enabled", True):
            return None
        return {k: v for k, v in cfg.items() if k != "enabled"}


def load_rules(path: str | Path) -> ClinicRules:
    with Path(path).open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return ClinicRules.model_validate(raw)
