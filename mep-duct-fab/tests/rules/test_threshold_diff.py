"""The cell-level diff must catch a changed cell and a swap, and the real packs must be clean."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import threshold_diff as td


def cells(first_second):
    rc = td.RuleCells("X")
    for key, (a, b) in first_second.items():
        rc.put("first", "J6D3", key, a, "L/s")
        rc.put("second", "J6D3", key, b, "L/s")
    return rc


def test_changed_cell_is_a_mismatch():
    rc = cells({"cz6": (2000, 1200), "cz7": (2500, 2500)})
    assert [td.status(c) for _, c in sorted(rc.cells.items())] == ["MISMATCH", "match"]


def test_swapped_cells_are_detected_even_though_the_value_sets_are_equal():
    rc = cells({"cz3": (7500, 3500), "cz4": (3500, 7500), "cz5": (3000, 3000)})
    assert td.swaps(rc) == [("cz3", "cz4")]


def test_unit_scaling_is_not_a_mismatch():
    rc = td.RuleCells("X")
    rc.put("first", "t", "k", 1, "kW")
    rc.put("second", "t", "k", 1000, "W")
    assert td.status(rc.cells[("t", "k")]) == "match"


def test_missing_cells_are_reported_not_ignored():
    rc = td.RuleCells("X")
    rc.put("first", "t", "only_rules", 1, "kW")
    rc.put("second", "t", "only_second", 1, "kW")
    assert {td.status(c) for c in rc.cells.values()} == {"first-only", "second-only"}


def test_real_packs_have_no_cell_disagreement_or_swap():
    for rc in td.build():
        for cell in rc.cells.values():
            assert td.status(cell) != "MISMATCH", (rc.rule_id, cell)
        assert td.swaps(rc) == []
