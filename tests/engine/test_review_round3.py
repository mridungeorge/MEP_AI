"""Regression tests for the third Sprint 1 adversarial review."""
import copy
import time
from datetime import date
from pathlib import Path

import pytest
import yaml
from mep.engine.applicability import load_applicability
from mep.engine.jurisdiction import Override
from mep.engine.loader import RuleLoadError, load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import MAX_SUBJECTS, RunRefused, canonical_classes, run
from mep.engine.units import is_offset

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
ECON25 = "NCC2025-J6D3-econ-cycle"
DEAD25 = "NCC2025-J6D3-deadband"
D = date(2026, 10, 6)


def iv(v, u=None, p=Provenance.ENGINEER_CONFIRMED):
    return InputValue(v, u, p)


def project(**kw):
    return ProjectFacts(**{"state": "VIC", "ncc_edition": "NCC2025", "climate_zone": 6, "building_class": "5",
                           "approval_date": D, **kw})


def econ_inputs(**over):
    base = {"max_airside_component_airflow": iv(1500, "L/s"), "economy_cycle": iv(False),
            "system_type": iv("air_conditioning"), "provides_required_mech_ventilation": iv(True),
            "dehumidification_control_needed": iv(False), "is_electricity_substation": iv(False)}
    base.update(over)
    return base


def one(report):
    assert len(report["results"]) == 1
    return report["results"][0]


class Ledger:
    def __init__(self):
        self.events = []

    def write(self, kind, payload, *, firm_id, revision_id):
        self.events.append(payload)


OVERRIDE = Override("approver-1", "approver", "a perfectly good reason here")


@pytest.mark.parametrize("unit", ["L/s^(9^9^9)", "L/s^9^9", "L/s^99", "L/s^10", "m^(2)", "L/s^-", "L/s^2.5",
                                  "L/s^2^2", "(L/s)", "L/s**2"])
def test_unit_exponents_cannot_hang_the_process(unit):
    start = time.monotonic()
    r = one(run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        max_airside_component_airflow=iv(1500, unit)))]), PACK))
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "unit"
    assert time.monotonic() - start < 5


def test_ordinary_unit_exponents_still_work():
    r = one(run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        max_airside_component_airflow=iv(1.5, "m^3/s")))]), PACK))      # 1500 L/s
    assert r["outcome"] == "FAIL" and r["causes"] == []


@pytest.mark.parametrize("value", [-1500, -0.001])
def test_negative_physical_quantities_need_judgement_not_a_pass(value):
    r = one(run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        max_airside_component_airflow=iv(value, "L/s")))]), PACK))
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "range"


def test_only_offset_temperature_units_are_exempt_from_the_sign_check():
    assert is_offset("degC") and is_offset("degF") and not is_offset("K")


@pytest.mark.parametrize("bad", [
    {"subjects": None}, {"subjects": "x"}, {"subjects": {}}, {"subjects": [{"id": "s"}]},
    {"subjects": [Subject("s", None, {})]}, {"subjects": [Subject("s", [["x"]], {})]},
    {"subjects": [Subject("s", ["a"], [])]}, {"subjects": [Subject("s", ["a"], {"x": False})]},
    {"subjects": [Subject(5, [], {})]}, {"subjects": [Subject("", [], {})]},
    {"subjects": [Subject("a", [], {}), Subject("a", [], {})]},
    {"subjects": [Subject("s", [], {})], "override": {"user_id": "u"}},
    {"subjects": [Subject("s", [], {})], "override": Override(1, "approver", "a perfectly good reason")},
])
def test_hostile_request_shapes_are_refused_not_crashed(bad):
    req = RunRequest(project(), [])
    for k, v in bad.items():
        setattr(req, k, v)
    with pytest.raises(RunRefused) as exc:
        run(req, PACK)
    assert exc.value.code == "invalid_request"


def test_rules_given_as_a_string_are_refused():
    with pytest.raises(RunRefused):
        run(RunRequest(project(), [Subject("s", ECON25, {})]), PACK)  # type: ignore[arg-type]


def test_request_size_is_capped():
    req = RunRequest(project(), [Subject(f"s{i}", [], {}) for i in range(MAX_SUBJECTS + 1)])
    with pytest.raises(RunRefused):
        run(req, PACK)


def test_none_facts_provenance_is_refused_not_a_crash():
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(facts_provenance=None), [Subject("s", [ECON25], econ_inputs())]), PACK)
    assert exc.value.code == "invalid_request"


def test_building_classes_come_from_one_list_that_rule_data_cannot_widen():
    classes = canonical_classes(load_applicability())
    widened = copy.deepcopy(PACK)
    rule = widened.rules[ECON25]
    for i in rule.raw["inputs"]:
        if i["name"] == "building_class":
            i["values"] = [*i["values"], "Class 2"]
    req = RunRequest(project(state="TAS", ncc_edition="NCC2022", building_class="Class 2"),
                     [Subject("s", ["NCC2022-J6D3-econ-cycle"], {})])
    with pytest.raises(RunRefused) as exc:
        run(req, widened)
    assert exc.value.code == "invalid_request" and "Class 2" not in classes


class _Str(str):
    __slots__ = ()

    def __str__(self):
        return "5"


def test_a_str_subclass_cannot_pose_as_a_building_class():
    with pytest.raises(RunRefused):
        run(RunRequest(project(building_class=_Str("2")), [Subject("s", [ECON25], econ_inputs())]), PACK)


def test_state_is_normalised_in_the_report_and_the_ledger_record():
    ledger = Ledger()
    report = run(RunRequest(project(state="nt\n"), [Subject("s", [DEAD25], {})], OVERRIDE), PACK, ledger=ledger)
    assert report["project"]["state"] == "NT" and ledger.events[0]["state"] == "NT"


def test_a_mistyped_rule_is_refused_before_anything_is_written_to_the_ledger():
    broken = copy.deepcopy(PACK)
    broken.rules[DEAD25].raw["check"] = "control_deadbnad >= threshold('min_deadband')"
    ledger = Ledger()
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(state="NT"), [Subject("s", [DEAD25], {})], OVERRIDE), broken, ledger=ledger)
    assert exc.value.code == "rule_error" and ledger.events == []
    assert any("control_deadbnad" in r for r in exc.value.reasons)


def test_an_unknown_threshold_name_in_a_rule_is_a_rule_error():
    broken = copy.deepcopy(PACK)
    broken.rules[DEAD25].raw["check"] = "control_deadband >= threshold('nope')"
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(), [Subject("s", [DEAD25], {})]), broken)
    assert exc.value.code == "rule_error"


def test_an_invisible_reviewer_name_does_not_count_as_a_reviewer(tmp_path):
    rules = tmp_path / "rules"
    (rules / "schema").mkdir(parents=True)
    (rules / "schema" / "rule.schema.json").write_text((ROOT / "rules/schema/rule.schema.json").read_text())
    raw = yaml.safe_load((ROOT / "rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml").read_text(encoding="utf-8"))
    raw.update(status="approved", reviewed_by=chr(0x200B) * 3, reviewed_on="2026-10-07")
    (rules / "ncc2025" / "j6").mkdir(parents=True)
    (rules / "ncc2025" / "j6" / "NCC2025-J6D3-deadband.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(RuleLoadError):
        load_pack(rules)


def test_only_engineer_confirmed_facts_may_choose_the_edition():
    for prov in (Provenance.ADDRESS_LOOKUP_CONFIRMED, Provenance.CALCULATED):
        with pytest.raises(RunRefused) as exc:
            run(RunRequest(project(facts_provenance=prov), [Subject("s", [ECON25], econ_inputs())]), PACK)
        assert exc.value.code == "unconfirmed_facts"
