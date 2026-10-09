"""Health score for an ingested file: a percentage and a fix list. Pure arithmetic on what the readers measured.

Seven checks (weights in ingest/health.yaml, an engineering policy of this product): closed boundaries, scale, layers,
duplicates, names, schema validity, exporter. Below `minimum_percent` the report recommends requesting a clean IFC/DXF.
It never judges compliance and never confirms a value: a high score only means the file read well.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mep.ingest.records import IngestResult

POLICY_PATH = Path(__file__).parent / "health.yaml"
CHECKS = ("closed_boundaries", "scale", "layers", "duplicates", "names", "schema", "exporter")
PLAUSIBLE_AREA_M2 = (0.5, 5000.0)       # a single space outside this range is more likely a unit or scale error


@dataclass(frozen=True)
class Check:
    name: str
    weight: int
    fraction: float                      # 0..1
    detail: str


@dataclass
class HealthReport:
    score_percent: float
    minimum_percent: float
    below_threshold: bool
    checks: dict[str, Check]
    fixes: list[str] = field(default_factory=list)
    recommendation: str = ""


def load_policy(path: Path | None = None) -> dict[str, Any]:
    data = yaml.safe_load((path or POLICY_PATH).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("weights"), dict):
        raise ValueError("health policy must be a mapping with weights")  # noqa: TRY004
    weights = data["weights"]
    if set(weights) != set(CHECKS) or not all(isinstance(w, int) and not isinstance(w, bool) and w >= 0 for w in weights.values()):
        raise ValueError(f"health policy weights must cover exactly {CHECKS} with whole numbers")
    if sum(weights.values()) != 100:
        raise ValueError("health policy weights must sum to 100")
    minimum = data.get("minimum_percent")
    if isinstance(minimum, bool) or not isinstance(minimum, int | float) or not 0 < minimum < 100:
        raise ValueError("minimum_percent must be a number between 0 and 100")
    return data


def _ratio(part: float, whole: float) -> float:
    return 0.0 if whole <= 0 else max(0.0, min(1.0, part / whole))


def _int(meta: dict[str, Any], key: str) -> int:
    value = meta.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _exporter(meta: dict[str, Any], fixes: list[str]) -> tuple[float, str]:
    profile, status = meta.get("exporter_profile"), meta.get("profile_status")
    if profile == "unknown" or profile is None:
        fixes.append("The exporting program was not recognised: every value needs checking at Gate 1.")
        return 0.25, "exporter not recognised"
    if status != "verified":
        fixes.append(f"The {profile} exporter profile has not been verified against a real export: check values at Gate 1.")
        return 0.5, f"{profile} profile unverified"
    return 1.0, f"{profile} profile verified"


def _score_ifc(r: IngestResult, fixes: list[str]) -> dict[str, tuple[float, str]]:
    m = r.metadata
    total = _int(m, "spaces_total")
    with_area, unnamed = _int(m, "spaces_with_area"), _int(m, "unnamed_spaces")
    dups, no_storey = _int(m, "duplicate_spaces"), _int(m, "spaces_without_storey")
    out: dict[str, tuple[float, str]] = {}
    out["closed_boundaries"] = (_ratio(with_area, total), f"{with_area} of {total} spaces have an area")
    if total and with_area < total:
        fixes.append(f"{total - with_area} of {total} spaces have no usable area (no quantity and no closed geometry).")
    areas = [s.area_m2 for s in r.spaces if s.area_m2 is not None]
    plausible = sum(1 for a in areas if PLAUSIBLE_AREA_M2[0] <= a <= PLAUSIBLE_AREA_M2[1])
    scale = _ratio(plausible, len(areas)) if areas else 0.0
    out["scale"] = (scale, f"{plausible} of {len(areas)} areas are plausible")
    if areas and plausible < len(areas):
        fixes.append(f"{len(areas) - plausible} of {len(areas)} space areas are outside {PLAUSIBLE_AREA_M2[0]} to "
                     f"{PLAUSIBLE_AREA_M2[1]} m2: check the model's units and scale.")
    if not areas:
        fixes.append("No space has an area, so the scale cannot be checked.")
    out["layers"] = (_ratio(total - no_storey, total), f"{total - no_storey} of {total} spaces sit on a storey")
    if no_storey:
        fixes.append(f"{no_storey} of {total} spaces are not assigned to a building storey.")
    out["duplicates"] = (1.0 - _ratio(dups, total) if total else 0.0, f"{dups} duplicate spaces")
    if dups:
        fixes.append(f"{dups} of {total} spaces duplicate another space (same name, storey and area).")
    out["names"] = (1.0 - _ratio(unnamed, total) if total else 0.0, f"{unnamed} unnamed spaces")
    if unnamed:
        fixes.append(f"{unnamed} of {total} spaces have no name.")
    supported = bool(m.get("schema_supported"))
    out["schema"] = (1.0 if supported else 0.0, f"schema {m.get('schema')}")
    if not supported:
        fixes.append(f"The IFC schema {m.get('schema')} is not one this tool reads (IFC2X3, IFC4, IFC4X3): export again.")
    fraction, detail = _exporter(m, fixes)
    out["exporter"] = (fraction, detail)
    return out


def _score_dxf(r: IngestResult, fixes: list[str]) -> dict[str, tuple[float, str]]:
    m = r.metadata
    closed, open_, dups = _int(m, "closed_polylines"), _int(m, "open_polylines"), _int(m, "duplicate_polylines")
    emitted = len(r.spaces)
    out: dict[str, tuple[float, str]] = {}
    out["closed_boundaries"] = (_ratio(emitted, closed + open_), f"{emitted} usable of {closed + open_} outlines")
    if open_:
        fixes.append(f"{open_} outlines on space layers are open (not closed): close them or request a clean drawing.")
    skipped = _int(m, "self_intersecting") + _int(m, "curved_polylines")
    if skipped:
        fixes.append(f"{skipped} outlines were skipped (self-intersecting or containing arcs).")
    if m.get("scale_ok") and m.get("scale_check") == "ok":
        scale, detail = 1.0, f"units {m.get('units')}, extents plausible"
    elif m.get("scale_ok") and m.get("scale_check") == "implausible":
        scale, detail = 0.3, "units known but the drawing size is implausible"
        fixes.append("The drawing size is implausible for a building: check the scale and units.")
    else:
        scale, detail = 0.0, f"units {m.get('units')}: scale unknown"
        fixes.append("The drawing has no usable units: scale is unknown, so no areas were read. Request a DXF with units set.")
    out["scale"] = (scale, detail)
    layers = m.get("space_layers") or []
    out["layers"] = (1.0 if layers else 0.0, f"{len(layers)} space layers")
    if not layers:
        fixes.append("No layer looks like a space/room/area layer: ask for rooms on a named layer.")
    out["duplicates"] = (1.0 - _ratio(dups, closed) if closed else 0.0, f"{dups} duplicate outlines")
    if dups:
        fixes.append(f"{dups} duplicate outlines were skipped.")
    labelled = _int(m, "labelled_spaces")
    out["names"] = (_ratio(labelled, emitted), f"{labelled} of {emitted} spaces have a label inside them")
    if emitted and labelled < emitted:
        fixes.append(f"{emitted - labelled} of {emitted} spaces have no text label inside their outline (no room label).")
    out["schema"] = (1.0, "file parsed")
    out["exporter"] = (0.5, "a DXF does not identify its exporter")
    return out


def score(result: IngestResult, *, policy: dict[str, Any] | None = None) -> HealthReport:
    policy = policy or load_policy()
    weights: dict[str, int] = policy["weights"]
    minimum = float(policy["minimum_percent"])
    fixes: list[str] = []
    if result.source_kind == "ifc":
        raw = _score_ifc(result, fixes)
    elif result.source_kind == "dxf":
        raw = _score_dxf(result, fixes)
    else:
        raw = {name: (0.0, "not a model or drawing") for name in CHECKS}
    checks = {name: Check(name, weights[name], max(0.0, min(1.0, raw[name][0])), raw[name][1]) for name in CHECKS}
    total = round(sum(c.weight * c.fraction for c in checks.values()), 1)
    below = total < minimum
    recommendation = ""
    if result.source_kind not in ("ifc", "dxf"):
        recommendation = (f"{result.source_kind.upper()} values are machine-read (vision/spreadsheet), not a model: "
                          "every value must be confirmed by hand at Gate 1; request a clean IFC or DXF if you can.")
    elif below:
        recommendation = (f"Health {total}% is below {minimum:g}%: request a clean IFC (or DXF) from the architect, or use "
                          "the manual trace for the spaces you need.")
    return HealthReport(total, minimum, below, checks, fixes, recommendation)
