"""The engineer review pack covers every rule and never pre-fills a sign-off."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "docs" / "engineer-review"
RULES = sorted((ROOT / "rules").rglob("NCC20*.yaml"))


def test_one_sheet_per_rule_plus_index():
    assert {p.stem for p in PACK.glob("NCC*.md")} == {p.stem for p in RULES}
    assert (PACK / "index.md").exists()


def test_sheets_carry_the_required_fields_and_blank_signoff():
    for p in RULES:
        rule = yaml.safe_load(p.read_text(encoding="utf-8"))
        text = (PACK / f"{rule['id']}.md").read_text(encoding="utf-8")
        for needle in (rule["id"], rule["applies_when"]["edition"], rule["source"]["clause"], rule["source"]["url"],
                       "First pass", "Second pass", "Exemptions and judgement conditions", "Open questions"):
            assert needle in text, (rule["id"], needle)
        assert "First-pass value check" in text and "does NOT approve" in text
        assert "| Checked by (rule file: checked_by) |  |" in text      # blank
        for field in ("Reviewer name", "Registration no.", "Date", "Decision"):
            assert f"| {field}" in text
        assert "| Reviewer name | |" in text and "| Registration no. | |" in text  # left blank
        assert rule["status"] == "draft"


def test_index_lists_the_todo_and_is_current():
    text = (PACK / "index.md").read_text(encoding="utf-8")
    assert "cz8.le_500" in text and "TODO_FROM_SOURCE" in text
    assert text.count("| `NCC") == len(RULES)
