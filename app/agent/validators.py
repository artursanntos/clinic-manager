"""Output validators: last line of defence against invented times, dates and prices.

Every time (``14:30``, ``14h``, ``14h30``), date (``26/10``) and price (``R$ 180,00``) in the reply must appear in
text the code produced this turn (tool results, offered slots, pending proposal) or in the patient's own message.
"""

from __future__ import annotations

import re

from app.harness.context import TurnDeps
from app.services.messages import format_slot_line

_TIME_RE = re.compile(r"\b([01]?\d|2[0-3])\s*(?::|h(?![a-zà-ú]))\s*([0-5]\d)?(?!\d)", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(0?[1-9]|[12]\d|3[01])/(0?[1-9]|1[0-2])\b")
_PRICE_RE = re.compile(r"R\$\s*([\d.]+(?:,\d{2})?)")


def times_in(text: str) -> set[tuple[int, int]]:
    return {(int(h), int(m or 0)) for h, m in _TIME_RE.findall(text)}


def dates_in(text: str) -> set[tuple[int, int]]:
    return {(int(d), int(m)) for d, m in _DATE_RE.findall(text)}


def prices_in(text: str) -> set[str]:
    return {p.rstrip(".").replace(".", "") for p in _PRICE_RE.findall(text)}


def allowed_sources(deps: TurnDeps) -> list[str]:
    conversation = deps.conversation
    sources = [*deps.turn.tool_texts, deps.turn.patient_text]
    sources += [format_slot_line(sid, o.slot, deps.rules.tz) for sid, o in conversation.offered_slots.items()]
    if conversation.pending_action:
        sources.append(conversation.pending_action.summary)
    if deps.turn.outcome:
        sources.append(deps.turn.outcome.message)
    return sources


def check_reply(message: str, deps: TurnDeps) -> str | None:
    """Return a correction instruction for the model, or None if the reply is acceptable."""
    if not message.strip():
        return "A mensagem ao paciente está vazia."
    if len(message) > 1500:
        return "A mensagem está longa demais. Resuma em poucas frases."

    summary = deps.turn.proposed_summary
    if summary and deps.conversation.pending_action is not None and summary not in message:
        return f"Você registrou uma proposta. A mensagem deve conter exatamente este resumo: «{summary}»."

    sources = allowed_sources(deps)
    bad_times = times_in(message) - {t for s in sources for t in times_in(s)}
    if bad_times:
        shown = ", ".join(f"{h:02d}:{m:02d}" for h, m in sorted(bad_times))
        return (
            f"Você mencionou horário(s) que não vieram de nenhuma tool ({shown}). "
            "Use apenas horários retornados por find_available_slots ou remova-os."
        )
    bad_dates = dates_in(message) - {d for s in sources for d in dates_in(s)}
    if bad_dates:
        return "Você mencionou data(s) que não vieram de nenhuma tool. Use apenas datas retornadas pelas tools."
    bad_prices = prices_in(message) - {p for s in sources for p in prices_in(s)}
    if bad_prices:
        return "Você mencionou preço(s) que não vieram de get_clinic_info. Consulte a tool ou remova o valor."
    return None
