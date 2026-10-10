"""Licensed-standard readiness: the slots where a rule pack for AS 1668.2 or the electrical standards can go once the firm holds the licensed text.

Nothing here encodes a rule or reads a standard. A slot is `LICENCE_REQUIRED` until a person sets `licence_held` in standards_slots.yaml, then `NO_RULES` until an
engineer-reviewed pack exists in its folder under rules/. The app never produces a result for a slot that is not READY, and says so.
"""
import os
from pathlib import Path
from typing import Any

import yaml

SLOTS_FILE = Path(__file__).with_name("standards_slots.yaml")
RULES_DIR = Path(os.environ.get("MEP_RULES_DIR") or Path(__file__).resolve().parents[3] / "rules")


def load_slots() -> list[dict[str, Any]]:
    data = yaml.safe_load(SLOTS_FILE.read_text(encoding="utf-8"))
    slots = data["slots"]
    ids = [s["id"] for s in slots]
    if len(set(ids)) != len(ids) or not all(isinstance(s.get("licence_held"), bool) for s in slots):
        raise ValueError("standards_slots.yaml is malformed")
    return list(slots)


def rule_files(slot: dict[str, Any], rules_dir: Path = RULES_DIR) -> list[Path]:
    folder = rules_dir / slot["pack_dir"]
    return sorted(folder.rglob("*.yaml")) if folder.is_dir() else []


def slot_state(slot: dict[str, Any], rules_dir: Path = RULES_DIR) -> dict[str, Any]:
    n = len(rule_files(slot, rules_dir))
    if not slot["licence_held"]:
        state, message = "LICENCE_REQUIRED", (f"Licence required: {slot['standard']} cannot be encoded until your firm holds a licensed copy and an engineer agrees to encode from it. "
                                              "No rules are loaded and no result is produced.")
    elif n == 0:
        state, message = "NO_RULES", f"The licence for {slot['standard']} is recorded but no engineer-reviewed rule pack has been added yet. No result is produced."
    else:
        state, message = "READY", f"{n} rule file(s) loaded for {slot['standard']} (all stay draft until an engineer approves them)."
    return {"id": slot["id"], "standard": slot["standard"], "title": slot["title"], "discipline": slot["discipline"], "licence_held": slot["licence_held"],
            "rules_loaded": n, "state": state, "message": message}


def all_states(rules_dir: Path = RULES_DIR) -> list[dict[str, Any]]:
    return [slot_state(s, rules_dir) for s in load_slots()]
