import hashlib
from pathlib import Path

import pytest
import yaml
from mep.engine.loader import RuleLoadError, load_pack
from mep.engine.runner import select_rules

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")


def test_all_24_draft_rules_load_with_their_file_hashes():
    assert len(PACK.rules) == 24
    for rule in PACK.rules.values():
        assert rule.status == "draft"
        assert rule.sha256 == hashlib.sha256(rule.path.read_bytes()).hexdigest()


def test_selection_never_mixes_editions_and_respects_state():
    for edition in ("NCC2022", "NCC2025"):
        for state in ("VIC", "NSW", "ACT"):
            chosen = select_rules(PACK, edition, state)
            assert chosen and {r.edition for r in chosen.values()} == {edition}
            assert all("ALL" in r.states or state in r.states for r in chosen.values())
    nsw = select_rules(PACK, "NCC2025", "NSW")
    vic = select_rules(PACK, "NCC2025", "VIC")
    assert "NCC2025-NSW-J6D3-time-switch-ac" in nsw and "NCC2025-J6D3-time-switch-ac" not in nsw
    assert "NCC2025-J6D3-time-switch-ac" in vic and "NCC2025-NSW-J6D3-time-switch-ac" not in vic


def test_a_rule_that_breaks_the_schema_is_refused(tmp_path):
    rules = tmp_path / "rules"
    (rules / "schema").mkdir(parents=True)
    (rules / "schema" / "rule.schema.json").write_text((ROOT / "rules/schema/rule.schema.json").read_text())
    good = yaml.safe_load((ROOT / "rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml").read_text(encoding="utf-8"))
    del good["depends_on"]
    d = rules / "ncc2025" / "j6"
    d.mkdir(parents=True)
    (d / "NCC2025-J6D3-deadband.yaml").write_text(yaml.safe_dump(good))
    with pytest.raises(RuleLoadError):
        load_pack(rules)
