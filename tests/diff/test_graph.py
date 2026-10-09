"""The dependency graph is built from the 24 real rules only; every dependency a rule declares is an edge."""
from pathlib import Path

import pytest
from mep.diff.graph import FACT, RULE, SCOPE, Change, build_graph, fact_name
from mep.engine.loader import load_pack

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pack():
    return load_pack(ROOT / "rules")


@pytest.fixture(scope="module")
def graph(pack):
    return build_graph(pack)


def test_all_24_rules_are_nodes_with_incoming_edges(pack, graph):
    assert len(pack.rules) == 24 and set(graph.rule_ids) == set(pack.rules)
    for rid in pack.rules:
        assert graph.edges_into_rule(rid), rid


def test_every_declared_dependency_is_an_edge(pack, graph):
    for rid, rule in pack.rules.items():
        into = {(e.source, e.via) for e in graph.edges_into_rule(rid)}
        for name in rule.inputs:                                    # the inputs the engine evaluates on
            assert any(s.endswith(":" + name) or s == FACT + {"edition": "ncc_edition"}.get(name, name) for s, v in into
                       if v == "input"), (rid, name)
        for path in rule.raw["depends_on"]:                         # what the author declared
            assert (FACT + fact_name(path), "depends_on") in into or fact_name(path) in ("approval_date",), (rid, path)
            if path.startswith("spaces."):
                assert (SCOPE + "spaces", "scope") in into, (rid, path)
        assert (FACT + "ncc_edition", "applies_when") in into and (FACT + "state", "applies_when") in into


def test_no_edge_dangles_and_the_graph_is_deterministic(pack, graph):
    assert all(e.target.startswith(RULE) and e.target[len(RULE):] in pack.rules for e in graph.edges)
    assert all(e.source.startswith((FACT, SCOPE)) for e in graph.edges)
    again = build_graph(pack)
    assert again.edges == graph.edges and again.rule_ids == graph.rule_ids
    assert list(graph.edges) == sorted(graph.edges)


def test_an_input_change_reaches_exactly_the_rules_that_read_it(pack, graph):
    for name in ("control_deadband", "economy_cycle", "duct_insulation_r_value"):
        expected = sorted(rid for rid, r in pack.rules.items() if name in r.inputs
                          or any(fact_name(p) == name for p in r.raw["depends_on"]))
        assert expected, name
        assert graph.rules_affected(Change("input", name)) == expected
    assert graph.rules_affected(Change("input", "no_such_input")) == []


def test_a_change_to_a_rule_specific_input_leaves_unrelated_rules_alone(pack, graph):
    hit = set(graph.rules_affected(Change("input", "control_deadband")))
    assert hit and len(hit) < len(pack.rules)
    assert "NCC2025-J6D9-pipe-insulation" not in hit and "NCC2022-J6D9-pipe-insulation" not in hit


def test_a_project_change_to_the_edition_or_state_reaches_every_rule(pack, graph):
    for name in ("ncc_edition", "state", "approval_date"):
        assert graph.rules_affected(Change("project", name)) == sorted(pack.rules)


def test_building_class_and_climate_zone_changes_reach_the_rules_that_use_them(pack, graph):
    for name in ("building_class", "climate_zone"):
        hit = graph.rules_affected(Change("project", name))
        assert hit and all(name in pack.rules[r].inputs or name in pack.rules[r].applies_when
                           or any(fact_name(p) == name for p in pack.rules[r].raw["depends_on"]) for r in hit)
    assert graph.rules_affected(Change("project", "building_part")) == graph.rules_affected(Change("project", "building_class"))


def test_a_space_change_reaches_the_rules_that_declare_a_spaces_dependency(pack, graph):
    expected = sorted(rid for rid, r in pack.rules.items() if any(p.startswith("spaces.") for p in r.raw["depends_on"]))
    assert expected
    for field in ("area_m2", "use", "storey", "ceiling_void_mm"):
        assert graph.rules_affected(Change("space", field)) == expected
    edge = graph.affected(Change("space", "area_m2"))[0]
    assert edge.source == SCOPE + "spaces" and edge.via == "scope" and edge.path.startswith("spaces.")
