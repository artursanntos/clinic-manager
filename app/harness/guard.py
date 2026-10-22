"""Deterministic clinical/urgency guard. Runs BEFORE the LLM; a match means immediate handoff.

Biased toward false positives on purpose: an unnecessary handoff costs the dentist a minute; a missed clinical
question could hurt a patient.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

_URGENT = [
    r"sangr\w*",
    r"sangue",
    r"inchad\w*",
    r"inchac\w*",
    r"inchou",
    r"febre",
    r"trauma\w*",
    r"acidente",
    r"pus",
    r"abscesso",
    r"urgen\w*",
    r"emergenc\w*",
    r"nao consigo (abrir a boca|engolir|respirar|comer)",
    r"dente (quebrou|caiu|quebrado|mole)",
    r"quebrei (o|um) dente",
]

_CLINICAL = [
    r"dor",
    r"dores",
    r"doi",
    r"doendo",
    r"doeu",
    r"dolorid\w*",
    r"latej\w*",
    r"sensibilidade",
    r"sensivel",
    r"remedio\w*",
    r"medicament\w*",
    r"medicac\w*",
    r"antibiotic\w*",
    r"anti-?inflamatori\w*",
    r"analgesic\w*",
    r"receita\w*",
    r"dipirona",
    r"ibuprofeno",
    r"amoxicilina",
    r"nimesulida",
    r"paracetamol",
    r"posso tomar",
    r"anestesi\w*",
    r"pos-?operatori\w*",
    r"infec\w*",
    r"inflamad\w*",
    r"inflamac\w*",
    r"gravida",
    r"gestante",
    r"amamenta\w*",
]


def _compile(patterns: list[str]) -> re.Pattern[str]:
    return re.compile(r"\b(" + "|".join(patterns) + r")\b")


_URGENT_RE = _compile(_URGENT)
_CLINICAL_RE = _compile(_CLINICAL)


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@dataclass(frozen=True)
class GuardHit:
    kind: Literal["urgent", "clinical"]
    term: str


def check_message(text: str) -> GuardHit | None:
    norm = normalize(text)
    if m := _URGENT_RE.search(norm):
        return GuardHit("urgent", m.group(0))
    if m := _CLINICAL_RE.search(norm):
        return GuardHit("clinical", m.group(0))
    return None
