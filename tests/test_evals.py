"""The eval scenarios stay valid and pass with the scripted model (real models are measured with `clinic eval`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from evals.runner import load_scenarios, run_scenario

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = load_scenarios(ROOT / "evals" / "scenarios")


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
async def test_scenario_with_scripted_model(scenario: object) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        clinic_config_path=str(ROOT / "config" / "clinic.yaml"),
        templates_path=str(ROOT / "config" / "templates.yaml"),
        skills_dir=str(ROOT / "skills"),
    )
    result = await run_scenario(scenario, "test", settings)  # type: ignore[arg-type]
    assert result.ok, "\n".join(result.failures + result.transcript)
