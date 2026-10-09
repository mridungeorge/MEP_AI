"""The reasoning trace is built from graph edges and stored results only, and verify_trace rejects anything else."""
import dataclasses
from pathlib import Path

import pytest
from mep.diff.graph import Change, build_graph
from mep.diff.revision import changes_of, diff_inputs, diff_spaces, stale_results
from mep.diff.trace import Chain, Step, Trace, trace_for, verify_trace
from mep.engine.loader import load_pack

ROOT = Path(__file__).resolve().parents[2]
CIT = {"document": "NCC 2025 Volume One", "clause": "J6D3(1)(h)"}


@pytest.fixture(scope="module")
def graph():
    return build_graph(load_pack(ROOT / "rules"))


def one_trace(graph, with_after):
    results = [{"subject_id": "ahu-1", "rule_id": "NCC2025-J6D3-deadband", "outcome": "PASS", "citation": CIT}]
    changes = changes_of([], diff_inputs({"ahu-1": {"control_deadband": (2, "K")}}, {"ahu-1": {"control_deadband": (6, "K")}}))
    (stale,) = stale_results(graph, changes, results)
    after = {"subject_id": "ahu-1", "rule_id": "NCC2025-J6D3-deadband", "outcome": "FAIL", "citation": CIT} if with_after else None
    return trace_for(stale, results[0], after)


def test_a_trace_runs_cause_fact_rule_result_and_quotes_only_data(graph):
    t = one_trace(graph, True)
    assert verify_trace(t, graph) == []
    assert len(t.chains) >= 1
    kinds = [s.kind for s in t.chains[0].steps]
    assert kinds == ["cause", "fact", "rule", "result"]
    line = t.lines()[0]
    assert "system ahu-1: control_deadband 2 -> 6" in line
    assert "rule NCC2025-J6D3-deadband (NCC 2025 Volume One J6D3(1)(h))" in line
    assert line.endswith("ahu-1: PASS -> FAIL")
    assert t.as_dict()["chains"][0][1]["via"] in ("input", "depends_on")


def test_before_the_re_run_the_result_is_reported_stale(graph):
    assert one_trace(graph, False).lines()[0].endswith("(stale, not yet re-run)")


def test_the_trace_is_deterministic(graph):
    assert one_trace(graph, True) == one_trace(graph, True)


def test_a_link_the_rules_do_not_contain_is_refused(graph):
    t = one_trace(graph, True)
    chain = t.chains[0]
    fake_fact = dataclasses.replace(chain.steps[1], id="fact:duct_insulation_r_value", via="input", path="")
    forged = Trace(t.subject_id, t.rule_id, (Chain((chain.steps[0], fake_fact, chain.steps[2], chain.steps[3])),))
    problems = verify_trace(forged, graph)
    assert problems and "is not an edge of the dependency graph" in problems[0]


def test_a_misdirected_or_malformed_chain_is_refused(graph):
    t = one_trace(graph, True)
    c = t.chains[0]
    other_rule = dataclasses.replace(c.steps[2], id="rule:NCC2025-J6D7-duct-sealing")
    assert verify_trace(Trace(t.subject_id, t.rule_id, (Chain((c.steps[0], c.steps[1], other_rule, c.steps[3])),)), graph)
    assert verify_trace(Trace(t.subject_id, t.rule_id, (Chain((c.steps[0], c.steps[2])),)), graph)
    other_result = Step("result", "result:ahu-2/NCC2025-J6D3-deadband", "x")
    assert verify_trace(Trace(t.subject_id, t.rule_id, (Chain((c.steps[0], c.steps[1], c.steps[2], other_result)),)), graph)


def test_a_space_change_trace_names_the_scope_edge(graph):
    from tests.diff.test_revision import sp
    rules = sorted(graph.rules_affected(Change("space", "area_m2")))
    results = [{"subject_id": "ahu-1", "rule_id": rules[0], "outcome": "PASS", "citation": CIT}]
    changes = changes_of(diff_spaces([sp("R", "G", area=20)], [sp("R", "G", area=40)]), [])
    (stale,) = stale_results(graph, changes, results)
    t = trace_for(stale, results[0], None)
    assert verify_trace(t, graph) == []
    assert "space G: area_m2 20 -> 40" in t.lines()[0] and "spaces (any field)" in t.lines()[0]
