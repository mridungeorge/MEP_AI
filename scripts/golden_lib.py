"""Shared loader/runner for evals/golden projects (used by tests and scripts/golden_regen.py)."""
import copy
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from mep.engine.jurisdiction import Override
from mep.engine.loader import RulePack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import RunRefused, run

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden"


class RecordingLedger:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def write(self, kind: str, payload: dict[str, Any], *, firm_id: str, revision_id: str) -> None:
        self.events.append({"kind": kind, "firm_id": firm_id, "revision_id": revision_id, "payload": payload})


def projects() -> list[Path]:
    return sorted(p for p in GOLDEN.iterdir() if (p / "project.yaml").exists())


def load_request(directory: Path) -> RunRequest:
    spec = yaml.safe_load((directory / "project.yaml").read_text(encoding="utf-8"))
    p = spec["project"]
    project = ProjectFacts(
        state=p["state"], ncc_edition=p["ncc_edition"], climate_zone=int(p["climate_zone"]),
        building_class=str(p["building_class"]), approval_date=date.fromisoformat(str(p["approval_date"])),
        firm_id=p.get("firm_id", ""), revision_id=p.get("revision_id", ""))
    subjects = []
    for s in spec["subjects"]:
        inputs = {
            name: InputValue(v["value"], v.get("unit"), Provenance(v.get("provenance", "engineer_confirmed")))
            for name, v in s.get("inputs", {}).items()}
        subjects.append(Subject(s["id"], list(s["rules"]), inputs))
    ov = spec.get("override")
    override = Override(ov["user_id"], ov["role"], ov["reason"]) if ov else None
    return RunRequest(project, subjects, override)


def run_project(directory: Path, pack: RulePack) -> dict[str, Any]:
    """Returns {"report": ..., "ledger": [...]} or {"refusal": {"code", "reasons"}}."""
    ledger = RecordingLedger()
    try:
        report = run(load_request(directory), pack, ledger=ledger)
    except RunRefused as exc:
        return {"refusal": {"code": exc.code, "reasons": exc.reasons}}
    return {"report": report, "ledger": ledger.events}


def normalise(report: dict[str, Any]) -> dict[str, Any]:
    """Drop rule file hashes: they change with any rule edit and are checked separately."""
    out = copy.deepcopy(report)
    for entry in out["rule_pack"]:
        entry.pop("sha256", None)
    return out
