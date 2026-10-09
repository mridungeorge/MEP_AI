"""Synthetic golden pair Rev B -> Rev C: the diff, the stale set, the reasoning traces and the results must equal what was derived
by hand (evals/revisions/syn-rev-b-to-c/derivation.yaml). SYNTHETIC: no pilot-firm data."""
import json
import sys
from pathlib import Path

import pytest
import yaml
from mep.diff.graph import build_graph
from mep.diff.revision import changes_of, diff_inputs, diff_spaces, stale_results
from mep.diff.trace import trace_for, verify_trace
from mep.engine.loader import load_pack
from mep.engine.runner import run

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import golden_lib as gl

PAIR = ROOT / "evals" / "revisions" / "syn-rev-b-to-c"
PACK = load_pack(ROOT / "rules")
GRAPH = build_graph(PACK)
HAND = yaml.safe_load((PAIR / "derivation.yaml").read_text(encoding="utf-8"))


def load(rev: str):
    spec = yaml.safe_load((PAIR / rev / "project.yaml").read_text(encoding="utf-8"))
    spaces = [{"id": s["name"], "ifc_guid": s["guid"], "name": s["name"], "area_m2": s["area_m2"], "use": s["use"],
               "storey": s["storey"], "ceiling_void_mm": s["ceiling_void_mm"],
               "centroid_x_m": s["centroid"][0], "centroid_y_m": s["centroid"][1]} for s in spec["spaces"]]
    inputs = {s["id"]: {n: (v["value"], v.get("unit")) for n, v in s["inputs"].items()} for s in spec["subjects"]}
    report = run(gl.load_request(PAIR / rev), PACK)
    return spaces, inputs, report


@pytest.fixture(scope="module")
def pair():
    sb, ib, rb = load("rev_b")
    sc, ic, rc = load("rev_c")
    space_diff, input_diff = diff_spaces(sb, sc), diff_inputs(ib, ic)
    stale = stale_results(GRAPH, changes_of(space_diff, input_diff), rb["results"])
    def by_key(rep):
        return {(r["subject_id"], r["rule_id"]): r for r in rep["results"]}

    return {"space_diff": space_diff, "input_diff": input_diff, "stale": stale, "rb": rb, "rc": rc,
            "before": by_key(rb), "after": by_key(rc)}


def test_the_pair_is_labelled_synthetic():
    meta = yaml.safe_load((PAIR / "meta.yaml").read_text(encoding="utf-8"))
    assert meta["synthetic"] is True and meta["firm"] is None and meta["data_agreement"] is None
    for rev in ("rev_b", "rev_c"):
        spec = yaml.safe_load((PAIR / rev / "project.yaml").read_text(encoding="utf-8"))
        assert spec["synthetic"] is True and "not pilot data" in spec["description"]


def test_results_for_both_revisions_equal_the_hand_derivation(pair):
    for rev, report in (("rev_b", pair["rb"]), ("rev_c", pair["rc"])):
        got = [(r["subject_id"], r["rule_id"], r["outcome"]) for r in report["results"]]
        want = [(r["subject"], r["rule"], r["outcome"]) for r in HAND["results"][rev]]
        assert got == want, rev


def test_the_diff_equals_the_hand_derivation(pair):
    got = sorted((d.change, d.key, d.matched_by, tuple((f.field, f.old, f.new) for f in d.fields))
                 for d in pair["space_diff"] if d.change != "unchanged")
    want = sorted((s["change"], s["key"], s.get("matched_by", ""), tuple((k, v[0], v[1]) for k, v in s.get("fields", {}).items()))
                  for s in HAND["spaces"])
    assert got == want
    got_in = sorted((i.change, i.system, i.name, i.old, i.new) for i in pair["input_diff"])
    want_in = sorted((i["change"], i["system"], i["name"], i["old"], i["new"]) for i in HAND["inputs"])
    assert got_in == want_in


def test_the_stale_set_equals_the_hand_derivation_and_nothing_else_is_stale(pair):
    got = {(s.subject_id, s.rule_id): sorted({c.change.name if c.change.kind != "space" else "spaces" for c in s.causes})
           for s in pair["stale"]}
    want = {(s["subject"], s["rule"]): sorted(s["because"]) for s in HAND["stale"]}
    assert got == want
    for ns in HAND["not_stale"]:
        assert (ns["subject"], ns["rule"]) not in got
        assert pair["before"][(ns["subject"], ns["rule"])]["outcome"] == pair["after"][(ns["subject"], ns["rule"])]["outcome"]


def test_every_result_that_moved_is_in_the_stale_set(pair):
    moved = {k for k in pair["before"] if pair["before"][k]["outcome"] != pair["after"][k]["outcome"]}
    assert moved and moved <= {(s.subject_id, s.rule_id) for s in pair["stale"]}


def test_the_traces_hold_and_match_the_expected_file(pair):
    traces = {}
    for s in pair["stale"]:
        key = (s.subject_id, s.rule_id)
        t = trace_for(s, pair["before"][key], pair["after"][key])
        assert verify_trace(t, GRAPH) == [], key
        traces[f"{s.subject_id}/{s.rule_id}"] = t
        want = HAND["trace"][f"{s.subject_id}/{s.rule_id}"]
        assert all(line.endswith(want["ends_with"]) for line in t.lines())
        if "chain_count" in want:
            assert len(t.chains) == want["chain_count"]
    expected = json.loads((PAIR / "expected_trace.json").read_text(encoding="utf-8"))
    assert {k: t.as_dict() for k, t in traces.items()} == expected
