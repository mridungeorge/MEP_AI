"""Which building classes an NCC edition's rule pack applies to, from rules/applicability.yaml only.

This is separate from the jurisdiction gate: `jurisdiction.decide` answers "which NCC edition is in force for this
state and approval date"; `check` answers "does that edition's rule pack apply to this building class (or part) in
this state". Carve-outs (for example Class 2 and Class 4 parts that follow BASIX or an earlier code) live in
rules/applicability.yaml with an official source each. Nothing here knows a state, class or date.
"""
from pathlib import Path
from typing import Any

import yaml

from mep.engine.jurisdiction import Decision

APPLICABILITY_FILE = Path(__file__).resolve().parents[4] / "rules" / "applicability.yaml"


KNOWN_EDITIONS = ("NCC2022", "NCC2025")
EFFECTS = ("refuse",)
STATUSES = ("regulator_verified", "abcb_listed")


def validate(data: Any, states: set[str]) -> list[str]:
    """Problems in applicability data. A malformed carve-out must never silently stop refusing, so loading fails."""
    if not isinstance(data, dict):
        return ["applicability.yaml must be a mapping"]
    problems: list[str] = []
    classes = data.get("building_classes")
    if (not isinstance(classes, list) or not classes or not all(type(c) is str and c for c in classes)
            or len(set(classes)) != len(classes)):
        problems.append("building_classes must be a non-empty list of unique text classes")
        classes = []
    carve_outs = data.get("carve_outs")
    if not isinstance(carve_outs, list):
        return [*problems, "carve_outs must be a list"]
    seen: set[str] = set()
    for n, c in enumerate(carve_outs):
        where = f"carve_outs[{n}]"
        if not isinstance(c, dict):
            problems.append(f"{where} must be a mapping")
            continue
        cid = c.get("id")
        if type(cid) is not str or not cid or cid in seen:
            problems.append(f"{where}: id must be a unique non-empty text")
        seen.add(str(cid))
        if c.get("state") not in states:
            problems.append(f"{where}: state must be exactly one of the adoption.yaml states {sorted(states)}")
        if c.get("edition") not in KNOWN_EDITIONS:
            problems.append(f"{where}: edition must be one of {KNOWN_EDITIONS}")
        cls = c.get("classes")
        if not isinstance(cls, list) or not cls or not set(map(str, cls)) <= set(classes):
            problems.append(f"{where}: classes must be a non-empty subset of building_classes")
        if c.get("effect") not in EFFECTS:
            problems.append(f"{where}: effect must be one of {EFFECTS}")
        if type(c.get("reason")) is not str or not c["reason"].strip():
            problems.append(f"{where}: reason is required")
        src = c.get("source")
        if type(src) is not str or not src.startswith("https://") or not (src.split("/")[2].endswith(".gov.au")):
            problems.append(f"{where}: source must be an official https .gov.au page")
        if c.get("status") not in STATUSES:
            problems.append(f"{where}: status must be one of {STATUSES}")
    return problems


def load_applicability(path: Path = APPLICABILITY_FILE) -> dict[str, Any]:
    """Load and validate rules/applicability.yaml (raises ValueError if it is malformed)."""
    from mep.engine.adoption import load_adoption

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        states = set(load_adoption().get("jurisdictions", {}))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"applicability data cannot be read: {exc}") from exc
    problems = validate(data, states)
    if problems:
        raise ValueError("applicability.yaml is malformed: " + "; ".join(problems))
    assert isinstance(data, dict)
    return data


def canonical_classes(data: dict[str, Any]) -> set[str]:
    """The one list of building classes the engine accepts; rule data cannot widen it."""
    return {str(c) for c in data.get("building_classes", [])}


def check(state: str, edition: str, building_class: str | None, data: dict[str, Any] | None = None) -> Decision:
    """Refuse when a carve-out says this edition's rule pack does not apply to this class in this state."""
    table = data if data is not None else load_applicability()
    code = (state or "").strip().upper()
    carve_outs = table.get("carve_outs", [])
    if not isinstance(carve_outs, list) or not all(isinstance(c, dict) for c in carve_outs):
        raise ValueError("applicability data is malformed: carve_outs must be a list of mappings")
    if building_class is None:
        has_carve_out = any(c.get("state") == code and c.get("edition") == edition for c in carve_outs)
        if has_carve_out:
            return Decision(False, (f"{code}: {edition} applicability depends on the building class and none was given",))
        return Decision(True)
    for carve in carve_outs:
        if (carve.get("state") == code and carve.get("edition") == edition
                and str(building_class) in {str(c) for c in carve.get("classes", [])}
                and carve.get("effect") == "refuse"):
            return Decision(False, (
                f"{code}: {edition} does not apply to class {building_class}: {carve.get('reason', 'see applicability.yaml')}",))
    return Decision(True)
