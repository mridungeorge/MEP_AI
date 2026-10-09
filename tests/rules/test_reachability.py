"""Reachability: rules/branches that can only run through an approver override are listed as UNREACHABLE."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import reachability


def by_id():
    return {r.rule_id: r for r in reachability.analyse()}


def test_nsw_2022_class_2_heating_is_unreachable_but_the_rest_of_the_rule_is_not():
    r = by_id()["NCC2022-NSW-J6D10-elec-heating-limit"]
    assert r.status == "PARTLY"
    assert r.unreachable == {"NSW": ["2"]}
    assert "5" in r.reachable["NSW"] and "2" not in r.reachable["NSW"]


def test_nt_is_unreachable_for_every_rule_that_lists_all_states():
    r = by_id()["NCC2025-J6D3-deadband"]
    assert r.unreachable["NT"] and "NT" not in r.reachable
    assert "VIC" in r.reachable and "NSW" in r.reachable    # NSW is reachable once NCC 2025 starts


def test_class_carve_outs_show_up_for_tas_and_nsw_on_ncc2022():
    r = by_id()["NCC2022-J6D3-econ-cycle"]
    assert r.unreachable["TAS"] == ["2", "4"] and r.unreachable["NSW"] == ["2", "4"]
    assert r.unreachable["QLD"] and "QLD" not in r.reachable        # NCC 2022 in QLD is unverified


def test_the_review_pack_index_lists_the_unreachable_combinations():
    text = (ROOT / "docs/engineer-review/index.md").read_text(encoding="utf-8")
    assert "## UNREACHABLE under the current adoption and applicability data" in text
    assert "NCC2022-NSW-J6D10-elec-heating-limit" in text and "NSW (class 2)" in text


def test_the_validator_prints_the_unreachable_lines(capsys):
    import validate_rules
    assert validate_rules.main([]) == 0
    assert "UNREACHABLE for: `NCC2022-NSW-J6D10-elec-heating-limit`" in capsys.readouterr().out
