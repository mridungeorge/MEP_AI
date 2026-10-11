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


def _is_origin(c: Any) -> bool:
    return all(abs(float(v)) < 1e-9 for v in c)


def _default_axes(pos: Any) -> bool:
    axis = None if pos.Axis is None else tuple(round(float(v), 9) for v in pos.Axis.DirectionRatios)
    ref = None if pos.RefDirection is None else tuple(round(float(v), 9) for v in pos.RefDirection.DirectionRatios)
    return axis in (None, (0.0, 0.0, 1.0)) and ref in (None, (1.0, 0.0, 0.0))


def _storey_frame(el: Any, k: float) -> tuple[Any, Any] | None:
    import ifcopenshell.util.element as eu

    storey = eu.get_container(el)
    if storey is None:
        return None
    m = _world(storey)
    return m[:3, :3], m[:3, 3] * k


def _duct_axes(start: list[float], end: list[float]) -> tuple[Any, Any, float]:
    import numpy as np

    v = np.array(end, dtype=float) - np.array(start, dtype=float)
    length = float(np.linalg.norm(v))
    u = v / length
    r = np.array([1.0, 0.0, 0.0]) if abs(u[2]) > 0.99 else np.array([-u[1], u[0], 0.0]) / math.hypot(u[0], u[1])
    return u, r, length


ALLOWED_NEW_ROOTS = (*ADDED_CLASSES, "IfcRelNests", "IfcRelConnectsPorts", "IfcRelAssignsToGroup", "IfcRelContainedInSpatialStructure", "IfcPropertySet", "IfcRelDefinesByProperties")
MAY_CHANGE = ("IfcRelContainedInSpatialStructure", "IfcOwnerHistory")


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
        changed = []
        for e in base:
            if e.is_a() in MAY_CHANGE:
                continue
            try:
                o = f.by_id(e.id())
            except RuntimeError:
                changed.append(f"#{e.id()} {e.is_a()} is gone")
                continue
            if str(o) != str(e):
                changed.append(f"#{e.id()} {e.is_a()} changed")
        check("architect_model_preserved", not lost and not changed, "every architect GlobalId is there and no architect entity is altered", (lost + changed)[:5])
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
        new_roots = [e for e in f if e.is_a("IfcRoot") and e.GlobalId not in {x.GlobalId for x in base if x.is_a("IfcRoot")}]
        stray = sorted({e.is_a() for e in new_roots if not any(e.is_a(c) for c in ALLOWED_NEW_ROOTS)})
        n_new = sum(1 for e in new_roots if any(e.is_a(c) for c in ADDED_CLASSES))
        n_ports = 2 * len(spec["ducts"]) + len(spec.get("terminals", [])) + 2 * len(spec.get("equipment", []))
        n_systems = len({e["system"] for _, items in groups for e in items})
        want = len(expected) + n_ports + n_systems
        check("nothing_else_added", n_new == want and not stray, {"products_ports_systems": want, "other_new_classes": []}, {"products_ports_systems": n_new, "other_new_classes": stray})

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

    # geometry measured from the file: placement, axes, profile and extrusion of every element, and its property set read back
    bad_geo: list[str] = []
    bad_pset: list[str] = []
    for tag, (kind, e) in expected.items():
        el = found.get(tag)
        if el is None:
            continue
        rep = el.Representation.Representations if el.Representation is not None else []
        if len(rep) != 1 or len(rep[0].Items) != 1 or not rep[0].Items[0].is_a("IfcExtrudedAreaSolid"):
            bad_geo.append(f"{tag}: not exactly one extruded solid")
            continue
        solid = rep[0].Items[0]
        prof = solid.SweptArea
        if (solid.Position is None or not _is_origin(solid.Position.Location.Coordinates) or not _default_axes(solid.Position)
                or tuple(round(float(v), 9) for v in solid.ExtrudedDirection.DirectionRatios) != (0.0, 0.0, 1.0)):
            bad_geo.append(f"{tag}: the solid is offset or tilted inside its own placement")
        pp = getattr(prof, "Position", None)
        if pp is None or not _is_origin(pp.Location.Coordinates) or (pp.RefDirection is not None and tuple(round(float(v), 9) for v in pp.RefDirection.DirectionRatios) != (1.0, 0.0)):
            bad_geo.append(f"{tag}: the profile is offset or turned inside its own placement")
        frame = _storey_frame(el, k)
        if frame is None:
            continue
        rot, trans = frame
        m = _world(el)
        origin = m[:3, 3] * k
        if kind == "duct":
            size = e["size"]
            if size["shape"] == "rect":
                ok = prof.is_a("IfcRectangleProfileDef") and abs(prof.XDim * k - size["width_mm"]) <= TOL_MM and abs(prof.YDim * k - size["depth_mm"]) <= TOL_MM
            else:
                ok = prof.is_a("IfcCircleProfileDef") and abs(prof.Radius * k * 2 - size["diameter_mm"]) <= TOL_MM
            if not ok:
                bad_geo.append(f"{tag}: profile does not match the size")
            u, r, length = _duct_axes(e["start_mm"], e["end_mm"])
            if abs(solid.Depth * k - length) > TOL_MM:
                bad_geo.append(f"{tag}: length {solid.Depth * k:.1f}, expected {length:.1f}")
            want_origin = rot @ np.array(e["start_mm"]) + trans
            if np.linalg.norm(origin - want_origin) > TOL_MM:
                bad_geo.append(f"{tag}: start is {np.linalg.norm(origin - want_origin):.1f} mm from where the spec puts it")
            if np.linalg.norm(m[:3, 2] - rot @ u) > 1e-4:
                bad_geo.append(f"{tag}: direction differs from the spec")
            if np.linalg.norm(m[:3, 0] - rot @ r) > 1e-4:
                bad_geo.append(f"{tag}: turned about its own axis (width and depth swapped)")
        else:
            box = e.get("box") or {"length_mm": 300.0, "width_mm": 300.0, "height_mm": 100.0}
            at = e["at_mm"]
            if not (prof.is_a("IfcRectangleProfileDef") and abs(prof.XDim * k - box["length_mm"]) <= TOL_MM and abs(prof.YDim * k - box["width_mm"]) <= TOL_MM
                    and abs(solid.Depth * k - box["height_mm"]) <= TOL_MM):
                bad_geo.append(f"{tag}: box size does not match the spec")
            want_origin = rot @ np.array(at) + trans
            if np.linalg.norm(origin - want_origin) > TOL_MM:
                bad_geo.append(f"{tag}: is {np.linalg.norm(origin - want_origin):.1f} mm from where the spec puts it")
            if np.linalg.norm(m[:3, :3] - rot) > 1e-4:
                bad_geo.append(f"{tag}: rotated")
        props = el_util.get_pset(el, "MEP_Services") or {}
        if props.get("System") != e["system"]:
            bad_pset.append(f"{tag}: System {props.get('System')!r}")
        if kind in ("duct", "terminal") and not math.isclose(float(props.get("AirflowLs", -1)), float(e["airflow_ls"]), rel_tol=1e-9, abs_tol=1e-9):
            bad_pset.append(f"{tag}: AirflowLs {props.get('AirflowLs')} expected {e['airflow_ls']}")
        if kind == "duct":
            keys = {"WidthMm": e["size"].get("width_mm"), "DepthMm": e["size"].get("depth_mm"), "DiameterMm": e["size"].get("diameter_mm")}
            for key, want in keys.items():
                if (want is None) != (key not in props) or (want is not None and abs(float(props[key]) - float(want)) > TOL_MM):
                    bad_pset.append(f"{tag}: {key}")
    check("element_geometry", not bad_geo, [], bad_geo[:5], TOL_MM)
    check("element_properties_read_back", not bad_pset, "MEP_Services matches the spec", bad_pset[:5])

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
    bad_pos = []
    for tag, (kind, e) in expected.items():
        el = found.get(tag)
        frame = None if el is None else _storey_frame(el, k)
        if el is None or frame is None:
            continue
        rot, trans = frame
        if kind == "duct":
            want = {f"{tag}.start": e["start_mm"], f"{tag}.end": e["end_mm"]}
            want = {n: rot @ np.array(v) + trans for n, v in want.items()}
        elif kind == "terminal":
            want = {f"{tag}.in": rot @ np.array(e["at_mm"]) + trans}
        else:
            half_l, half_h = e["box"]["length_mm"] / 2.0, e["box"]["height_mm"] / 2.0
            at = np.array(e["at_mm"])
            want = {f"{tag}.in": rot @ (at + np.array([-half_l, 0.0, half_h])) + trans, f"{tag}.out": rot @ (at + np.array([half_l, 0.0, half_h])) + trans}
        have = _ports(el)
        for name, pos in want.items():
            if name in have and np.linalg.norm(_world(have[name])[:3, 3] * k - pos) > PORT_TOL_MM:
                bad_pos.append(f"{name}: {np.linalg.norm(_world(have[name])[:3, 3] * k - pos):.1f} mm from where it belongs")
    check("port_positions", not bad_pos, f"every port within {PORT_TOL_MM} mm of its place", bad_pos[:5], PORT_TOL_MM)
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

    # airflow balance per system, from the values READ BACK from the file
    systems: dict[str, dict[str, float]] = {}
    for tag, (kind, e) in expected.items():
        el = found.get(tag)
        if el is None or kind not in ("duct", "terminal"):
            continue
        val = float((el_util.get_pset(el, "MEP_Services") or {}).get("AirflowLs", 0.0))
        s_ = systems.setdefault(e["system"], {"trunk": 0.0, "terminals": 0.0, "n": 0})
        if kind == "duct":
            s_["trunk"] = max(s_["trunk"], val)
        else:
            s_["terminals"] += val
            s_["n"] += 1
    tol = float(spec.get("balance_tolerance_pct", 1))
    off = [f"{n}: trunk {v['trunk']:g} L/s vs terminals {v['terminals']:g} L/s" for n, v in sorted(systems.items())
           if v["n"] and abs(v["trunk"] - v["terminals"]) / max(v["trunk"], v["terminals"], 1e-12) * 100.0 > tol]
    check("airflow_balance", not off, f"within {tol:g} %", off[:5], tol)

    if manifest is not None:
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            ok = all(_sha(out_path.parent / x["name"]) == x["sha256"] for x in m["files"])
        except (OSError, ValueError, KeyError):
            ok = False
        check("manifest_checksums", ok, "files match the manifest", ok)
    return ValidationResult(all(c["passed"] for c in checks), checks)
