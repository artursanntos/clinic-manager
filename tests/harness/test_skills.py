from __future__ import annotations

from pathlib import Path

import pytest

from app.capabilities import build_capabilities
from app.capabilities.skills import SkillsCapability
from app.domain.rules import ClinicRules
from app.harness.skills import Skill, SkillError, SkillLibrary, parse_skill
from tests.helpers import make_rules

ROOT = Path(__file__).resolve().parents[2]


def test_repository_skills_are_valid(rules: ClinicRules) -> None:
    library = SkillLibrary.load_dir(ROOT / "skills")
    assert {"agendar", "remarcar", "cancelar", "lista-de-espera", "info-clinica", "acolhimento"} <= set(library.skills)
    build_capabilities(rules, library)  # validates referenced tools


def test_parse_skill() -> None:
    skill = parse_skill("---\nname: x\ndescription: Faz x.\ntools: [a]\n---\nCorpo.")
    assert skill == Skill(name="x", description="Faz x.", body="Corpo.", tools=("a",))


@pytest.mark.parametrize(
    "text",
    [
        "sem frontmatter",
        "---\nname: X Maiúsculo\ndescription: d\n---\nbody",
        "---\nname: x\n---\nbody",
        "---\nname: x\ndescription: d\n---\n",
        "---\nname: x\ndescription: d\nallow: [everything]\n---\nbody",
        "---\nname: x\ndescription: d\ntools: a\n---\nbody",
    ],
)
def test_invalid_skills(text: str) -> None:
    with pytest.raises(SkillError):
        parse_skill(text)


def test_unknown_tool_fails_at_startup(rules: ClinicRules) -> None:
    library = SkillLibrary()
    library.add(Skill(name="hack", description="d", body="b", tools=("delete_everything",)))
    with pytest.raises(SkillError, match="delete_everything"):
        build_capabilities(rules, library)


def test_skill_of_disabled_capability_is_left_out() -> None:
    rules = make_rules(capabilities={"waitlist": {"enabled": False}})
    capabilities = build_capabilities(rules, SkillLibrary.load_dir(ROOT / "skills"))
    skills_cap = next(c for c in capabilities if isinstance(c, SkillsCapability))
    assert "lista-de-espera" not in skills_cap.library.skills
    assert "agendar" in skills_cap.library.skills
    assert all(c.name != "waitlist" for c in capabilities)


def test_folder_name_must_match(tmp_path: Path) -> None:
    (tmp_path / "pasta").mkdir()
    (tmp_path / "pasta" / "SKILL.md").write_text("---\nname: outra\ndescription: d\n---\nbody", encoding="utf-8")
    with pytest.raises(SkillError):
        SkillLibrary.load_dir(tmp_path)
