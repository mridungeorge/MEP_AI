#!/usr/bin/env python3
"""Which rule / state / building-class combinations can ever be selected under the current data?

A combination is REACHABLE when some approval date lets the jurisdiction gate (rules/adoption.yaml) allow the rule's
edition in that state AND the applicability check (rules/applicability.yaml) allows the class. Otherwise it can only
run through an approver override: UNREACHABLE. A rule with no state/class combination at all (its own filters match
nothing) is UNREACHABLE (entire rule, even with an override).

`analyse()` is used by scripts/engineer_review_pack.py (docs/engineer-review/index.md) and by validate_rules.py,
which prints UNREACHABLE lines as a warning (it does not fail the build: it is information for the engineer).
"""
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
from mep.engine.adoption import load_adoption
from mep.engine.applicability import canonical_classes, check, load_applicability
from mep.engine.jurisdiction import decide


@dataclass
class RuleReach:
    rule_id: str
    status: str                                   # REACHABLE | PARTLY | UNREACHABLE
    unreachable: dict[str, list[str]] = field(default_factory=dict)   # state -> classes that need an override
    reachable: dict[str, list[str]] = field(default_factory=dict)
    note: str = ""


def date_grid(adoption: dict[str, Any]) -> list[date]:
    """Every date at which adoption data can change an answer, plus a monthly grid 2023-2031."""
    days: set[date] = {date(y, m, 1) for y in range(2023, 2032) for m in range(1, 13)}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, str):
            for part in node.replace(" to ", " ").split():
                try:
                    d = date.fromisoformat(part)
                except ValueError:
                    continue
                days.update({d - timedelta(days=1), d, d + timedelta(days=1)})

    walk(adoption)
    return sorted(days)


def rule_filters(rule: dict[str, Any], classes: set[str]) -> tuple[list[str], set[str]]:
    aw = rule["applies_when"]
    states = [s for s in aw["state"]]
    spec = aw.get("building_class")
    if spec is None:
        allowed = set(classes)
    elif isinstance(spec, list):
        allowed = {str(c) for c in spec}
    else:
        allowed = {str(c) for c in spec.get("in", classes)} - {str(c) for c in spec.get("not_in", [])}
    return states, allowed & classes


def analyse() -> list[RuleReach]:
    adoption = load_adoption()
    applic = load_applicability()
    classes = canonical_classes(applic)
    grid = date_grid(adoption)
    all_states = sorted(adoption["jurisdictions"])
    out: list[RuleReach] = []
    for path in sorted((ROOT / "rules").rglob("NCC20*.yaml")):
        rule = yaml.safe_load(path.read_text(encoding="utf-8"))
        states, rule_classes = rule_filters(rule, classes)
        states = all_states if "ALL" in states else [s for s in states if s in all_states]
        edition = rule["applies_when"]["edition"]
        result = RuleReach(rule["id"], "REACHABLE")
        if not states or not rule_classes:
            result.status, result.note = "UNREACHABLE", "its own state/class filters match nothing"
            out.append(result)
            continue
        for state in states:
            edition_ok = any(decide(state, edition, d, adoption).allowed for d in grid)
            for cls in sorted(rule_classes):
                ok = edition_ok and check(state, edition, cls, applic).allowed
                (result.reachable if ok else result.unreachable).setdefault(state, []).append(cls)
        if not result.reachable:
            result.status, result.note = "UNREACHABLE", "no state/class can select it without an approver override"
        elif result.unreachable:
            result.status = "PARTLY"
        out.append(result)
    return out


def describe(classes: list[str], all_for_rule: set[str]) -> str:
    if set(classes) >= all_for_rule:
        return "all classes"
    return ("class " if len(classes) == 1 else "classes ") + ", ".join(classes)


def lines(results: list[RuleReach]) -> list[str]:
    """One line per rule that has anything UNREACHABLE (compressed: state -> all classes or the class list)."""
    out = []
    for r in results:
        if not r.unreachable and r.status != "UNREACHABLE":
            continue
        if r.status == "UNREACHABLE" and not r.unreachable:
            out.append(f"UNREACHABLE (entire rule): `{r.rule_id}`: {r.note}")
            continue
        every = {c for cs in (*r.reachable.values(), *r.unreachable.values()) for c in cs}
        parts = [f"{s} ({describe(cs, every)})" for s, cs in sorted(r.unreachable.items())]
        label = "UNREACHABLE (entire rule)" if r.status == "UNREACHABLE" else "UNREACHABLE for"
        out.append(f"{label}: `{r.rule_id}`: " + "; ".join(parts) + " (selectable only with an approver override)")
    return out


def main() -> int:
    results = analyse()
    for line in lines(results):
        print(line)
    print(f"{len(results)} rules analysed; "
          f"{sum(r.status == 'REACHABLE' for r in results)} fully reachable, "
          f"{sum(r.status == 'PARTLY' for r in results)} partly, {sum(r.status == 'UNREACHABLE' for r in results)} unreachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
