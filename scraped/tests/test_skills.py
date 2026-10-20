"""The shipped skills are spec-clean and the subagent's contract matches the code (scraped#6)."""

from pathlib import Path

import pytest
yaml = pytest.importorskip("yaml")

from scraped.acquire import report_json_schema

SKILLS = Path(__file__).resolve().parents[1] / "data" / "skills"
SKILL_DIRS = sorted(p for p in SKILLS.iterdir() if (p / "SKILL.md").is_file())


def _frontmatter(path: Path) -> dict:
    text = path.read_text()
    _, fm, _ = text.split("---", 2)
    return yaml.safe_load(fm)


def test_router_and_sub_skills_ship():
    names = {p.name for p in SKILL_DIRS}
    assert {"scraped-acquire", "scraped-recon", "scraped-http-rung",
            "scraped-browser-rung", "scraped-api-discovery", "scraped-acquirer"} <= names


@pytest.mark.parametrize("skill_dir", SKILL_DIRS, ids=lambda p: p.name)
def test_skill_is_spec_clean(skill_dir):
    path = skill_dir / "SKILL.md"
    fm = _frontmatter(path)
    assert fm["name"] == skill_dir.name
    assert len(fm["description"]) <= 1024
    assert len(path.read_text().splitlines()) < 500
    assert set(fm) <= {"name", "description", "license", "compatibility",
                       "metadata", "allowed-tools", "coact"}


def test_subagent_return_schema_matches_report_schema():
    returns = _frontmatter(SKILLS / "scraped-acquirer" / "SKILL.md")["coact"]["returns"]
    declared = returns["json_schema"]
    code = report_json_schema()
    assert set(declared["required"]) == set(code["required"])
    assert set(declared["properties"]) <= set(code["properties"])
    validated = declared["properties"]["claims"]["items"]["properties"]["validated"]
    assert validated["enum"] == code["properties"]["claims"]["items"]["properties"]["validated"]["enum"]
