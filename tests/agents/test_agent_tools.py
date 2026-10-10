"""The agents' safety boundary: every forbidden action is refused by the tool layer, whatever the model asks for. No database."""
from uuid import uuid4

import pytest
from mep.agents.tools import (
    HUMAN_ROLES,
    MODELS,
    PERMISSIONS,
    WRITE_TOOLS,
    Agent,
    Context,
    ToolLayer,
    tool_catalogue,
)

from tests.agents.agent_fakes import PASS_ID, RESULT_ID, FakeBackend

ALL = sorted(MODELS)


def layer(agent: Agent, role: str = "designer", **kw) -> tuple[ToolLayer, FakeBackend]:
    b = FakeBackend(**kw)
    return ToolLayer(Context(agent, role, uuid4()), b), b


def sample_args(tool: str) -> dict:
    return {
        "read_results": {}, "read_result": {"result_id": str(RESULT_ID)}, "read_gate_status": {}, "read_notes": {},
        "fill_spec_card": {"skill": "space-envelope", "fields": {"mark": "L1"}},
        "ask_clarifying_question": {"question": "What is the floor to floor height?"},
        "run_skill": {"skill": "space-envelope", "use_draft": True}, "request_rule_run": {},
        "propose_fix_hypothesis": {"result_id": str(RESULT_ID), "text": "Consider adding an economy cycle and re-run the rules."},
        "explain_result": {"result_id": str(RESULT_ID), "explanation": "It failed."},
        "raise_flag": {"severity": "low", "text": "The airflow input looks high."},
        "raise_risk": {"category": "scope", "severity": "low", "text": "Thin evidence for the plant room."},
    }[tool]


# ---- who may use which tool ------------------------------------------------------------------------------------------

def test_the_permission_table_is_exactly_what_the_spec_says():
    designer = {"fill_spec_card", "ask_clarifying_question", "run_skill", "request_rule_run", "propose_fix_hypothesis", "explain_result"}
    reads = {"read_results", "read_result", "read_gate_status", "read_notes"}
    assert PERMISSIONS[Agent.DESIGNER] == designer | reads
    assert PERMISSIONS[Agent.ADVERSARIAL_CHECKER] == reads | {"raise_flag"}
    assert PERMISSIONS[Agent.COMPLIANCE_RISK] == reads | {"raise_risk"}
    assert set(WRITE_TOOLS) | reads == set(MODELS)


@pytest.mark.parametrize("agent", list(Agent))
@pytest.mark.parametrize("tool", ALL)
def test_every_tool_outside_an_agents_set_is_refused_and_does_nothing(agent, tool):
    if tool in PERMISSIONS[agent]:
        pytest.skip("allowed for this agent")
    L, b = layer(agent, "designer")
    r = L.call(tool, sample_args(tool))
    assert not r.ok and r.denied and "may not use" in (r.error or "")
    assert b.notes_added == [] and b.skill_runs == [] and b.rule_runs == 0
    assert b.calls and b.calls[-1][0] == tool and b.calls[-1][1] is False                  # the refusal itself is recorded


@pytest.mark.parametrize("agent", [Agent.ADVERSARIAL_CHECKER, Agent.COMPLIANCE_RISK])
def test_the_read_only_agents_cannot_build_run_rules_edit_cards_or_explain(agent):
    L, b = layer(agent, "checker")
    for tool in ("run_skill", "request_rule_run", "fill_spec_card", "ask_clarifying_question", "propose_fix_hypothesis", "explain_result"):
        assert L.call(tool, sample_args(tool)).denied
    assert b.skill_runs == [] and b.rule_runs == 0 and b.drafts == {} and b.notes_added == []


def test_the_checker_agent_can_only_flag_and_the_risk_agent_only_raise_risks():
    c, cb = layer(Agent.ADVERSARIAL_CHECKER, "checker")
    assert c.call("raise_risk", sample_args("raise_risk")).denied
    assert c.call("raise_flag", sample_args("raise_flag")).ok and [n["kind"] for n in cb.notes_added] == ["flag"]
    r, rb = layer(Agent.COMPLIANCE_RISK, "approver")
    assert r.call("raise_flag", sample_args("raise_flag")).denied
    assert r.call("raise_risk", sample_args("raise_risk")).ok and [n["kind"] for n in rb.notes_added] == ["risk"]


def test_there_is_no_tool_that_writes_a_result_a_review_a_confirmation_or_a_signature():
    forbidden = ["set_result", "write_result", "approve_rule", "confirm_input", "gate1_confirm", "review", "sign_gate", "approve", "reject", "freeze_revision",
                 "set_outcome", "mark_pass", "mark_fail", "run_sql", "shell", "bash", "read_file", "write_file", "fetch_url", "create_share_link"]
    for agent in Agent:
        L, b = layer(agent, "designer")
        for tool in forbidden:
            r = L.call(tool, {"result_id": str(RESULT_ID), "outcome": "PASS"})
            assert r.denied and "no tool called" in (r.error or ""), (agent, tool)
        assert b.notes_added == []
    assert not {n for n in MODELS if any(w in n for w in ("approve", "sign", "confirm", "freeze", "outcome", "set_result", "write_result"))}


# ---- the human behind the agent ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["checker", "approver"])
@pytest.mark.parametrize("tool", ["fill_spec_card", "ask_clarifying_question", "run_skill", "request_rule_run", "propose_fix_hypothesis"])
def test_an_agent_never_has_more_rights_than_the_person_it_works_for(role, tool):
    L, b = layer(Agent.DESIGNER, role)                       # e.g. a checker somehow talking to the designer agent
    r = L.call(tool, sample_args(tool))
    assert r.denied and "needs a designer" in (r.error or "")
    assert b.skill_runs == [] and b.rule_runs == 0 and b.notes_added == []
    assert tool not in L.allowed_tools()


def test_the_catalogue_offered_to_a_model_matches_what_the_layer_will_accept():
    for agent in Agent:
        for role in ("designer", "checker", "approver"):
            L, _ = layer(agent, role)
            offered = {t["name"] for t in tool_catalogue(L)}
            assert offered == {t for t in PERMISSIONS[agent] if role in HUMAN_ROLES[t]}
            assert all(L.call(t, sample_args(t)).denied is False for t in offered if t.startswith("read_"))


# ---- the revision ---------------------------------------------------------------------------------------------------

def test_a_revision_that_is_not_the_users_is_unavailable_to_every_tool():
    L, b = layer(Agent.DESIGNER, "designer", revision_exists=False)
    for tool in ALL:
        if tool in PERMISSIONS[Agent.DESIGNER]:
            assert L.call(tool, sample_args(tool)).denied
    assert b.notes_added == [] and b.skill_runs == [] and b.rule_runs == 0


@pytest.mark.parametrize("tool", ["fill_spec_card", "run_skill", "request_rule_run"])
def test_nothing_that_changes_a_frozen_revision_is_allowed(tool):
    L, b = layer(Agent.DESIGNER, "designer", frozen=True)
    r = L.call(tool, sample_args(tool))
    assert r.denied and "frozen" in (r.error or "")
    assert b.skill_runs == [] and b.rule_runs == 0 and b.drafts == {}


def test_notes_on_a_frozen_revision_are_still_allowed_because_they_change_nothing():
    L, _ = layer(Agent.ADVERSARIAL_CHECKER, "checker", frozen=True)
    assert L.call("raise_flag", sample_args("raise_flag")).ok


def test_a_runaway_agent_is_rate_limited_and_note_limited():
    L, b = layer(Agent.DESIGNER, "designer")
    b.minute_calls = 60
    assert L.call("read_results", {}).denied
    b.minute_calls = 0
    for _ in range(50):
        assert L.call("ask_clarifying_question", sample_args("ask_clarifying_question")).ok
    r = L.call("ask_clarifying_question", sample_args("ask_clarifying_question"))
    assert r.denied and "still open" in (r.error or "")


# ---- arguments -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("tool,args", [
    ("read_result", {"result_id": "not-a-uuid"}), ("read_result", {}), ("read_results", {"extra": 1}),
    ("raise_flag", {"severity": "catastrophic", "text": "x" * 20}), ("raise_flag", {"severity": "low", "text": "x"}),
    ("raise_flag", {"severity": "low", "text": "long enough text", "approve": True}),
    ("raise_risk", {"category": "made_up", "severity": "low", "text": "long enough text"}),
    ("explain_result", {"result_id": str(RESULT_ID)}), ("run_skill", {"skill": "space-envelope"}),
    ("run_skill", {"skill": "space-envelope", "use_draft": True, "spec": {"a": 1}}), ("run_skill", {"skill": "rm -rf /", "use_draft": True}),
    ("run_skill", {"skill": "../duct-fab", "spec": {}}), ("fill_spec_card", {"skill": "nope", "fields": {}}),
    ("fill_spec_card", {"skill": "space-envelope", "fields": {"spec_version": "9"}}), ("fill_spec_card", {"skill": "space-envelope", "fields": {"__class__": 1}}),
    ("fill_spec_card", {"skill": "space-envelope", "fields": {"x" * 5: "y" * 30000}}),
    ("ask_clarifying_question", {"question": "ok"}), ("propose_fix_hypothesis", {"result_id": str(uuid4()), "text": "Consider adding something sensible."}),
])
def test_bad_arguments_are_refused_without_effect(tool, args):
    agent = next(a for a in Agent if tool in PERMISSIONS[a])
    L, b = layer(agent, "designer" if agent is Agent.DESIGNER else "checker")
    r = L.call(tool, args)
    assert not r.ok
    assert b.skill_runs == [] and b.rule_runs == 0 and [n for n in b.notes_added if n["kind"] != "spec_card_draft"] == []


def test_a_result_of_another_revision_or_firm_cannot_be_read_flagged_or_explained():
    c, cb = layer(Agent.ADVERSARIAL_CHECKER, "checker")
    foreign = {"result_id": str(uuid4())}
    assert not c.call("read_result", foreign).ok and not c.call("raise_flag", {**foreign, "severity": "low", "text": "about someone else's"}).ok
    assert cb.notes_added == []
    d, db = layer(Agent.DESIGNER, "designer")
    assert not d.call("explain_result", {**foreign, "explanation": "text"}).ok and db.notes_added == []


# ---- what the designer agent may do ---------------------------------------------------------------------------------

def test_filling_a_card_stores_a_draft_and_reports_what_is_missing_but_builds_nothing():
    L, b = layer(Agent.DESIGNER)
    r = L.call("fill_spec_card", {"skill": "space-envelope", "fields": {"mark": "L1", "storey": {"name": "Level 1"}}})
    assert r.ok and b.skill_runs == []
    assert {"storey.floor_to_floor_mm", "rooms"} <= {m["field"] for m in r.data["missing"]}
    again = L.call("fill_spec_card", {"skill": "space-envelope", "fields": {"storey": {"floor_to_floor_mm": 3600}}})
    assert again.data["draft"]["storey"] == {"name": "Level 1", "floor_to_floor_mm": 3600} and again.data["draft"]["mark"] == "L1"


def test_run_skill_goes_through_the_validated_door_and_only_from_a_draft_or_a_full_spec():
    L, b = layer(Agent.DESIGNER)
    assert not L.call("run_skill", {"skill": "space-envelope", "use_draft": True}).ok            # no draft yet
    L.call("fill_spec_card", {"skill": "space-envelope", "fields": {"mark": "L1"}})
    b.confirmed.add(b.card_digest("space-envelope", {"mark": "L1"}))
    b.confirmed.add(b.card_digest("duct-fab", {"mark": "x"}))
    assert L.call("run_skill", {"skill": "space-envelope", "use_draft": True}).ok and b.skill_runs == [("space-envelope", {"mark": "L1"})]
    assert L.call("run_skill", {"skill": "duct-fab", "spec": {"mark": "x"}}).ok and len(b.skill_runs) == 2


def test_run_skill_needs_a_designers_confirmation_of_the_exact_card_version():
    L, b = layer(Agent.DESIGNER)
    first = L.call("run_skill", {"skill": "duct-fab", "spec": {"mark": "x"}})
    assert first.ok and first.data["status"] == "needs_confirmation" and b.skill_runs == []
    assert any(n["kind"] == "clarifying_question" and n["data"]["needs"] == "card_confirmation" for n in b.notes_added)
    b.confirmed.add(first.data["spec_sha256"])
    assert L.call("run_skill", {"skill": "duct-fab", "spec": {"mark": "x"}}).data.get("status") == "ok"
    # any change to the card is a new version that nobody confirmed
    changed = L.call("run_skill", {"skill": "duct-fab", "spec": {"mark": "x", "extra": 1}})
    assert changed.data["status"] == "needs_confirmation" and len(b.skill_runs) == 1
    # the confirmation is for this skill's card only
    other = L.call("run_skill", {"skill": "space-envelope", "spec": {"mark": "x"}})
    assert other.data["status"] == "needs_confirmation" and len(b.skill_runs) == 1


def test_request_rule_run_only_asks_and_returns_the_engines_own_refusal():
    L, b = layer(Agent.DESIGNER)
    r = L.call("request_rule_run", {})
    assert r.ok and r.data["status"] == "refused" and r.data["code"] == "gate1_required" and b.rule_runs == 1


def test_a_fix_hypothesis_is_labelled_for_failing_results_only_and_never_claims_compliance():
    L, b = layer(Agent.DESIGNER)
    ok = L.call("propose_fix_hypothesis", {"result_id": str(RESULT_ID), "text": "Consider adding an economy cycle and then re-run the rules."})
    assert ok.ok and ok.data["stored_as"].startswith("Hypothesis: verify") and b.notes_added[-1]["body"].startswith("Hypothesis: verify")
    for bad in ("This will pass once an economy cycle is added.", "It then complies with the clause.", "Use NCC2025-J6D3-deadband instead of this."):
        assert not L.call("propose_fix_hypothesis", {"result_id": str(RESULT_ID), "text": bad}).ok
    assert not L.call("propose_fix_hypothesis", {"result_id": str(PASS_ID), "text": "Consider changing something about this passing result."}).ok
    assert len(b.notes_added) == 1


def test_explain_result_text_is_cut_back_to_the_results_own_rule_and_outcome():
    L, b = layer(Agent.DESIGNER)
    r = L.call("explain_result", {"result_id": str(RESULT_ID),
                                  "explanation": "NCC2025-J6D3-econ-cycle failed. But NCC2025-J6D3-deadband passes and the unit complies overall."})
    assert r.ok and "NCC2025-J6D3-deadband" not in r.data["text"] and "complies" not in r.data["text"] and "passes" not in r.data["text"]
    assert "NCC2025-J6D3-econ-cycle" in r.data["text"] and len(r.data["redactions"]) == 3
    assert r.data["facts"]["outcome"] == "FAIL" and r.data["facts"]["rule_id"] == "NCC2025-J6D3-econ-cycle"
    assert b.notes_added[-1]["kind"] == "explanation" and "deadband" not in b.notes_added[-1]["body"]


def test_text_with_hidden_characters_is_cleaned_before_it_is_stored():
    L, b = layer(Agent.ADVERSARIAL_CHECKER, "checker")
    L.call("raise_flag", {"severity": "low", "text": "Looks wrong" + chr(0x202E) + chr(0x200B) + chr(0) + " near the plant room"})
    assert b.notes_added[0]["body"] == "Looks wrong near the plant room"


def test_a_failure_inside_a_tool_is_recorded_and_the_model_gets_a_generic_answer():
    l, b = layer(Agent.DESIGNER)

    def boom(_model):
        raise RuntimeError("Failing row contains (firm_id, secret)")
    l.handlers["raise_flag"] = boom
    r = l.call("read_results", {})
    assert r.ok
    l.handlers["read_results"] = boom
    r = l.call("read_results", {})
    assert not r.ok and "Failing row" not in (r.error or "") and "could not complete" in (r.error or "")
    assert b.calls[-1][2].startswith("failed: RuntimeError") and "secret" not in b.calls[-1][2]


def test_an_explanation_is_cut_to_the_stored_limit_after_filtering():
    l, b = layer(Agent.DESIGNER)
    r = l.call("explain_result", {"result_id": str(RESULT_ID), "explanation": "pass " * 800})
    assert r.ok and all(len(n["body"]) <= 4000 for n in b.notes_added)
