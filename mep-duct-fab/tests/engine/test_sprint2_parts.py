"""Sprint 2: building class becomes a list of parts (class, storeys, area); checks run per part; refusals are audited."""
from datetime import date
from pathlib import Path

import pytest
from mep.engine.loader import load_pack
from mep.engine.model import BuildingPart, InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import RunRefused, run

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
DEAD22 = "NCC2022-J6D3-deadband"
DEAD25 = "NCC2025-J6D3-deadband"
D = date(2026, 10, 6)
LATE = date(2029, 1, 1)         # NSW/TAS: NCC 2022 past its transition (edition refused) AND class 2/4 carve-out
OV = None


class Ledger:
    def __init__(self):
        self.events = []

    def write(self, kind, payload, *, firm_id, revision_id):
        self.events.append((kind, payload, firm_id, revision_id))


def p(cls, storeys=None, area=None):
    return BuildingPart(cls, storeys, area)


def inputs22():
    c = Provenance.ENGINEER_CONFIRMED
    return {"control_deadband": InputValue(3, "K", c),
            "specialised_application_needs_smaller_deadband": InputValue(False, None, c),
            "system_type": InputValue("air_conditioning", None, c),
            "electricity_network_substation": InputValue(False, None, c)}


def inputs25():
    c = Provenance.ENGINEER_CONFIRMED
    return {"control_deadband": InputValue(3, "K", c),
            "specialised_application_smaller_range_claimed": InputValue(False, None, c),
            "system_type": InputValue("air_conditioning", None, c),
            "is_electricity_substation": InputValue(False, None, c)}


def project(state, edition, cls, on=D):
    return ProjectFacts(state, edition, 6, cls, on, firm_id="f1", revision_id="r1")


# ---- one part is the old behaviour ------------------------------------------------------------

def test_a_text_class_is_one_part_and_the_report_is_unchanged():
    rep = run(RunRequest(project("VIC", "NCC2025", "5"), [Subject("s", [DEAD25], inputs25())]), PACK)
    assert rep["project"]["building_class"] == "5" and "building_parts" not in rep["project"]
    assert "part" not in rep["results"][0]


def test_a_one_part_list_is_the_same_as_a_text_class():
    a = run(RunRequest(project("VIC", "NCC2025", "5"), [Subject("s", [DEAD25], inputs25())]), PACK)
    b = run(RunRequest(project("VIC", "NCC2025", [p("5", 3, 1200.0)]), [Subject("s", [DEAD25], inputs25())]), PACK)
    assert a["results"] == b["results"] and b["project"]["building_class"] == "5"


# ---- mixed use ---------------------------------------------------------------------------------

def test_each_subject_is_evaluated_with_the_class_of_its_own_part():
    parts = [p("5", 4, 900.0), p("2", 6, 2400.0)]
    rep = run(RunRequest(project("VIC", "NCC2025", parts),
                         [Subject("office-ahu", [DEAD25], inputs25(), part=0),
                          Subject("flat-ac", [DEAD25], inputs25(), part=1)]), PACK)
    classes = {r["subject_id"]: r["inputs_used"]["building_class"]["value"] for r in rep["results"]}
    assert classes == {"office-ahu": "5", "flat-ac": "2"}
    assert {r["subject_id"]: r["part"] for r in rep["results"]} == {"office-ahu": 0, "flat-ac": 1}
    assert rep["project"]["building_class"] == "mixed"
    assert rep["project"]["building_parts"] == [
        {"index": 0, "building_class": "5", "storeys": 4, "area_m2": 900.0},
        {"index": 1, "building_class": "2", "storeys": 6, "area_m2": 2400.0}]


def test_a_mixed_use_project_needs_every_subject_to_name_its_part():
    parts = [p("5"), p("2")]
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project("VIC", "NCC2025", parts), [Subject("s", [DEAD25], inputs25())]), PACK)
    assert exc.value.code == "invalid_request" and "part" in exc.value.reasons[0]


@pytest.mark.parametrize("part", [-1, 2, 99, True, "0", 1.0])
def test_a_part_index_outside_the_list_or_of_the_wrong_type_is_refused(part):
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project("VIC", "NCC2025", [p("5"), p("2")]),
                       [Subject("s", [DEAD25], inputs25(), part=part)]), PACK)
    assert exc.value.code == "invalid_request"


def test_a_single_part_project_does_not_need_a_part_index_and_rejects_a_bad_one():
    run(RunRequest(project("VIC", "NCC2025", "5"), [Subject("s", [DEAD25], inputs25(), part=0)]), PACK)
    with pytest.raises(RunRefused):
        run(RunRequest(project("VIC", "NCC2025", "5"), [Subject("s", [DEAD25], inputs25(), part=1)]), PACK)


@pytest.mark.parametrize("parts", [
    [], [p("99")], [p("")], [p(5)], ["5"], [p("5", 0)], [p("5", -1)], [p("5", 1.5)], [p("5", True)], [p("5", 500)],
    [p("5", 3, 0)], [p("5", 3, -1.0)], [p("5", 3, float("nan"))], [p("5", 3, float("inf"))], [p("5", 3, "big")],
    [p("5")] * 51, "not a class", None,
])
def test_malformed_parts_are_refused(parts):
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project("VIC", "NCC2025", parts), [Subject("s", [DEAD25], inputs25(), part=0)]), PACK)
    assert exc.value.code == "invalid_request"


def test_the_applicability_check_runs_per_part_and_names_the_refused_part():
    parts = [p("5", 3, 800.0), p("2", 5, 1500.0)]
    req = RunRequest(project("TAS", "NCC2022", parts),
                     [Subject("a", [DEAD22], inputs22(), part=0), Subject("b", [DEAD22], inputs22(), part=1)])
    with pytest.raises(RunRefused) as exc:
        run(req, PACK)
    assert exc.value.code == "applicability"
    assert any("part 1" in r and "class 2" in r for r in exc.value.reasons)
    assert not any("part 0" in r for r in exc.value.reasons)


def test_a_part_that_is_allowed_alone_runs_when_only_allowed_parts_exist():
    run(RunRequest(project("TAS", "NCC2022", [p("5"), p("6")]),
                   [Subject("a", [DEAD22], inputs22(), part=0), Subject("b", [DEAD22], inputs22(), part=1)]), PACK)


def test_an_override_for_a_refused_part_is_recorded_with_the_part():
    from mep.engine.jurisdiction import Override
    ledger = Ledger()
    ov = Override("approver-1", "approver", "BASIX certificate version confirmed with the certifier")
    parts = [p("5"), p("4")]
    run(RunRequest(project("NSW", "NCC2022", parts),
                   [Subject("a", [DEAD22], inputs22(), part=0)], ov), PACK, ledger=ledger)
    kind, payload, *_ = ledger.events[0]
    assert kind == "applicability_override" and payload["refused_by"] == ["applicability"]
    assert any("part 1" in r for r in payload["reasons"])


def test_parts_cannot_carry_an_extracted_class_by_being_hidden_in_a_subject():
    inputs = inputs25()
    inputs["building_class"] = InputValue("2", None, Provenance.ENGINEER_CONFIRMED)
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project("VIC", "NCC2025", [p("5"), p("2")]), [Subject("s", [DEAD25], inputs, part=0)]), PACK)
    assert exc.value.code == "invalid_request"


# ---- audit of a refusal by both checks ---------------------------------------------------------

def both_request(override=None):
    return RunRequest(project("NSW", "NCC2022", "2", LATE), [Subject("s", [DEAD22], inputs22())], override)


def test_the_fixture_really_is_refused_by_both_checks():
    with pytest.raises(RunRefused) as exc:
        run(both_request(), PACK, ledger=Ledger())
    assert exc.value.code == "jurisdiction"
    assert len(exc.value.reasons) >= 2


def test_when_both_checks_refuse_the_refusal_is_written_to_the_ledger():
    ledger = Ledger()
    with pytest.raises(RunRefused):
        run(both_request(), PACK, ledger=ledger)
    assert len(ledger.events) == 1
    kind, payload, firm_id, revision_id = ledger.events[0]
    assert kind == "run_refused" and (firm_id, revision_id) == ("f1", "r1")
    assert payload["refused_by"] == ["jurisdiction", "applicability"]
    assert payload["project"] == {"state": "NSW", "edition": "NCC2022", "building_parts": [{"building_class": "2"}],
                                  "approval_date": LATE.isoformat()}
    assert payload["revision_id"] == "r1" and payload["reasons"]
    assert payload["timestamp"].endswith("+00:00") and date.fromisoformat(payload["timestamp"][:10])


def test_a_refusal_by_only_one_check_writes_nothing():
    ledger = Ledger()
    with pytest.raises(RunRefused):
        run(RunRequest(project("NSW", "NCC2022", "2"), [Subject("s", [DEAD22], inputs22())]), PACK, ledger=ledger)
    with pytest.raises(RunRefused):
        run(RunRequest(project("QLD", "NCC2022", "5"), [Subject("s", [DEAD22], inputs22())]), PACK, ledger=ledger)
    assert ledger.events == []


def test_a_rejected_override_of_a_double_refusal_is_still_recorded():
    from mep.engine.jurisdiction import Override
    ledger = Ledger()
    with pytest.raises(RunRefused) as exc:
        run(both_request(Override("designer-1", "designer", "I would like to run it anyway please")), PACK,
            ledger=ledger)
    assert exc.value.code == "override_rejected"
    assert [e[0] for e in ledger.events] == ["run_refused"]


def test_an_accepted_override_writes_the_override_not_a_refusal():
    from mep.engine.jurisdiction import Override
    ledger = Ledger()
    run(both_request(Override("approver-1", "approver", "Engineer ruling documented on file 2026-114")), PACK,
        ledger=ledger)
    assert [e[0] for e in ledger.events] == ["jurisdiction_override"]


def test_if_the_refusal_cannot_be_recorded_the_run_fails_closed_with_the_ledger_error_named():
    class Broken:
        def write(self, *a, **k):
            raise RuntimeError("db down")

    with pytest.raises(RunRefused) as exc:
        run(both_request(), PACK, ledger=Broken())
    assert exc.value.code == "ledger_unavailable"


def test_without_a_ledger_a_double_refusal_fails_closed_as_ledger_unavailable():
    with pytest.raises(RunRefused) as exc:
        run(both_request(), PACK)
    assert exc.value.code == "ledger_unavailable"


def test_a_double_refusal_is_recorded_even_when_the_run_is_also_refused_for_an_unselected_rule():
    ledger = Ledger()
    req = RunRequest(project("NSW", "NCC2022", "2", LATE), [Subject("s", [DEAD22, DEAD25], inputs22())])
    with pytest.raises(RunRefused) as exc:
        run(req, PACK, ledger=ledger)
    assert exc.value.code == "rule_not_selected"
    assert [e[0] for e in ledger.events] == ["run_refused"]


def test_the_ledger_failure_message_does_not_leak_the_database_error():
    class Broken:
        def write(self, *a, **k):
            raise RuntimeError("password authentication failed for user supabase_admin")

    with pytest.raises(RunRefused) as exc:
        run(both_request(), PACK, ledger=Broken())
    assert exc.value.code == "ledger_unavailable" and "password" not in " ".join(exc.value.reasons)
