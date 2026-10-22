"""Interface for delivering prescriptions the DENTIST issued and signed elsewhere (e.g. Memed). See ADR 0006.

Design constraints for whoever implements this later:
- The bot never creates, edits, explains or summarizes a prescription. The LLM never sees its content.
- A future ``PrescriptionsCapability`` would expose NO agent tools: only a job that polls
  ``fetch_ready_documents`` and sends each link to the patient with an approved template (e.g. ``receita_pronta``).
- Any patient question about a prescription → clinical guard / handoff.
- Store only delivery metadata (provider ref, patient phone, sent_at). Never the document or its content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class PrescriptionDocument:
    provider_ref: str
    """Id of the document at the prescription platform (used for idempotency)."""
    patient_phone: str
    patient_name: str
    url: str
    """Link to the signed document hosted by the platform (patient opens it there)."""
    issued_at: datetime


class PrescriptionDeliveryProvider(Protocol):
    async def fetch_ready_documents(self, since: datetime) -> list[PrescriptionDocument]:
        """Documents signed by the dentist since ``since`` that are ready to be delivered."""
        ...

    async def mark_delivered(self, provider_ref: str, at: datetime) -> None:
        """Optional bookkeeping at the platform side (no-op if unsupported)."""
        ...
