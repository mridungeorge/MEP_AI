#!/usr/bin/env python3
"""Cell-level comparison of rule thresholds with the independent second pass.

Both passes are reduced to cells keyed by (rule_id, table, row/zone key) and compared key by key,
never by value. Writes docs/threshold-diff.md. Never edits a rule. `build()` is reused by
scripts/engineer_review_pack.py.

The second pass (evals/dual_encoding/second_pass.yaml) was extracted from ABCB by an agent with no
access to rules/. Its free-form names are parsed into keys here; anything that cannot be keyed is
reported as unkeyed, not silently dropped.
"""
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
from mep.engine import (
    units as engine_units,
)

PRINTED_WITHOUT_UNIT = ("R-Value", "multiplier", "climate zone", "n/a")

# first-pass scalar threshold name -> second-pass name (clause-value thresholds, no table)
SCALAR_ALIASES: dict[str, str] = {
    "ac_system": "ac_system_capacity_trigger", "heater": "heater_capacity_trigger",
    "ac_trigger": "ac_system_capacity_trigger", "heater_trigger": "heater_capacity_trigger",
    "max_override_resume": "override_max_time_to_resume_normal",
    "min_deadband": "min_control_deadband",
    "exhaust_system": "exhaust_airflow_trigger", "exhaust_trigger": "exhaust_airflow_trigger",
    "mv_system": "mv_airflow_trigger", "mv_trigger": "mv_airflow_trigger",
    "sealing_trigger": "ac_system_capacity_trigger",
    "vsd_trigger": "fan_input_power_trigger", "small_fan_exempt": "exempt_fan_input_power_max",
    "unducted_exempt_airflow": "exempt_unducted_supply_air_capacity",
}
# rule-specific overrides where the same first-pass name maps to different second-pass names
SCALAR_OVERRIDES: dict[tuple[str, str], str] = {
    ("NCC2022-J6D3-ac-fan-vsd", "ac_system"): "ac_airflow_trigger",
    ("NCC2022-J6D7-duct-sealing", "ac_system"): "ac_system_capacity_trigger",
}
EDGE_ALIASES = {  # first-pass band edges -> (second-pass name, which number inside its text value)
    "t_edge_low": ("temp_band_low_temp_chilled_upper", 0),
    "t_chilled_max": ("temp_band_chilled", -1),
    "t_heated_min": ("temp_band_heated", 0),
    "t_high": ("temp_band_high_temp_heated_lower", 0),
    "dn_1": ("dia_band_1", 0), "dn_2": ("dia_band_2", -1), "dn_3": ("dia_band_3", -1),
}
DN_KEYS = {"le_40": "le_40", "dn_le_40": "le_40", "gt_40_le_80": "40_80", "dn_gt_40_le_80": "40_80",
           "40_80": "40_80", "gt_80_le_150": "80_150", "dn_gt_80_le_150": "80_150", "80_150": "80_150",
           "gt_150": "gt_150", "dn_gt_150": "gt_150"}
FIRST_BAND = {"low_temperature_chilled": "low_temp_chilled", "high_temperature_heated": "high_temp_heated"}


@dataclass
class Cell:
    table: str
    key: str
    first: tuple[Any, str] | None = None
    second: tuple[Any, str] | None = None
    note: str = ""


@dataclass
class RuleCells:
    rule_id: str
    cells: dict[tuple[str, str], Cell] = field(default_factory=dict)
    unkeyed_second: list[str] = field(default_factory=list)

    def put(self, side: str, table: str, key: str, value: Any, unit: str, note: str = "") -> None:
        cell = self.cells.setdefault((table, key), Cell(table, key))
        setattr(cell, side, (value, unit))
        if note:
            cell.note = note


def canon(value: Any, unit: str | None) -> tuple[Any, str]:
    """Magnitude in base units and dimension, so W/m2 vs W/m^2, kWr vs kW and hour vs h compare equal."""
    if isinstance(value, str):
        return value, "text"
    u = re.sub(r"[^ -~]+", "deg", unit or "").replace("kWr", "kW").replace("m2", "m^2")
    u = re.sub(r"\s*\(.*", "", u).strip()
    u = {"hours": "hour", "hour": "hour", "minutes": "minute", "year": "year"}.get(u, u)
    if not u or any(u.startswith(p) for p in PRINTED_WITHOUT_UNIT):
        return round(float(value), 6), "as-printed"
    try:
        unit = engine_units.unit_of(u.replace("degC", "delta_degC"))
        base = engine_units.UREG.Quantity(float(value), unit).to_base_units()
        return round(float(base.magnitude), 6), str(base.dimensionality)
    except Exception:  # noqa: BLE001
        return round(float(value), 6), f"unparsed:{unit}"


def same(a: tuple[Any, str], b: tuple[Any, str]) -> bool:
    ca, cb = canon(*a), canon(*b)
    return ca[0] == cb[0] and (ca[1] == cb[1] or "as-printed" in (ca[1], cb[1]))


def leaves(node: Any, inherited: str | None, path: tuple[str, ...]) -> list[tuple[tuple[str, ...], Any, str | None]]:
    if isinstance(node, dict):
        if "formula" in node:
            return []
        if "value" in node:
            return [(path, node["value"], node.get("unit", inherited))]
        inner = node["values"] if "values" in node and "unit" in node else node
        unit = node.get("unit", inherited) if "values" in node else inherited
        return [x for k, c in inner.items() for x in leaves(c, unit, (*path, str(k)))]
    return [(path, node, inherited)]


def first_pass(rule: dict[str, Any], rc: RuleCells) -> None:
    rid = rule["id"]
    for path, value, unit in leaves((rule.get("threshold") or {}).get("values", {}), None, ()):
        unit = unit or ""
        head = path[0]
        if "econ-cycle" in rid:
            rc.put("first", "J6D3", f"cz{path[-1]}", value, unit)
        elif "duct-insulation" in rid:
            key = "flexible" if head == "flexible" else f"{path[1]}.cz{path[2]}"
            rc.put("first", "J6D6", key, value, unit)
        elif "pipe-insulation" in rid:
            if head in EDGE_ALIASES:
                rc.put("first", "J6D9a", f"edge.{head}", value, unit)
            else:
                rc.put("first", "J6D9a", f"{FIRST_BAND.get(head, head)}.{DN_KEYS[path[1]]}", value, unit)
        elif "J6D10" in rid:
            if head in ("cz1_density", "cz2_density"):
                rc.put("first", "J6D10", head.split("_")[0], value, unit)
            elif head == "density":
                rc.put("first", "J6D10", f"cz{path[1]}.{path[2]}" if len(path) > 2 else f"cz{path[1]}", value, unit)
            elif head == "area_split":
                rc.put("first", "J6D10", "area_split", value, unit)
            elif head == "annual_energy":
                rc.put("first", "J6D10", "annual_energy", value, unit)
        else:
            rc.put("first", "clause", head, value, unit)


def numbers_in(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", text)]


def second_pass(rid: str, items: list[dict[str, Any]], rc: RuleCells) -> None:
    first_scalars = {k for (t, k) in rc.cells if t == "clause"}
    scalar_targets = {SCALAR_OVERRIDES.get((rid, k), SCALAR_ALIASES.get(k, "")): k for k in first_scalars}
    edge_names = {v[0]: (k, v[1]) for k, v in EDGE_ALIASES.items()}
    for t in items:
        name, value, unit = t["name"], t["value"], t["unit"] or ""
        if "econ-cycle" in rid and (m := re.fullmatch(r"economy_cycle_trigger_airflow_cz(\d)", name)):
            rc.put("second", "J6D3", f"cz{m[1]}", value, unit)
        elif "duct-insulation" in rid and name.startswith("r_min_") and name != "r_min_cushion_box":
            m = re.fullmatch(r"r_min_(flexible_ductwork|within_conditioned_space|exposed_direct_sunlight|"
                             r"all_other_locations)(?:_cz(1_to_7|8))?", name)
            if not m:
                rc.unkeyed_second.append(name)
                continue
            if m[1] == "flexible_ductwork":
                rc.put("second", "J6D6", "flexible", value, unit)
                continue
            loc = {"within_conditioned_space": "conditioned_space", "exposed_direct_sunlight": "direct_sunlight",
                   "all_other_locations": "other"}[m[1]]
            for cz in (range(1, 8) if m[2] == "1_to_7" else [8]):
                rc.put("second", "J6D6", f"{loc}.cz{cz}", value, unit, "second pass lists a zone range")
        elif "pipe-insulation" in rid:
            if name in edge_names:
                edge, idx = edge_names[name]
                nums = numbers_in(value) if isinstance(value, str) else [value]
                rc.put("second", "J6D9a", f"edge.{edge}", nums[idx], unit)
            elif m := re.fullmatch(r"r_(low_temp_chilled|chilled|heated|high_temp_heated)_dn_(.+)", name):
                rc.put("second", "J6D9a", f"{m[1]}.{DN_KEYS[m[2]]}", value, unit)
            else:
                rc.unkeyed_second.append(f"{name} = {value} {unit}".strip())
        elif "J6D10" in rid:
            if m := re.fullmatch(r"max_elec_heating_cz(\d)_area_(le|gt)_500", name):
                rc.put("second", "J6D10", f"cz{m[1]}.{m[2]}_500", value, unit)
            elif name == "max_elec_heating_cz1_cz2":  # 2025: one value covers both zones and both bands
                for cz in (1, 2):
                    for band in ("le_500", "gt_500"):
                        rc.put("second", "J6D10", f"cz{cz}.{band}", value, unit, "second pass lists CZ1-2 once")
            elif name in ("max_elec_heating_cz1", "max_elec_heating_cz2"):
                rc.put("second", "J6D10", name.rsplit("_", 1)[-1], value, unit)
            elif name == "max_elec_heating_cz8":
                for key in (("cz8.le_500", "cz8.gt_500") if "2025" in rid else ("cz8",)):
                    rc.put("second", "J6D10", key, value, unit)
            elif name == "floor_area_band_edge":
                rc.put("second", "J6D10", "area_split", value, unit)
            elif name == "alt_max_annual_heating_energy":
                rc.put("second", "J6D10", "annual_energy", value, unit)
            else:
                rc.unkeyed_second.append(f"{name} = {value} {unit}".strip())
        elif name in scalar_targets:
            rc.put("second", "clause", scalar_targets[name], value, unit)
        else:
            rc.unkeyed_second.append(f"{name} = {value} {unit}".strip())


def build() -> list[RuleCells]:
    second = yaml.safe_load((ROOT / "evals/dual_encoding/second_pass.yaml").read_text(encoding="utf-8"))
    out = []
    for path in sorted((ROOT / "rules").rglob("*.yaml")):
        if "schema" in path.parts or path.name in ("adoption.yaml", "applicability.yaml"):
            continue
        rule = yaml.safe_load(path.read_text(encoding="utf-8"))
        rc = RuleCells(rule["id"])
        first_pass(rule, rc)
        second_pass(rule["id"], second[rule["id"]]["thresholds"], rc)
        out.append(rc)
    return out


def status(cell: Cell) -> str:
    """match / MISMATCH / first-only / second-only / unresolved."""
    if cell.first and cell.second:
        if cell.second[0] == "UNREADABLE" or cell.first[0] == "TODO_FROM_SOURCE":
            return "unresolved"
        return "match" if same(cell.first, cell.second) else "MISMATCH"
    if cell.first:
        return "first-only"
    if cell.second and cell.second[0] == "UNREADABLE":
        return "unresolved"
    return "second-only"


def swaps(rc: RuleCells) -> list[tuple[str, str]]:
    bad = [c for c in rc.cells.values() if status(c) == "MISMATCH"]
    found = []
    for i, a in enumerate(bad):
        for b in bad[i + 1:]:
            if (a.table == b.table and a.first and a.second and b.first and b.second
                    and same(a.first, b.second) and same(b.first, a.second)):
                found.append((a.key, b.key))
    return found


def fmt(v: tuple[Any, str] | None) -> str:
    return "-" if v is None else f"{v[0]} {v[1]}".strip()


def main() -> int:
    results = build()
    lines = [
        "# Threshold diff: rules vs independent second pass (cell level)",
        "",
        "Generated by `scripts/threshold_diff.py`. For the engineer review only: **no rule was changed",
        "because of this diff.** The second pass was extracted from ABCB by an agent with no access to",
        "`rules/` (`evals/dual_encoding/second_pass.yaml`).",
        "",
        "Method: both passes are reduced to cells keyed by (rule, table, row/zone key) and compared key by",
        "key, never by value. Second-pass names are parsed into keys by this script; anything it cannot",
        "key is listed as unkeyed. Units are compared through pint where the second pass printed one.",
        "A *swap* is two cells whose values are exchanged between the passes.",
        "",
    ]
    total = {"match": 0, "MISMATCH": 0, "first-only": 0, "second-only": 0, "unresolved": 0}
    buckets: dict[str, list[str]] = {"MISMATCH": [], "first-only": [], "second-only": [], "unresolved": []}
    swapped: list[str] = []
    unkeyed: list[str] = []
    for rc in results:
        for (table, key), cell in sorted(rc.cells.items()):
            st = status(cell)
            total[st] += 1
            if st in buckets:
                buckets[st].append(
                    f"- `{rc.rule_id}` {table} `{key}`: rules {fmt(cell.first)} | second pass {fmt(cell.second)}")
        swapped += [f"- `{rc.rule_id}`: `{a}` and `{b}` are exchanged" for a, b in swaps(rc)]
        unkeyed += [f"- `{rc.rule_id}`: {n}" for n in rc.unkeyed_second]
    lines += ["## Totals", "", *[f"- {k}: {v}" for k, v in total.items()], "",
              "## Cell-level disagreements", "", *(buckets["MISMATCH"] or ["None."]), "",
              "## Swapped cells", "", *(swapped or ["None."]), "",
              "## Cells only in the rules (second pass has no cell for that key)", "",
              *(buckets["first-only"] or ["None."]), "",
              "## Cells only in the second pass (rules do not hold them as thresholds)", "",
              *(buckets["second-only"] or ["None."]), "",
              "## Unresolved", "", *(buckets["unresolved"] or ["None."]), "",
              "## Second-pass entries this script could not key", "",
              "Text conditions and values with no matching threshold (exemption conditions, notes).", "",
              *(unkeyed or ["None."]), "",
              "## Notes", "",
              "- Dead band: rules store 2 K, the page prints degC; a 2 degC difference is a 2 K delta.",
              "- R-values are printed without a unit; the rules assume m^2.K/W.",
              "- The heating trigger is printed as kW with a 'heating' subscript and the cooling trigger as kWr;",
              "  the rules store both as kW.",
              "- CZ8 in Table J6D10 has no column in either edition; 2025 J6D10(1)(e)(i)(B) refers to zones 3 to 8.",
              ""]
    out = ROOT / "docs" / "threshold-diff.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}: {total}, swaps={len(swapped)}")
    return 1 if (buckets["MISMATCH"] or swapped) else 0


if __name__ == "__main__":
    sys.exit(main())
