"""End to end: refused runs, approver override, ledger record, banner and report content."""
from datetime import date
from pathlib import Path

import pytest
from mep.engine.jurisdiction import Override
from mep.engine.loader import load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import RunRefused, run

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
RULE = "NCC2025-J6D3-deadband"


class Ledger:
    def __init__(self, fail=False):
        self.events, self.fail = [], fail

    def write(self, kind, payload, *, firm_id, revision_id):
        if self.fail:
            raise OSError("down")
        self.events.append((kind, payload, firm_id, revision_id))


def iv(v, u=None):
    return InputValue(v, u, Provenance.ENGINEER_CONFIRMED)


def request(override=None, state="NT", edition="NCC2025"):
    project = ProjectFacts(state, edition, 6, "5", date(2026, 10, 6), firm_id="firm-1", revision_id="rev-1")
    subject = Subject("ahu", [RULE] if edition == "NCC2025" else [], {
        "control_deadband": iv(3, "K"), "specialised_application_smaller_range_claimed": iv(False),
        "system_type": iv("air_conditioning"), "is_electricity_substation": iv(False)})
    return RunRequest(project, [subject], override)


def test_refused_run_returns_a_clear_jurisdiction_warning():
    with pytest.raises(RunRefused) as exc:
        run(request(), PACK)
    assert exc.value.code == "jurisdiction"
    assert any("NT" in r and "not adopted" in r for r in exc.value.reasons)


def test_approver_override_runs_records_the_ledger_and_flags_the_report():
    ledger = Ledger()
    ov = Override("approver-1", "approver", "Permit lodged under NCC 2025 per client letter dated 1 Oct")
    report = run(request(ov), PACK, ledger=ledger)
    assert [e[0] for e in ledger.events] == ["jurisdiction_override"]
    assert ledger.events[0][2:] == ("firm-1", "rev-1")
    assert report["jurisdiction"]["decision"] == "overridden"
    assert report["jurisdiction"]["override"]["user_id"] == "approver-1"
    assert report["jurisdiction"]["override"]["reason"].startswith("Permit lodged")
    assert report["jurisdiction"]["warnings"]
    assert report["results"][0]["outcome"] == "PASS"


def test_non_approver_override_is_refused_and_nothing_is_written():
    ledger = Ledger()
    for role in ("designer", "checker"):
        with pytest.raises(RunRefused) as exc:
            run(request(Override("u", role, "a perfectly good reason here")), PACK, ledger=ledger)
        assert exc.value.code == "override_rejected"
    assert ledger.events == []


def test_override_fails_closed_without_a_working_ledger():
    ov = Override("approver-1", "approver", "a perfectly good reason here")
    with pytest.raises(RunRefused) as exc:
        run(request(ov), PACK, ledger=Ledger(fail=True))
    assert exc.value.code == "override_rejected"
    with pytest.raises(RunRefused):
        run(request(ov), PACK, ledger=None)


def test_override_is_not_needed_and_not_recorded_when_the_jurisdiction_is_confirmed():
    ledger = Ledger()
    ov = Override("approver-1", "approver", "a perfectly good reason here")
    report = run(request(ov, state="VIC"), PACK, ledger=ledger)
    assert ledger.events == [] and report["jurisdiction"]["decision"] == "confirmed"
    assert report["jurisdiction"]["override"] is None


def test_override_never_bypasses_extracted_inputs():
    ov = Override("approver-1", "approver", "a perfectly good reason here")
    req = request(ov)
    req.subjects[0].inputs["control_deadband"] = InputValue(3, "K", Provenance.EXTRACTED)
    with pytest.raises(RunRefused) as exc:
        run(req, PACK, ledger=Ledger())
    assert exc.value.code == "extracted_inputs"
