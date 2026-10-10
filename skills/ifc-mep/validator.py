"""Independent validator for ifc-mep output: re-reads the produced IFC (and the architect's file when it is supplied) and compares them with the spec.

`validate(spec, files)` takes the NORMALISED spec (the `inputs` of manifest.json) and the files: the produced .ifc, the architect's file (recognised by its
checksum) and optionally manifest.json. Nothing is trusted from the builder: every number is measured from the file. Tolerance: 0.5 mm; connected ports must
coincide within 1 mm.
"""
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOL_MM = 0.5
PORT_TOL_MM = 1.0
SUPPORTED_SCHEMAS = ("IFC4", "IFC4X3", "IFC4X3_ADD2")
CLASSES = {"duct": "IfcDuctSegment", "terminal": "IfcAirTerminal", "ahu": "IfcUnitaryEquipment", "fan": "IfcFan"}
ADDED_CLASSES = ("IfcDuctSegment", "IfcAirTerminal", "IfcUnitaryEquipment", "IfcFan", "IfcDistributionPort", "IfcDistributionSystem")


@dataclass
class ValidationResult:
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def failed(self) -> list[str]:
        return [c["name"] for c in self.checks if not c["passed"]]


class _CountLogger:
    """ifcopenshell.validate wants a logger; this one only counts errors."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def info(self, *a: Any, **k: Any) -> None: ...
    def debug(self, *a: Any, **k: Any) -> None: ...
    def warning(self, *a: Any, **k: Any) -> None: ...

    def error(self, msg: Any, *a: Any, **k: Any) -> None:
        self.errors.append(str(msg)[:120])

    critical = error


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _mm_factor(f: Any) -> float:
    import ifcopenshell.util.unit as u

    return float(u.calculate_unit_scale(f)) * 1000.0         # file units -> mm


def _world(el: Any) -> Any:
    import ifcopenshell.util.placement as pl

    return pl.get_local_placement(el.ObjectPlacement)


def _ports(el: Any) -> dict[str, Any]:
    return {o.Name: o for r in (el.IsNestedBy or []) for o in r.RelatedObjects if o.is_a("IfcDistributionPort")}


def _by_tag(f: Any, cls: str) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for e in f.by_type(cls):
        out.setdefault(str(e.Tag), []).append(e)
    return out


def validate(spec: dict[str, Any], files: list[Path]) -> ValidationResult:
    import ifcopenshell
    import ifcopenshell.util.element as el_util
    import ifcopenshell.util.system as su
    import numpy as np

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, expected: Any, actual: Any, tol: Any = None) -> None:
        checks.append({"name": name, "passed": bool(ok), "expected": expected, "actual": actual, "tolerance": tol})

    paths = [Path(f) for f in files]
    manifest = next((p for p in paths if p.name == "manifest.json"), None)
    ifcs = [p for p in paths if p.suffix.lower() == ".ifc"]
    base_path = next((p for p in ifcs if _sha(p) == spec["base_ifc_sha256"]), None)
    out_path = next((p for p in ifcs if p != base_path), None)
    if out_path is None:
        check("ifc_present", False, "a produced .ifc file", None)
        return ValidationResult(False, checks)
    try:
        f = ifcopenshell.open(str(out_path))
    except Exception as exc:  # noqa: BLE001 - unreadable file
        check("ifc_readable", False, "a readable IFC", type(exc).__name__)
        return ValidationResult(False, checks)
    check("schema_supported", f.schema in SUPPORTED_SCHEMAS, list(SUPPORTED_SCHEMAS), f.schema)
    k = _mm_factor(f)
    base = None
    if base_path is not None:
        base = ifcopenshell.open(str(base_path))
        # the architect's model must be intact
        base_ids = {e.GlobalId for e in base if e.is_a("IfcRoot")}
        out_ids = {e.GlobalId for e in f if e.is_a("IfcRoot")}
        lost = sorted(base_ids - out_ids)
        check("architect_model_preserved", not lost, "every architect GlobalId is still there", lost[:5])
        try:
            import ifcopenshell.validate as v

            lo, lb = _CountLogger(), _CountLogger()
            v.validate(f, lo)
            v.validate(base, lb)
            check("schema_valid", len(lo.errors) <= len(lb.errors), f"no more errors than the architect's own file ({len(lb.errors)})", len(lo.errors))
        except Exception as exc:  # noqa: BLE001 - the validation library failed: do not call it valid
            check("schema_valid", False, "the schema check ran", type(exc).__name__)
    else:
        check("architect_file_supplied", False, "the architect's file (base.ifc) to compare with", None)

    groups = [("duct", spec["ducts"]), ("terminal", spec.get("terminals", [])), *[(q["kind"], [q]) for q in spec.get("equipment", [])]]
    expected = {e["tag"]: (kind, e) for kind, items in groups for e in items}
    found: dict[str, Any] = {}
    problems: list[str] = []
    for kind, cls in CLASSES.items():
        for tag, els in _by_tag(f, cls).items():
            if tag in expected and expected[tag][0] == kind:
                if len(els) != 1:
                    problems.append(f"{tag}: {len(els)} elements")
                found[tag] = els[0]
    missing = sorted(set(expected) - set(found))
    check("elements_present_once", not missing and not problems, sorted(expected), {"missing": missing, "problems": problems[:5]})
    if base is not None:
        new_ids = {e.GlobalId for e in f if e.is_a("IfcRoot")} - {e.GlobalId for e in base if e.is_a("IfcRoot")}
        n_new = sum(1 for e in f if e.is_a("IfcRoot") and e.GlobalId in new_ids and any(e.is_a(c) for c in ADDED_CLASSES))
        n_ports = 2 * len(spec["ducts"]) + len(spec.get("terminals", [])) + 2 * len(spec.get("equipment", []))
        n_systems = len({e["system"] for _, items in groups for e in items})
        want = len(expected) + n_ports + n_systems
        check("nothing_else_added", n_new == want, want, n_new)

    # storeys and systems
    bad_storey, bad_system = [], []
    for tag, el in found.items():
        want = expected[tag][1]
        cont = el_util.get_container(el)
        if cont is None or not cont.is_a("IfcBuildingStorey") or cont.Name != want["storey"]:
            bad_storey.append(f"{tag}: in {getattr(cont, 'Name', None)!r}, expected {want['storey']!r}")
        names = {s.Name for s in su.get_element_systems(el)}
        if names != {want["system"]}:
            bad_system.append(f"{tag}: systems {sorted(n for n in names if n)} expected {want['system']}")
    check("storey_assignment", not bad_storey, [], bad_storey[:5])
    check("system_assignment", not bad_system, [], bad_system[:5])

    # duct sizes and placement, measured from the geometry
    bad_geo = []
    for d in spec["ducts"]:
        el = found.get(d["tag"])
        if el is None or el.Representation is None:
            continue
        solid = next((i for r in el.Representation.Representations for i in r.Items if i.is_a("IfcExtrudedAreaSolid")), None)
        if solid is None:
            bad_geo.append(f"{d['tag']}: no extruded solid")
            continue
        prof = solid.SweptArea
        size = d["size"]
        if size["shape"] == "rect":
            ok = prof.is_a("IfcRectangleProfileDef") and abs(prof.XDim * k - size["width_mm"]) <= TOL_MM and abs(prof.YDim * k - size["depth_mm"]) <= TOL_MM
        else:
            ok = prof.is_a("IfcCircleProfileDef") and abs(prof.Radius * k * 2 - size["diameter_mm"]) <= TOL_MM
        if not ok:
            bad_geo.append(f"{d['tag']}: profile does not match the size")
        want_len = math.dist(d["start_mm"], d["end_mm"])
        if abs(solid.Depth * k - want_len) > TOL_MM:
            bad_geo.append(f"{d['tag']}: length {solid.Depth * k:.1f}, expected {want_len:.1f}")
        storey = el_util.get_container(el)
        if storey is None:
            continue
        m = _world(el)
        sm = _world(storey)
        origin = (m[:3, 3]) * k
        want_origin = sm[:3, :3] @ np.array(d["start_mm"]) + sm[:3, 3] * k
        if np.linalg.norm(origin - want_origin) > TOL_MM:
            bad_geo.append(f"{d['tag']}: start is {np.linalg.norm(origin - want_origin):.1f} mm from where the spec puts it")
        axis = m[:3, 2]
        u = np.array([b - a for a, b in zip(d["start_mm"], d["end_mm"], strict=True)])
        u = sm[:3, :3] @ (u / np.linalg.norm(u))
        if np.linalg.norm(axis - u) > 1e-4:
            bad_geo.append(f"{d['tag']}: direction differs from the spec")
    check("duct_geometry", not bad_geo, [], bad_geo[:5], TOL_MM)

    # ports and connections
    port_problems = []
    all_ports: dict[str, Any] = {}
    for tag, (kind, e) in expected.items():
        el = found.get(tag)
        if el is None:
            continue
        have = _ports(el)
        want_names = ({f"{tag}.start", f"{tag}.end"} if kind == "duct" else {f"{tag}.in"} if kind == "terminal" else {f"{tag}.in", f"{tag}.out"})
        if set(have) != want_names:
            port_problems.append(f"{tag}: ports {sorted(have)} expected {sorted(want_names)}")
        all_ports.update(have)
    check("ports_present", not port_problems, [], port_problems[:5])
    rels = f.by_type("IfcRelConnectsPorts")
    got_pairs = {(r.RelatingPort.Name, r.RelatedPort.Name) for r in rels if r.RelatingPort and r.RelatedPort}
    want_pairs = {(c["from"], c["to"]) for c in spec.get("connections", [])}
    in_scope = {p for p in got_pairs if p[0].split(".")[0] in expected or p[1].split(".")[0] in expected}
    check("connections_match_spec", in_scope == want_pairs, sorted(want_pairs), sorted(in_scope))
    gap_problems = []
    for a_name, b_name in want_pairs:
        a, b = all_ports.get(a_name), all_ports.get(b_name)
        if a is None or b is None:
            continue
        pa, pb = _world(a)[:3, 3] * k, _world(b)[:3, 3] * k
        if np.linalg.norm(pa - pb) > PORT_TOL_MM:
            gap_problems.append(f"{a_name} -> {b_name}: {np.linalg.norm(pa - pb):.1f} mm apart")
        if a.FlowDirection != "SOURCE" or b.FlowDirection != "SINK":
            gap_problems.append(f"{a_name} -> {b_name}: flow direction {a.FlowDirection}->{b.FlowDirection}, expected SOURCE->SINK")
    check("connected_ports_meet_with_flow", not gap_problems, f"within {PORT_TOL_MM} mm, SOURCE to SINK", gap_problems[:5], PORT_TOL_MM)

    # airflow balance per system
    systems: dict[str, dict[str, float]] = {}
    for d in spec["ducts"]:
        s = systems.setdefault(d["system"], {"trunk": 0.0, "terminals": 0.0, "n": 0})
        s["trunk"] = max(s["trunk"], float(d["airflow_ls"]))
    for t in spec.get("terminals", []):
        s = systems.setdefault(t["system"], {"trunk": 0.0, "terminals": 0.0, "n": 0})
        s["terminals"] += float(t["airflow_ls"])
        s["n"] += 1
    off = [f"{n}: trunk {s['trunk']:g} L/s vs terminals {s['terminals']:g} L/s" for n, s in sorted(systems.items())
           if s["n"] and abs(s["trunk"] - s["terminals"]) / max(s["trunk"], s["terminals"]) > 0.01]
    check("airflow_balance", not off, "within 1 %", off[:5], 1)

    if manifest is not None:
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            ok = all(_sha(out_path.parent / x["name"]) == x["sha256"] for x in m["files"])
        except (OSError, ValueError, KeyError):
            ok = False
        check("manifest_checksums", ok, "files match the manifest", ok)
    return ValidationResult(all(c["passed"] for c in checks), checks)
