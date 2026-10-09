"""Sign-off fields: checked_by/checked_on are a non-approving first-pass check; approval needs all three
reviewer fields; agents never fill any of them."""
import json
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from mep.engine.loader import RuleLoadError, load_pack

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_rules

VALIDATOR = Draft202012Validator(json.loads((ROOT / "rules/schema/rule.schema.json").read_text(encoding="utf-8")))
SRC = ROOT / "rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml"


def check(tmp_path, **fields):
    rule = yaml.safe_load(SRC.read_text(encoding="utf-8"))
    rule.update(fields)
    d = tmp_path / "rules" / "ncc2025" / "j6"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{rule['id']}.yaml"
    f.write_text(yaml.safe_dump(rule), encoding="utf-8")
    return validate_rules.check_file(f, VALIDATOR)


def test_a_first_pass_check_is_allowed_and_leaves_the_rule_draft(tmp_path):
    assert check(tmp_path, checked_by="A. Checker", checked_on="2026-10-07") == []


def test_checked_by_and_checked_on_go_together(tmp_path):
    assert check(tmp_path, checked_by="A. Checker")
    assert check(tmp_path, checked_on="2026-10-07")
    assert check(tmp_path, checked_by=chr(0x200B) * 3, checked_on="2026-10-07")


def test_checked_on_must_be_a_date(tmp_path):
    assert check(tmp_path, checked_by="A. Checker", checked_on="yesterday")


def test_a_check_never_changes_status(tmp_path):
    errors = check(tmp_path, checked_by="A. Checker", checked_on="2026-10-07", status="approved")
    assert any("reviewed_by" in e for e in errors)          # still needs the full engineer sign-off


APPROVAL = {"status": "approved", "reviewed_by": "E. Engineer", "reviewed_on": "2026-10-07",
            "reviewer_registration_no": "RPEQ 12345"}


def test_approval_needs_reviewer_name_date_and_registration_number(tmp_path):
    assert check(tmp_path, **APPROVAL) == []
    for missing in ("reviewed_by", "reviewed_on", "reviewer_registration_no"):
        fields = {**APPROVAL, missing: None}
        assert any(missing in e for e in check(tmp_path, **fields)), missing
    assert check(tmp_path, **{**APPROVAL, "reviewer_registration_no": chr(0x200B)})


def test_sign_off_fields_on_a_draft_rule_are_refused(tmp_path):
    assert check(tmp_path, reviewed_by="E. Engineer")
    assert check(tmp_path, reviewer_registration_no="RPEQ 12345")


def _pack(tmp_path, **fields):
    rules = tmp_path / "rules"
    (rules / "schema").mkdir(parents=True, exist_ok=True)
    (rules / "schema" / "rule.schema.json").write_text((ROOT / "rules/schema/rule.schema.json").read_text(encoding="utf-8"))
    raw = yaml.safe_load(SRC.read_text(encoding="utf-8"))
    raw.update(fields)
    (rules / "ncc2025" / "j6").mkdir(parents=True, exist_ok=True)
    (rules / "ncc2025" / "j6" / f"{raw['id']}.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return rules


def test_the_engine_loader_requires_the_registration_number_to_load_an_approved_rule(tmp_path):
    assert load_pack(_pack(tmp_path, **APPROVAL)).rules
    with pytest.raises(RuleLoadError):
        load_pack(_pack(tmp_path / "b", **{**APPROVAL, "reviewer_registration_no": None}))


def test_the_engine_loads_a_draft_rule_with_a_first_pass_check(tmp_path):
    pack = load_pack(_pack(tmp_path, checked_by="A. Checker", checked_on="2026-10-07"))
    assert next(iter(pack.rules.values())).status == "draft"
