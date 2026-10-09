#!/usr/bin/env python3
"""Generate docs/engineer-review/: one review sheet per rule plus an index.

Sheets are for a human engineer. They never approve anything: decision, reviewer name,
registration number and date are blank. Rerun after any rule change.
"""
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import reachability
import threshold_diff as td

OUT = ROOT / "docs" / "engineer-review"

# Open questions raised while encoding, keyed by clause family (from the encoder reports and reviews).
FAMILY_QUESTIONS: dict[str, list[str]] = {
    "J6D3-econ-cycle": [
        "Is 'any airside component' the single largest component's airflow (assumed) or each air-conditioner?",
        "The clause text gives only two exemptions (climate zone 1, dehumidification). The Guide text is not encoded.",
    ],
    "J6D3-ac-fan-vsd": ["The Guide says unitary air-conditioning is exempt; the clause text has no such exemption. Not encoded."],
    "J6D4-mv-fan-vsd": ["This rule and the AC variable-speed rule can both fire on ventilation that is part of an AC system."],
    "J6D4-exhaust-motor-stop": ["The Guide mentions an exemption for exhaust that balances required outdoor air; not in the clause text, not encoded."],
    "time-switch": ["The cooling trigger is printed as kWr and the heating trigger as kW (heating); both are stored as kW."],
    "J6D6-duct-insulation": [
        "R-values are printed without a unit; m^2.K/W is assumed.",
        "Cushion box: matched to the installed (not required) R-value of the connecting duct. Confirm.",
        "Flexible duct is R1.0 in every location (assumed). Confirm.",
    ],
    "J6D7-duct-sealing": ["The sealing class itself comes from AS 4254 (licensed); only an engineer-confirmed yes/no is checked."],
    "J6D9-pipe-insulation": [
        "Table J6D9a has no row for fluid above 20 and up to 30 degC; such pipes return NEEDS_JUDGEMENT.",
        "Halving of R at structural penetrations is not encoded (NEEDS_JUDGEMENT).",
        "Refill/relief piping and vessels/tanks (Table J6D9b) are not encoded.",
        "Do split/VRF refrigerant lines count as within MEPS appliances, and which temperature band applies?",
    ],
    "J6D10": [
        "Table J6D10 has columns for climate zones 3 to 7 only. What applies in climate zone 8 (2025 clause says 3 to 8)?",
        "Is the floor area per heater or per system served?",
        "Annual heating energy intensity is a required engineer-confirmed input for the alternative route.",
    ],
    "J6D5-fan-vsd": [
        "Whole-system alternative path (J6D5(1)) returns NEEDS_JUDGEMENT; confirm that is the right treatment.",
        "Below the 125 W small-fan limit the rule is NOT_APPLICABLE, but below 750 W it is PASS: confirm intended semantics.",
    ],
    "deadband": ["Does the 2 K dead band apply to heating-only or cooling-only systems? Specialised-application claims return NEEDS_JUDGEMENT."],
}


def families(rule_id: str) -> list[str]:
    out = [k for k in FAMILY_QUESTIONS if k in rule_id]
    if "time-switch" in rule_id:
        out.append("time-switch")
    if "J6D10" in rule_id:
        out.append("J6D10")
    return list(dict.fromkeys(out))


def sheet(rule: dict[str, Any], rc: td.RuleCells, path: Path) -> str:
    aw = rule["applies_when"]
    src = rule["source"]
    cells = sorted(rc.cells.items())
    rows = []
    for (table, key), cell in cells:
        st = td.status(cell)
        yn = {"match": "yes", "MISMATCH": "NO", "unresolved": "unresolved"}.get(st, f"n/a ({st})")
        rows.append(f"| {table} | `{key}` | {td.fmt(cell.first)} | {td.fmt(cell.second)} | {yn} | |")
    questions = [q for fam in families(rule["id"]) for q in FAMILY_QUESTIONS[fam]]
    if rule.get("notes"):
        questions.append(f"Encoder note: {rule['notes']}")
    for k in rc.unkeyed_second:
        questions.append(f"Second pass also recorded (not a threshold in the rule): {k}")
    todo = [f"`{k[1]}`" for k, c in cells if c.first and c.first[0] == "TODO_FROM_SOURCE"]
    if todo:
        questions.append(f"TODO_FROM_SOURCE in the rule: {', '.join(todo)} (cannot be approved until typed from ABCB)")
    inputs = ", ".join(f"`{i['name']}`" + (f" ({i['unit']})" if i.get("unit") else "") for i in rule["inputs"])
    lines = [
        f"# Review sheet: {rule['id']}",
        "",
        "| Field | Value |", "|---|---|",
        f"| Rule ID | `{rule['id']}` |",
        f"| Edition | {aw['edition']} |",
        f"| State | {', '.join(aw['state'])} |",
        f"| Clause | {src['clause']} |",
        f"| ABCB URL | {src['url']} |",
        f"| Rule file | `{path.relative_to(ROOT).as_posix()}` |",
        f"| Status | {rule['status']} |",
        f"| Tests in rule | {len(rule['tests'])} |",
        "",
        "## Threshold cells",
        "",
        "First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.",
        "Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.",
        "",
        "| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |",
        "|---|---|---|---|---|---|",
        *(rows or ["| - | (no numeric thresholds) | | | | |"]),
        "",
        "## Check logic (encoded)",
        "",
        f"Inputs: {inputs}",
        "",
        "```",
        " ".join(str(rule["check"]).split()),
        "```",
        "",
        "## Exemptions and judgement conditions encoded",
        "",
        "NOT_APPLICABLE when any of these is true (exempt_when):",
        *([f"- `{e}`" for e in aw.get("exempt_when", [])] or ["- none"]),
        "",
        "NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):",
        *([f"- `{e}`" for e in aw.get("needs_judgement_when", [])] or ["- none"]),
        "",
        "Applicability filters: " + (
            ", ".join(f"`{k}` {v}" for k, v in aw.items()
                      if k not in ("edition", "state", "exempt_when", "needs_judgement_when")) or "none"),
        "",
        "## Open questions",
        "",
        *([f"- {q}" for q in questions] or ["- none recorded"]),
        "",
        "## Sub-clause letters",
        "",
        "The ABCB HTML drops sub-clause letters. They were inferred from item order and cross-references.",
        "Confirm every letter in the clause field against the published NCC.",
        "",
        "## First-pass value check (optional, does NOT approve the rule)",
        "",
        "Someone other than the approving engineer may type-check the threshold cells against ABCB and record it in the",
        "rule file as `checked_by` and `checked_on`. This changes nothing about status: the rule stays `draft`.",
        "",
        "| Field | Entry |", "|---|---|",
        f"| Checked by (rule file: checked_by) | {rule.get('checked_by') or ''} |",
        f"| Checked on (rule file: checked_on) | {rule.get('checked_on') or ''} |",
        "",
        "## Reviewer sign-off (blank: to be completed by the engineer)",
        "",
        "| Field | Entry |", "|---|---|",
        "| Reviewer name | |",
        "| Registration no. | |",
        "| Date | |",
        "| Decision (approve / approve with changes / reject) | |",
        "| Changes required | |",
        "",
        ("Only the engineer sets `status: approved`, together with `reviewed_by`, `reviewed_on` and "
         "`reviewer_registration_no`, in the rule file. Approval needs all three."),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    by_id = {rc.rule_id: rc for rc in td.build()}
    index = [
        "# Engineer review pack",
        "",
        "One sheet per draft rule. Nothing here is approved: every rule is `status: draft` and every sign-off",
        "field is blank. Regenerate with `python scripts/engineer_review_pack.py`.",
        "",
        "| # | Rule | Edition | State | Clause | Cells | Match | TODO_FROM_SOURCE | Sheet |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    todos: list[str] = []
    for n, path in enumerate(sorted((ROOT / "rules").rglob("NCC20*.yaml")), 1):
        rule = yaml.safe_load(path.read_text(encoding="utf-8"))
        rc = by_id[rule["id"]]
        (OUT / f"{rule['id']}.md").write_text(sheet(rule, rc, path), encoding="utf-8")
        statuses = [td.status(c) for c in rc.cells.values()]
        todo_keys = [k[1] for k, c in rc.cells.items() if c.first and c.first[0] == "TODO_FROM_SOURCE"]
        todos += [f"`{rule['id']}` `{k}`" for k in todo_keys]
        aw = rule["applies_when"]
        index.append(
            f"| {n} | `{rule['id']}` | {aw['edition']} | {', '.join(aw['state'])} | {rule['source']['clause']} | "
            f"{len(statuses)} | {statuses.count('match')} | {len(todo_keys)} | [{rule['id']}.md]({rule['id']}.md) |")
    index += ["", "## TODO_FROM_SOURCE (must be typed from ABCB before approval)", "",
              ("One open value in total: the climate zone 8 limit of Table J6D10 in the 2025 rule"
               " (one cell per area band)."), "",
              *([f"- {t}" for t in todos] or ["- none"]), "",
              "## UNREACHABLE under the current adoption and applicability data", "",
              ("Combinations of rule, state and building class that cannot be selected unless an approver overrides the "
               "jurisdiction/applicability refusal (`rules/adoption.yaml` decides the edition, `rules/applicability.yaml` "
               "the class). Generated by `scripts/reachability.py`; an engineer should confirm each is intended."), "",
              *[f"- {line}" for line in reachability.lines(reachability.analyse())], "",
              "## Cross-cutting questions for the reviewer", "",
              ("- Rule selection by state: `state: [ALL]` rules also match NT and QLD, whose adoption is "
               "unverified (see `rules/adoption.yaml`)."),
              "- Sub-clause letters were inferred; check each against the published NCC.",
              "- Both NCC 2025 time-switch rules drop Class 2 from the sole-occupancy exemptions; confirm that reading.",
              "- Two-pass agreement is evidence of transcription accuracy only; it is not engineering approval.",
              ""]
    (OUT / "index.md").write_text("\n".join(index), encoding="utf-8")
    print(f"wrote {len(list(OUT.glob('NCC*.md')))} sheets and index to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
