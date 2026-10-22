"""Capabilities: core (always on) + optional ones enabled in clinic.yaml > capabilities."""

from __future__ import annotations

from app.capabilities.clinic_info import ClinicInfoCapability
from app.capabilities.followup import FollowupCapability
from app.capabilities.handoff import HandoffCapability
from app.capabilities.recall import RecallCapability
from app.capabilities.scheduling import SchedulingCapability
from app.capabilities.skills import SkillsCapability
from app.capabilities.waitlist import WaitlistCapability
from app.domain.rules import ClinicRules
from app.harness.capability import BaseCapability
from app.harness.skills import SkillLibrary

CORE_CAPABILITIES: tuple[type[BaseCapability], ...] = (
    SchedulingCapability,
    ClinicInfoCapability,
    HandoffCapability,
)

CAPABILITY_REGISTRY: dict[str, type[BaseCapability]] = {
    "waitlist": WaitlistCapability,
    "recall": RecallCapability,
    "followup": FollowupCapability,
}
"""Optional capabilities by name. Add new ones here and configure them in clinic.yaml."""


class CapabilityConfigError(ValueError):
    pass


def build_capabilities(rules: ClinicRules, skills: SkillLibrary | None = None) -> list[BaseCapability]:
    """Core capabilities, then enabled optional ones (in clinic.yaml order), then skills.

    Validates that every tool referenced by a skill exists (fail fast at startup).
    """
    core_names = {c.name for c in CORE_CAPABILITIES}
    unknown = set(rules.capabilities) - set(CAPABILITY_REGISTRY) - core_names
    if unknown:
        raise CapabilityConfigError(f"unknown capabilities in clinic.yaml: {sorted(unknown)}")

    capabilities: list[BaseCapability] = [cls(rules, rules.capabilities.get(cls.name, {})) for cls in CORE_CAPABILITIES]
    for name in rules.capabilities:
        if name in core_names:
            continue
        config = rules.capability_config(name)
        if config is not None:
            capabilities.append(CAPABILITY_REGISTRY[name](rules, config))

    library = skills or SkillLibrary()
    # Typos fail fast: every tool a skill names must exist in SOME capability (enabled or not)…
    known_tools = set().union(
        *(cls(rules, {}).tool_names() for cls in (*CORE_CAPABILITIES, *CAPABILITY_REGISTRY.values()))
    )
    library.validate_tools(known_tools)
    # …but skills of disabled capabilities are simply left out.
    active_tools = set().union(*(c.tool_names() for c in capabilities))
    capabilities.append(SkillsCapability(rules, library=library.available_with(active_tools)))
    return capabilities


__all__ = ["CAPABILITY_REGISTRY", "CORE_CAPABILITIES", "CapabilityConfigError", "build_capabilities"]
