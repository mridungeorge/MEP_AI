"""Generous upper bounds on the pure (no web, no database) parts of a 200-space, 20-system project. See docs/performance.md.

These guard against an algorithmic regression, not a precise speed: the bounds are the product targets (run < 10 s) on a small dev container.
"""
import sys
import time
from pathlib import Path

import pytest
from mep import clash, sizing
from mep.engine.model import Outcome
from mep.engine.runner import run
from mep.review.package import to_pdf

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import perf_project as pp

LIMIT_S = 10.0


@pytest.fixture(scope="module")
def pack():
    return pp.load()


@pytest.fixture(scope="module")
def world(pack):
    spaces = pp.make_spaces(200)
    req = pp.make_request(pack, 20)
    t = time.perf_counter()
    report = run(req, pack)
    return {"spaces": spaces, "req": req, "report": report, "run_s": time.perf_counter() - t}


def test_engine_run_for_20_systems_under_limit(world):
    assert len(world["spaces"]) == 200 and len(world["req"].subjects) == 20
    results = world["report"]["results"]
    assert len({r["subject_id"] for r in results}) == 20 and len(results) >= 20
    assert {r["outcome"] for r in results} <= {o.value for o in Outcome}
    assert world["run_s"] < LIMIT_S


def test_diff_and_cross_rule_rerun_between_two_revisions_under_limit(pack, world):
    req2, spaces2 = pp.revise(world["req"], world["spaces"])
    report2 = run(req2, pack)
    (diff, _), seconds = pp.timed(lambda: pp.diff_and_rerun(pack, world["req"], req2, world["spaces"], spaces2, world["report"], report2))
    assert diff["has_changes"] and diff["spaces"] and diff["inputs"]
    assert any(s["change"] == "removed" for s in diff["spaces"]) and any(s["change"] == "added" for s in diff["spaces"])
    assert seconds < LIMIT_S


def test_package_pdf_with_several_hundred_lines_under_limit():
    pkg = pp.make_package(400)
    data, seconds = pp.timed(lambda: to_pdf(pkg))
    assert data.startswith(b"%PDF") and len(data) > 10_000
    assert seconds < LIMIT_S


def test_sizing_2000_duct_runs_under_limit():
    settings = sizing.clean_settings(None)
    runs = pp.make_duct_runs(2000)
    sized, seconds = pp.timed(lambda: [sizing.size_duct(r["airflow_ls"], "rect" if r["id"] % 2 else "round", settings) for r in runs])
    assert len(sized) == 2000 and all(s.unsound is False for s in sized)
    assert seconds < LIMIT_S


def test_clash_detect_500_ducts_against_20000_boxes_under_limit_with_early_stop():
    ducts, elements = pp.make_duct_runs(500), pp.make_boxes(20000)
    models = [{"discipline": "structure", "file_name": "s.ifc", "elements": elements}]
    found, seconds = pp.timed(lambda: clash.detect(ducts, models, 50.0, limit=2000))
    assert 0 < len(found) <= 2002
    assert seconds < LIMIT_S


def test_clash_detect_full_scan_with_no_hits_under_limit():
    """The worst case for the early stop: nothing is found, so every duct is checked against the whole model."""
    ducts, elements = pp.make_duct_runs(500), pp.make_boxes(20000, extent=1e7)
    models = [{"discipline": "structure", "file_name": "s.ifc", "elements": elements}]
    found, seconds = pp.timed(lambda: clash.detect(ducts, models, 50.0, limit=2000))
    assert found == [] and seconds < LIMIT_S


def test_indexed_clash_detect_matches_checking_every_box():
    ducts, elements = pp.make_duct_runs(40, seed=5), pp.make_boxes(1500, seed=6, extent=100000.0)
    models = [{"discipline": "structure", "file_name": "s.ifc", "elements": elements}]
    for clearance in (0.0, 50.0, 4000.0):
        got = clash.detect(ducts, models, clearance)
        want = []
        for d in ducts:
            db = clash.duct_box(d)
            want += [(str(d["id"]), e.guid) for e in elements if db is not None and clash.gap(db, e) < clearance]
        assert sorted((r["duct_id"], r["element_guid"]) for r in got) == sorted(want)
