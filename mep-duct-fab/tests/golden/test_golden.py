"""Golden evals: engine output must match the expected files exactly, and the expected files must
match the outcomes derived by hand (derivation.yaml). Golden evals run on draft rules."""
import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import golden_lib as gl
import release_gate
from mep.engine.loader import load_pack
from mep.engine.report import DRAFT_BANNER

PACK = load_pack(ROOT / "rules")
PROJECTS = gl.projects()
IDS = [f"{p.name}[{release_gate.label(p)}]" for p in PROJECTS]


def test_golden_projects_exist():
    assert len(PROJECTS) >= 5


@pytest.mark.parametrize("directory", PROJECTS, ids=IDS)
def test_engine_output_matches_expected_exactly(directory):
    out = gl.run_project(directory, PACK)
    if "refusal" in out:
        expected = json.loads((directory / "expected_refusal.json").read_text(encoding="utf-8"))
        assert out["refusal"] == expected
        return
    expected = json.loads((directory / "expected_report.json").read_text(encoding="utf-8"))
    assert gl.normalise(out["report"]) == expected
    ledger = directory / "expected_ledger.json"
    if ledger.exists():
        assert out["ledger"] == json.loads(ledger.read_text(encoding="utf-8"))
    else:
        assert out["ledger"] == []


@pytest.mark.parametrize("directory", PROJECTS, ids=IDS)
def test_expected_files_match_the_hand_derivation(directory):
    derived = yaml.safe_load((directory / "derivation.yaml").read_text(encoding="utf-8"))
    if "refusal" in derived:
        expected = json.loads((directory / "expected_refusal.json").read_text(encoding="utf-8"))
        assert expected == derived["refusal"]
        return
    report = json.loads((directory / "expected_report.json").read_text(encoding="utf-8"))
    assert report["jurisdiction"]["decision"] == derived["jurisdiction"]
    assert report["jurisdiction"]["notes"] == derived.get("jurisdiction_notes", [])
    got = [(r["subject_id"], r["rule_id"], r["outcome"],
            None if r["near_miss"] is None else r["near_miss"]["is_near_miss"]) for r in report["results"]]
    want = [(r["subject"], r["rule"], r["outcome"], r["near_miss"]) for r in derived["results"]]
    assert got == want
    # near-miss flips, derived by hand: exactly these and no others
    got_flips = sorted((r["subject_id"], r["rule_id"], f["input"], f["change"], f["becomes"])
                       for r in report["results"] if r["near_miss"] for f in r["near_miss"]["flips"])
    want_flips = sorted((f["subject"], f["rule"], f["input"], f["change"], f["becomes"])
                        for f in derived.get("flips", []))
    assert got_flips == want_flips
    # unassigned rules: selected for the project (edition, state, status) minus those assigned, worked out from
    # the rule files directly, independently of the engine's selection code
    spec = yaml.safe_load((directory / "project.yaml").read_text(encoding="utf-8"))["project"]
    selected = set()
    for path in (ROOT / "rules").rglob("NCC20*.yaml"):
        rule = yaml.safe_load(path.read_text(encoding="utf-8"))
        aw = rule["applies_when"]
        if aw["edition"] == spec["ncc_edition"] and ("ALL" in aw["state"] or spec["state"] in aw["state"]) \
                and rule["status"] in ("draft", "approved"):
            selected.add(rule["id"])
    assigned = {r["rule"] for r in derived["results"]}
    assert report["unassigned_rules"] == sorted(selected - assigned)
    if "ledger_events" in derived:
        ledger = json.loads((directory / "expected_ledger.json").read_text(encoding="utf-8"))
        assert [(e["kind"], e["payload"]["user_id"], e["payload"]["state"], e["payload"]["edition"])
                for e in ledger] == [(e["kind"], e["user_id"], e["state"], e["edition"])
                                     for e in derived["ledger_events"]]


@pytest.mark.parametrize("directory", PROJECTS, ids=IDS)
def test_every_result_is_cited_and_every_report_is_marked_draft(directory):
    out = gl.run_project(directory, PACK)
    if "refusal" in out:
        pytest.skip("refused runs produce no report")
    report = out["report"]
    assert report["banner"] == DRAFT_BANNER and report["draft_rules"]
    for r in report["results"]:
        c = r["citation"]
        assert c["rule_id"] and c["clause"] and c["url"] and c["document"] and c["edition"]
        assert c["rule_status"] == "draft"
    for entry in report["rule_pack"]:
        rule = PACK.rules[entry["id"]]
        assert entry["sha256"] == hashlib.sha256(rule.path.read_bytes()).hexdigest()


def test_synthetic_golden_projects_are_labelled_as_such():
    for directory in PROJECTS:
        spec = yaml.safe_load((directory / "project.yaml").read_text(encoding="utf-8"))
        meta = yaml.safe_load((directory / "meta.yaml").read_text(encoding="utf-8"))
        assert spec["synthetic"] is True and "not pilot data" in spec["description"]
        assert meta["synthetic"] is True and meta["name"] == directory.name
        assert release_gate.label(directory) == "SYNTHETIC"
