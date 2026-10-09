"""Sprint 0 acceptance: all rules valid, >=2 tests each, none approved."""
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
FILES = [p for p in (ROOT / "rules").rglob("*.yaml")
         if "schema" not in p.parts and p.name not in ("adoption.yaml", "applicability.yaml")]
SCHEMA = Draft202012Validator(json.loads((ROOT / "rules/schema/rule.schema.json").read_text()))


def test_schema_valid():
    for f in FILES:
        assert not list(SCHEMA.iter_errors(yaml.safe_load(f.read_text()))), f


def test_at_least_two_tests_each():
    for f in FILES:
        assert len(yaml.safe_load(f.read_text())["tests"]) >= 2, f


def test_none_approved():
    for f in FILES:
        assert yaml.safe_load(f.read_text())["status"] != "approved", f


def test_adoption_never_mixes_editions():
    data = yaml.safe_load((ROOT / "rules/adoption.yaml").read_text())
    assert "never mix" in data["rule"]
    for f in FILES:  # rule 7: a pack directory only ever holds its own edition
        rule = yaml.safe_load(f.read_text())
        assert rule["applies_when"]["edition"].lower() in f.parts, f
        assert rule["id"].startswith(rule["applies_when"]["edition"]), f
    assert set(data["jurisdictions"]) == {"NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"}
