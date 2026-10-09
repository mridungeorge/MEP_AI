"""Revision diff: matching, classification with tolerances, and the stale set from graph edges."""
import random
from pathlib import Path

import pytest
from mep.diff.graph import Change, build_graph
from mep.diff.revision import changes_of, diff_inputs, diff_spaces, match_spaces, stale_results
from mep.engine.loader import load_pack

ROOT = Path(__file__).resolve().parents[2]


def sp(name, guid=None, area=20.0, use="office", storey="L1", void=300.0, cx=None, cy=None, id=None):
    return {"id": id or name, "ifc_guid": guid, "name": name, "area_m2": area, "use": use, "storey": storey,
            "ceiling_void_mm": void, "centroid_x_m": cx, "centroid_y_m": cy}


def kinds(items):
    return sorted((d.change, d.key) for d in items)


# ---- matching --------------------------------------------------------------------------------------------------

def test_spaces_match_by_guid_even_when_renamed_and_moved():
    old, new = [sp("Office", "G1", cx=1, cy=1)], [sp("Open office", "G1", cx=40, cy=40)]
    pairs, removed, added = match_spaces(old, new)
    assert len(pairs) == 1 and pairs[0][2] == "guid" and not removed and not added
    d = diff_spaces(old, new)[0]
    assert d.change == "changed" and [f.field for f in d.fields] == ["name"]


def test_without_a_guid_name_and_centroid_decide():
    old = [sp("Store", cx=0, cy=0), sp("Store", cx=50, cy=0)]
    new = [sp("Store", cx=50.4, cy=0.2), sp("Store", cx=0.3, cy=-0.1)]
    pairs, removed, added = match_spaces(old, new)
    assert not removed and not added
    assert {(a["centroid_x_m"], b["centroid_x_m"]) for a, b, how in pairs if how == "name+centroid"} == {(0, 0.3), (50, 50.4)}


def test_same_name_far_apart_is_a_removal_and_an_addition():
    pairs, removed, added = match_spaces([sp("Plant", cx=0, cy=0)], [sp("Plant", cx=30, cy=0)])
    assert not pairs and len(removed) == 1 and len(added) == 1


def test_a_missing_centroid_matches_on_an_unambiguous_name_only():
    pairs, _, _ = match_spaces([sp("Lobby")], [sp("lobby ")])
    assert [how for *_, how in pairs] == ["name"]
    pairs, removed, added = match_spaces([sp("Store"), sp("Store", id="s2")], [sp("Store")])
    assert not pairs and len(removed) == 2 and len(added) == 1                     # ambiguous: never guess


def test_duplicate_guids_pair_off_in_order_and_the_rest_is_unmatched():
    pairs, removed, added = match_spaces([sp("A", "G"), sp("B", "G", id="b")], [sp("A", "G")])
    assert len(pairs) == 1 and len(removed) == 1 and not added


def test_the_result_does_not_depend_on_input_order():
    old = [sp(f"R{i}", f"G{i}", area=10 + i) for i in range(8)] + [sp("X", cx=1, cy=1)]
    new = [sp(f"R{i}", f"G{i}", area=10 + i + (i % 3)) for i in range(2, 10)] + [sp("X", cx=1.2, cy=1)]
    ref = kinds(diff_spaces(old, new))
    for seed in range(5):
        a, b = old[:], new[:]
        random.Random(seed).shuffle(a)
        random.Random(seed + 1).shuffle(b)
        assert kinds(diff_spaces(a, b)) == ref


# ---- classification --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("change, expected", [
    ({"area": 20.05}, "unchanged"),           # within 0.5 %
    ({"area": 21.0}, "changed"),
    ({"use": "OFFICE"}, "unchanged"),          # case is not a change
    ({"use": "meeting room"}, "changed"),
    ({"storey": "l1"}, "unchanged"),
    ({"storey": "L2"}, "changed"),
    ({"void": 303.0}, "unchanged"),            # within 5 mm
    ({"void": 450.0}, "changed"),
    ({"void": None}, "changed"),
])
def test_classification_tolerances(change, expected):
    d = diff_spaces([sp("Room", "G")], [sp("Room", "G", **change)])[0]
    assert d.change == expected


def test_added_and_removed_spaces():
    items = diff_spaces([sp("Old", "G1")], [sp("New", "G2")])
    assert kinds(items) == [("added", "G2"), ("removed", "G1")]


def test_changed_fields_are_named_with_old_and_new_values():
    d = diff_spaces([sp("Room", "G", area=20, use="office", storey="L1", void=300)],
                    [sp("Room", "G", area=26, use="lab", storey="L2", void=450)])[0]
    assert {f.field: (f.old, f.new) for f in d.fields} == {
        "area_m2": (20, 26), "use": ("office", "lab"), "storey": ("L1", "L2"), "ceiling_void_mm": (300, 450)}


def test_system_inputs_added_removed_changed_and_part():
    old = {"ahu-1": {"control_deadband": (2, "K"), "economy_cycle": (False, None), "building_part": (0, "dimensionless")},
           "ahu-2": {"control_deadband": (3, "K")}}
    new = {"ahu-1": {"control_deadband": (4, "K"), "economy_cycle": (False, None), "building_part": (1, "dimensionless")},
           "ahu-3": {"control_deadband": (3, "K")}}
    items = diff_inputs(old, new)
    got = {(i.change, i.system, i.name) for i in items}
    assert got == {("changed", "ahu-1", "control_deadband"), ("changed", "ahu-1", "building_part"),
                   ("removed", "ahu-2", "control_deadband"), ("added", "ahu-3", "control_deadband")}
    assert next(i for i in items if i.name == "building_part").is_part_change
    assert not diff_inputs(old, old)
    assert diff_inputs({"a": {"x": (1.0, "K")}}, {"a": {"x": (1.0, "m")}})[0].change == "changed"      # a unit change is a change


# ---- the stale set ----------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def graph_and_results():
    pack = load_pack(ROOT / "rules")
    graph = build_graph(pack)
    rules = sorted(r for r in pack.rules if r.startswith(("NCC2025-J6D3", "NCC2025-J6D7")))
    results = [{"subject_id": s, "rule_id": r} for s in ("ahu-1", "ahu-2") for r in rules]
    return graph, results, pack


def test_a_system_input_change_makes_only_that_systems_dependent_results_stale(graph_and_results):
    graph, results, pack = graph_and_results
    changes = changes_of([], diff_inputs({"ahu-1": {"control_deadband": (2, "K")}}, {"ahu-1": {"control_deadband": (4, "K")}}))
    stale = stale_results(graph, changes, results)
    assert stale and {s.subject_id for s in stale} == {"ahu-1"}
    assert all("control_deadband" in pack.rules[s.rule_id].inputs for s in stale)
    cause = stale[0].causes[0]
    assert cause.subject == "ahu-1" and cause.edge.target == "rule:" + stale[0].rule_id and "2 -> 4" in cause.detail


def test_a_space_change_reaches_every_subject_for_rules_with_a_spaces_dependency(graph_and_results):
    graph, results, pack = graph_and_results
    changes = changes_of(diff_spaces([sp("R", "G", area=20)], [sp("R", "G", area=30)]), [])
    stale = stale_results(graph, changes, results)
    assert stale
    assert {s.subject_id for s in stale} == {"ahu-1", "ahu-2"}
    assert all(any(p.startswith("spaces.") for p in pack.rules[s.rule_id].raw["depends_on"]) for s in stale)
    assert all(c.edge.via == "scope" for s in stale for c in s.causes)


def test_a_project_change_to_the_edition_makes_everything_stale_and_nothing_changes_nothing_stale(graph_and_results):
    graph, results, _ = graph_and_results
    everything = stale_results(graph, [(Change("project", "ncc_edition"), None, "edition changed")], results)
    assert len(everything) == len(results)
    assert stale_results(graph, changes_of([], []), results) == []
    unchanged = changes_of(diff_spaces([sp("R", "G")], [sp("R", "G", area=20.05)]), [])
    assert stale_results(graph, unchanged, results) == []


def test_text_and_boolean_inputs_are_compared_by_value_not_as_numbers():
    old = {"ahu-1": {"system_type": ("air_conditioning", None), "economy_cycle": (False, None)}}
    new = {"ahu-1": {"system_type": ("exhaust", None), "economy_cycle": (False, None)}}
    assert [(i.name, i.old, i.new) for i in diff_inputs(old, new)] == [("system_type", "air_conditioning", "exhaust")]
    assert diff_inputs(old, old) == []
