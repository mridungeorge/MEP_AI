"""Which rules run on a scheduled system: only those whose own rule YAML names the system's type."""
from pathlib import Path

import pytest
from mep.engine.assignment import rules_for_system_type
from mep.engine.loader import load_pack

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pack():
    return load_pack(ROOT / "rules")


def declared(pack, rid):
    return pack.rules[rid].applies_when.get("system_type")


def test_a_system_gets_exactly_the_rules_that_declare_its_type(pack):
    ids = rules_for_system_type(pack, "NCC2025", "VIC", "air_conditioning")
    assert ids
    assert all(declared(pack, rid) == "air_conditioning" for rid in ids)
    every = {rid for rid, r in pack.rules.items() if r.edition == "NCC2025" and declared(pack, rid) == "air_conditioning"
             and ("ALL" in r.states or "VIC" in r.states)}
    assert set(ids) == every


def test_editions_are_never_mixed(pack):
    for edition in ("NCC2022", "NCC2025"):
        ids = rules_for_system_type(pack, edition, "VIC", "air_conditioning")
        assert ids and all(pack.rules[rid].edition == edition for rid in ids)
    assert not set(rules_for_system_type(pack, "NCC2022", "VIC", "air_conditioning")) & set(
        rules_for_system_type(pack, "NCC2025", "VIC", "air_conditioning"))


def test_different_types_get_different_rules(pack):
    ac = set(rules_for_system_type(pack, "NCC2025", "VIC", "air_conditioning"))
    exhaust = set(rules_for_system_type(pack, "NCC2025", "VIC", "exhaust"))
    assert exhaust and not ac & exhaust


def test_an_unknown_type_or_a_typo_gets_nothing(pack):
    assert rules_for_system_type(pack, "NCC2025", "VIC", "air_conditionning") == []
    assert rules_for_system_type(pack, "NCC2025", "VIC", "") == []


def test_a_rule_that_declares_no_system_type_is_never_assigned_to_a_system(pack):
    undeclared = {rid for rid, r in pack.rules.items() if declared(pack, rid) is None}
    assigned = {rid for ed in ("NCC2022", "NCC2025") for t in ("air_conditioning", "air_conditioning_heating",
                "mechanical_ventilation", "exhaust") for rid in rules_for_system_type(pack, ed, "VIC", t)}
    assert not undeclared & assigned
