"""Independent validator for space-envelope-stack output: re-reads the combined IFC and every storey's DXF from disk and compares them with the spec.

`validate(spec, files)` takes the NORMALISED multi-storey spec (the `inputs` of manifest.json) and the produced files (`<mark>.ifc`, `<mark>-S01.dxf` ..., optionally
manifest.json). The DXF checks are space-envelope's own, run per storey against that storey's card. Tolerance: 0.5 mm; areas 0.1 %.
"""
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent
SE_DIR = SKILL_DIR.parent / "space-envelope"
for _p in (str(SE_DIR), str(SKILL_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TOL_MM = 0.5
AREA_REL_TOL = 1e-3


@dataclass
class ValidationResult:
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def failed(self) -> list[str]:
        return [c["name"] for c in self.checks if not c["passed"]]


def _se_validator() -> Any:
    name = "space_envelope_validator_for_stack"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SE_DIR / "validator.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _storey_spec(spec: dict[str, Any], i: int) -> dict[str, Any]:
    s = spec["storeys"][i]
    return {"spec_version": "1", "mark": f"{spec['mark']}-S{i + 1:02d}", "units": "mm",
            "storey": {"name": s["name"], "elevation_mm": s["elevation_mm"], "floor_to_floor_mm": s["floor_to_floor_mm"]}, "rooms": s["rooms"]}


DXF_CHECKS = ("dxf_loads", "dxf_units", "dxf_layers", "dxf_entities", "dxf_room_count", "dxf_outlines", "dxf_areas", "dxf_labels")


def validate(spec: dict[str, Any], files: list[Path]) -> ValidationResult:
    import ifcopenshell

    se = _se_validator()
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, expected: Any, actual: Any, tol: Any = None) -> None:
        checks.append({"name": name, "passed": bool(ok), "expected": expected, "actual": actual, "tolerance": tol})

    paths = [Path(f) for f in files]
    mark = spec["mark"]
    ifcs = [p for p in paths if p.suffix.lower() == ".ifc"]
    dxfs = {p.name: p for p in paths if p.suffix.lower() == ".dxf"}
    manifest = next((p for p in paths if p.name == "manifest.json"), None)
    want_dxf = [f"{mark}-S{i + 1:02d}.dxf" for i in range(len(spec["storeys"]))]
    check("input_files", len(ifcs) == 1 and set(dxfs) == set(want_dxf), [f"{mark}.ifc", *want_dxf], sorted(p.name for p in paths if p.name != "manifest.json"))
    if len(ifcs) != 1:
        return ValidationResult(False, checks)
    try:
        f = ifcopenshell.open(str(ifcs[0]))
    except Exception as exc:  # noqa: BLE001 - unreadable file
        check("ifc_loads", False, "a readable IFC", type(exc).__name__)
        return ValidationResult(False, checks)
    check("ifc_schema", str(f.schema) == "IFC4", "IFC4", str(f.schema))
    projects = f.by_type("IfcProject")
    check("ifc_project", len(projects) == 1 and projects[0].Name == mark, (1, mark), (len(projects), projects[0].Name if projects else None))
    units = {u.UnitType: (getattr(u, "Prefix", None), getattr(u, "Name", None)) for u in projects[0].UnitsInContext.Units if u.is_a("IfcSIUnit")} if projects else {}
    check("ifc_units", units.get("LENGTHUNIT") == ("MILLI", "METRE"), ("MILLI", "METRE"), units.get("LENGTHUNIT"))

    # storeys: exactly the specified ones, with names and elevations
    st_in = sorted(((s.Name, float(s.Elevation or 0)) for s in f.by_type("IfcBuildingStorey")), key=lambda x: x[1])
    st_want = [(s["name"], float(s["elevation_mm"])) for s in spec["storeys"]]
    ok = len(st_in) == len(st_want) and all(a[0] == b[0] and abs(a[1] - b[1]) <= TOL_MM for a, b in zip(st_in, st_want, strict=True))
    check("ifc_storeys", ok, st_want, st_in, TOL_MM)

    # spaces per storey: aggregation, names, ids, footprints, areas, heights, plant flags
    problems: list[str] = []
    all_spaces = f.by_type("IfcSpace")
    want_total = sum(len(s["rooms"]) for s in spec["storeys"])
    if len(all_spaces) != want_total:
        problems.append(f"{len(all_spaces)} spaces, expected {want_total}")
    by_storey = {s.Name: s for s in f.by_type("IfcBuildingStorey")}
    for st in spec["storeys"]:
        storey = by_storey.get(st["name"])
        if storey is None:
            continue
        agg = {str(o.Name): o for r in (storey.IsDecomposedBy or []) for o in r.RelatedObjects if o.is_a("IfcSpace")}
        want_names = {r["name"] for r in st["rooms"]}
        if set(agg) != want_names:
            problems.append(f"{st['name']}: spaces {sorted(agg)} expected {sorted(want_names)}")
            continue
        for room in st["rooms"]:
            sp = agg[room["name"]]
            if sp.GlobalId != se._expected_guid(mark, room["name"]):
                problems.append(f"{room['name']}: GlobalId")
            try:
                solid = sp.Representation.Representations[0].Items[0]
                pts = [(float(p.Coordinates[0]), float(p.Coordinates[1])) for p in solid.SweptArea.OuterCurve.Points]
                pts = pts[:-1] if pts[0] == pts[-1] else pts
                if not se._same_outline(pts, [(float(x), float(y)) for x, y in room["outline"]]):
                    problems.append(f"{room['name']}: footprint")
                if abs(float(solid.Depth) - float(room["height_mm"])) > TOL_MM:
                    problems.append(f"{room['name']}: height")
            except (AttributeError, IndexError, TypeError):
                problems.append(f"{room['name']}: no extruded footprint")
                continue
            plant = room["kind"] == "plant_room"
            if (sp.ObjectType == "PLANT ROOM") != plant:
                problems.append(f"{room['name']}: plant flag")
            qto = next((p for r in (sp.IsDefinedBy or []) if r.is_a("IfcRelDefinesByProperties") and r.RelatingPropertyDefinition.is_a("IfcElementQuantity")
                        for p in [r.RelatingPropertyDefinition]), None)
            area = next((q.AreaValue for q in (qto.Quantities if qto else []) if q.Name == "NetFloorArea"), None)
            want_area = se.geo.area_m2([tuple(p) for p in room["outline"]])
            if area is None or not se._area_ok(float(area), want_area):
                problems.append(f"{room['name']}: area {area} vs {want_area:.4f}")
    check("ifc_spaces", not problems, "every room on its own storey as specified", problems[:6], TOL_MM)

    # a DXF per storey, with space-envelope's own DXF checks against that storey's card
    for i in range(len(spec["storeys"])):
        p = dxfs.get(want_dxf[i])
        sub = _storey_spec(spec, i)
        ctx = se._Ctx(sub, None, p, None)
        failures = []
        if p is None:
            failures.append("missing file")
        else:
            for fn_name in DXF_CHECKS:
                fn = getattr(se, f"chk_{fn_name}")
                try:
                    ok_, exp, act = fn(ctx)
                except Exception as exc:  # noqa: BLE001 - any failure to measure is a failure
                    ok_, exp, act = False, None, f"{type(exc).__name__}"
                if not ok_:
                    failures.append(f"{fn_name}: expected {exp} actual {act}")
        check(f"dxf_storey_{i + 1:02d}", not failures, f"{spec['storeys'][i]['name']} plan matches its rooms", failures[:4], TOL_MM)

    if manifest is not None:
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            listed = {x["name"]: x["sha256"] for x in m["files"]}
            ok = set(listed) == {p.name for p in paths if p.name != "manifest.json"} and all(
                hashlib.sha256((manifest.parent / n).read_bytes()).hexdigest() == h for n, h in listed.items())
        except (OSError, ValueError, KeyError):
            ok = False
        check("manifest_checksums", ok, "files match the manifest", ok)
    return ValidationResult(all(c["passed"] for c in checks), checks)
