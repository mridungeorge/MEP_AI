"""Rule selection must intersect adoption.yaml; unconfirmed jurisdictions refuse; approver-only override."""
from datetime import date

import pytest
from mep.engine.adoption import load_adoption
from mep.engine.jurisdiction import Override, OverrideRejected, check_override, decide

LIVE = load_adoption()
D = date(2026, 10, 6)


def synth(**states):
    return {"jurisdictions": states}


# ---- live data (rules/adoption.yaml): the four cases the project asked for ------------------------

def test_nt_is_refused_for_both_editions():
    for ed in ("NCC2022", "NCC2025"):
        d = decide("NT", ed, D, LIVE)
        assert not d.allowed and d.reasons, ed
    assert any("not adopted" in r for r in decide("NT", "NCC2025", D, LIVE).reasons)


def test_qld_ncc2025_is_not_in_force_before_2027_05_01():
    d = decide("QLD", "NCC2025", D, LIVE)
    assert not d.allowed
    assert not decide("QLD", "NCC2025", date(2027, 4, 30), LIVE).allowed


def test_qld_ncc2022_is_refused_unless_a_verified_start_date_exists():
    d = decide("QLD", "NCC2022", D, LIVE)
    entry = LIVE["jurisdictions"]["QLD"]
    verified = entry.get("status") != "unverified" and isinstance(
        entry.get("ncc2022_commercial_energy", {}).get("from"), (str, date)) and \
        str(entry["ncc2022_commercial_energy"]["from"]) != "UNVERIFIED"
    assert d.allowed == verified


def test_vic_ncc2025_allowed_and_ncc2022_superseded_without_transition():
    assert decide("VIC", "NCC2025", D, LIVE).allowed
    d = decide("VIC", "NCC2022", D, LIVE)
    assert not d.allowed and any("superseded" in r or "no transition" in r for r in d.reasons)
    assert decide("VIC", "NCC2022", date(2026, 4, 30), LIVE).allowed


def test_vic_ncc2022_is_not_in_force_before_its_own_start():
    assert not decide("VIC", "NCC2022", date(2024, 4, 30), LIVE).allowed


def test_act_transition_lets_both_editions_run_until_it_ends():
    assert decide("ACT", "NCC2025", D, LIVE).allowed
    assert decide("ACT", "NCC2022", D, LIVE).allowed          # inside the 12-month transition
    assert decide("ACT", "NCC2022", date(2027, 4, 30), LIVE).allowed
    d = decide("ACT", "NCC2022", date(2027, 5, 1), LIVE)
    assert not d.allowed                                          # transition over
    assert decide("ACT", "NCC2022", date(2026, 4, 30), LIVE).allowed


def test_nsw_runs_ncc2022_until_the_2025_start():
    assert decide("NSW", "NCC2022", D, LIVE).allowed
    assert not decide("NSW", "NCC2025", D, LIVE).allowed
    assert decide("NSW", "NCC2025", date(2027, 5, 1), LIVE).allowed
    assert not decide("NSW", "NCC2022", date(2027, 5, 1), LIVE).allowed


def test_tas_date_range_transition():
    assert decide("TAS", "NCC2022", date(2027, 4, 30), LIVE).allowed
    assert not decide("TAS", "NCC2022", date(2027, 5, 1), LIVE).allowed


def test_unknown_state_and_case_insensitivity():
    assert not decide("XX", "NCC2022", D, LIVE).allowed
    assert decide("vic", "NCC2025", D, LIVE).allowed


def test_unknown_edition_and_missing_date_are_refused():
    assert not decide("VIC", "NCC2030", D, LIVE).allowed
    assert not decide("VIC", "NCC2025", None, LIVE).allowed  # type: ignore[arg-type]


# ---- synthetic data: the rules, independent of what the YAML currently says -----------------------

def test_unverified_state_is_refused_even_when_dates_exist():
    adoption = synth(ZZ={"status": "unverified", "ncc2025": {"from": "2026-05-01"},
                         "ncc2022_commercial_energy": {"from": "2024-01-01"}})
    assert not decide("ZZ", "NCC2025", D, adoption).allowed


def test_verified_state_with_dates_is_allowed_and_before_start_is_not():
    adoption = synth(ZZ={"status": "abcb_listed", "ncc2025": {"from": "2026-05-01", "transition": "none"},
                         "ncc2022_commercial_energy": {"from": "2024-01-01"}})
    assert decide("ZZ", "NCC2025", D, adoption).allowed
    assert not decide("ZZ", "NCC2025", date(2026, 4, 30), adoption).allowed


def test_month_transition_math():
    adoption = synth(ZZ={"status": "abcb_listed", "ncc2025": {"from": "2026-05-31", "transition": "12 months"},
                         "ncc2022_commercial_energy": {"from": "2024-01-01"}})
    assert decide("ZZ", "NCC2022", date(2027, 5, 30), adoption).allowed
    assert not decide("ZZ", "NCC2022", date(2027, 5, 31), adoption).allowed


# ---- override -----------------------------------------------------------------------------------

class Ledger:
    def __init__(self, fail=False):
        self.events, self.fail = [], fail

    def write(self, kind, payload, *, firm_id, revision_id):
        if self.fail:
            raise OSError("ledger down")
        self.events.append((kind, payload, firm_id, revision_id))


def refusal():
    return decide("NT", "NCC2025", D, LIVE)


def test_approver_override_with_reason_is_written_to_the_ledger():
    ledger = Ledger()
    ov = Override(user_id="u1", role="approver", reason="Client confirmed NT adopts NCC 2025 for this permit")
    check_override(ov, refusal(), ledger, state="NT", edition="NCC2025", approval_date=D, firm_id="f", revision_id="r")
    kind, payload, firm, rev = ledger.events[0]
    assert kind == "jurisdiction_override" and firm == "f" and rev == "r"
    assert payload["user_id"] == "u1" and payload["reason"].startswith("Client confirmed")
    assert payload["state"] == "NT" and payload["edition"] == "NCC2025" and payload["reasons"]


@pytest.mark.parametrize("role", ["designer", "checker", "", "admin", None])
def test_only_an_approver_may_override(role):
    ledger = Ledger()
    with pytest.raises(OverrideRejected):
        check_override(Override("u", role, "a perfectly good reason"), refusal(), ledger, state="NT",  # type: ignore[arg-type]
                       edition="NCC2025", approval_date=D, firm_id="f", revision_id="r")
    assert ledger.events == []


@pytest.mark.parametrize("reason", ["", "   ", "ok", None])
def test_override_needs_a_real_reason(reason):
    with pytest.raises(OverrideRejected):
        check_override(Override("u", "approver", reason), refusal(), Ledger(), state="NT",  # type: ignore[arg-type]
                       edition="NCC2025", approval_date=D, firm_id="f", revision_id="r")


def test_override_fails_closed_when_the_ledger_cannot_be_written():
    with pytest.raises(OverrideRejected):
        check_override(Override("u", "approver", "a perfectly good reason"), refusal(), Ledger(fail=True),
                       state="NT", edition="NCC2025", approval_date=D, firm_id="f", revision_id="r")


def test_override_without_a_ledger_is_rejected():
    with pytest.raises(OverrideRejected):
        check_override(Override("u", "approver", "a perfectly good reason"), refusal(), None,
                       state="NT", edition="NCC2025", approval_date=D, firm_id="f", revision_id="r")


@pytest.mark.parametrize("reason", [chr(0x200B) * 12, ".........." , "-" * 20, chr(0) * 12])
def test_reason_made_of_invisible_or_punctuation_characters_is_not_a_reason(reason):
    with pytest.raises(OverrideRejected):
        check_override(Override("u", "approver", reason), refusal(), Ledger(), state="NT", edition="NCC2025",
                       approval_date=D, firm_id="f", revision_id="r")


def test_override_needs_a_user_id():
    with pytest.raises(OverrideRejected):
        check_override(Override("  ", "approver", "a perfectly good reason"), refusal(), Ledger(), state="NT",
                       edition="NCC2025", approval_date=D, firm_id="f", revision_id="r")
