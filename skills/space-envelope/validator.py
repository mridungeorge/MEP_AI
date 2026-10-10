"""Independent validator for space-envelope output: re-reads the IFC and the DXF from disk and compares them with the spec.

`validate(spec, files)` takes the NORMALISED spec (the `inputs` of manifest.json) and the produced files (.ifc, .dxf, optionally manifest.json)
and returns a ValidationResult. Nothing is trusted from the builder: every number is measured from the files themselves.
Tolerance: 0.5 mm on coordinates, 0.1 % on areas.
"""
import hashlib
import json
import math
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))
import se_geometry as geo

TOL_MM = 0.5
AREA_REL_TOL = 1e-3
GUID_NAMESPACE = uuid.UUID("6d2f7c1e-0b57-4c0e-9d0e-5b6a3b0f1a11")
APPID = "MEPSPACE"
LAYERS = {"A-SPACE": 7, "A-SPACE-PLANT": 1, "A-ANNO-TEXT": 3}
STANDARD_BLOCKS = {"_ARCHTICK", "_CLOSEDFILLED", "_CLOSEDBLANK"}      # the dimension arrow heads ezdxf writes with its standard setup
ALLOWED_EXTRA_LAYERS = {"0", "DEFPOINTS"}
Pt = tuple[float, float]
Outcome = tuple[bool, Any, Any]


@dataclass
class ValidationResult:
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def failed(self) -> list[str]:
        return [c["name"] for c in self.checks if not c["passed"]]


def _expected_guid(mark: str, name: str) -> str:
    import ifcopenshell.guid

    return str(ifcopenshell.guid.compress(uuid.uuid5(GUID_NAMESPACE, f"{mark}|space|{name.strip().lower()}").hex))


def _same_outline(a: list[Pt], b: list[Pt], tol: float = TOL_MM) -> bool:
    a, b = geo.normalise_outline(list(a)), geo.normalise_outline(list(b))
    return len(a) == len(b) and all(math.dist(p, q) <= tol for p, q in zip(a, b, strict=True))


def _area_ok(measured_m2: float, expected_m2: float) -> bool:
    return abs(measured_m2 - expected_m2) <= AREA_REL_TOL * max(expected_m2, 1e-9)


class _Ctx:
    def __init__(self, spec: dict[str, Any], ifc: Path | None, dxf: Path | None, manifest: Path | None) -> None:
        self.spec, self.ifc_path, self.dxf_path, self.manifest_path = spec, ifc, dxf, manifest
        self.rooms: list[dict[str, Any]] = spec.get("rooms", []) if isinstance(spec, dict) else []
        self._ifc: Any = None
        self._dxf: Any = None

    @property
    def ifc(self) -> Any:
        if self._ifc is None:
            import ifcopenshell

            self._ifc = ifcopenshell.open(str(self.ifc_path))
        return self._ifc

    @property
    def dxf(self) -> Any:
        if self._dxf is None:
            import ezdxf

            self._dxf = ezdxf.readfile(str(self.dxf_path))
        return self._dxf

    def ifc_spaces(self) -> dict[str, Any]:
        return {str(s.Name): s for s in self.ifc.by_type("IfcSpace")}

    def room_outline(self, room: dict[str, Any]) -> list[Pt]:
        return [(float(x), float(y)) for x, y in room["outline"]]

    def ifc_profile(self, space: Any) -> tuple[list[Pt], float]:
        rep = space.Representation.Representations[0]
        solid = rep.Items[0]
        if not solid.is_a("IfcExtrudedAreaSolid"):
            raise ValueError("the space is not an extruded area solid")
        curve = solid.SweptArea.OuterCurve
        pts = [(float(p.Coordinates[0]), float(p.Coordinates[1])) for p in curve.Points]
        if pts[0] == pts[-1]:
            pts = pts[:-1]
        return pts, float(solid.Depth)

    def dxf_rooms(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for e in self.dxf.modelspace().query("LWPOLYLINE"):
            xd = e.get_xdata(APPID) if e.has_xdata(APPID) else None
            name = str(xd[0].value) if xd else ""
            out[name] = e
        return out


def _props(space: Any, pset: str) -> dict[str, Any]:
    import ifcopenshell.util.element as util

    return dict(util.get_psets(space).get(pset) or {})


# ------------------------------------------------------------------------------------------------ the checks
def chk_input_files(c: _Ctx) -> Outcome:
    return (c.ifc_path is not None and c.dxf_path is not None, "one .ifc and one .dxf", f"ifc={c.ifc_path is not None} dxf={c.dxf_path is not None}")


def chk_spec_readable(c: _Ctx) -> Outcome:
    ok = isinstance(c.spec, dict) and bool(c.rooms) and all(k in c.spec for k in ("mark", "storey", "rooms"))
    return ok, "mark, storey and at least one room", "ok" if ok else "missing"


def chk_ifc_loads(c: _Ctx) -> Outcome:
    n = len(list(c.ifc.by_type("IfcRoot")))
    return n > 0, "a readable IFC with entities", n


def chk_ifc_schema(c: _Ctx) -> Outcome:
    return str(c.ifc.schema) == "IFC4", "IFC4", str(c.ifc.schema)


def chk_ifc_units(c: _Ctx) -> Outcome:
    units = c.ifc.by_type("IfcProject")[0].UnitsInContext.Units
    got = {u.UnitType: (getattr(u, "Prefix", None), getattr(u, "Name", None)) for u in units if u.is_a("IfcSIUnit")}
    want = {"LENGTHUNIT": ("MILLI", "METRE"), "AREAUNIT": (None, "SQUARE_METRE")}
    return all(got.get(k) == v for k, v in want.items()), want, got


def chk_ifc_project(c: _Ctx) -> Outcome:
    projects = c.ifc.by_type("IfcProject")
    got = (len(projects), projects[0].Name if projects else None, len(c.ifc.by_type("IfcBuildingStorey")))
    return got == (1, c.spec["mark"], 1), (1, c.spec["mark"], 1), got


def chk_ifc_storey(c: _Ctx) -> Outcome:
    st = c.ifc.by_type("IfcBuildingStorey")[0]
    want = (c.spec["storey"]["name"], float(c.spec["storey"].get("elevation_mm", 0)))
    got = (st.Name, float(st.Elevation or 0))
    return got[0] == want[0] and abs(got[1] - want[1]) <= TOL_MM, want, got


def _axis_ok(placement: Any, xyz: tuple[float, float, float]) -> bool:
    """A plain IfcAxis2Placement3D at xyz with the global axes (no rotation)."""
    if placement is None or not placement.is_a("IfcAxis2Placement3D"):
        return False
    loc = tuple(float(v) for v in placement.Location.Coordinates)
    z = tuple(float(v) for v in placement.Axis.DirectionRatios) if placement.Axis else (0.0, 0.0, 1.0)
    x = tuple(float(v) for v in placement.RefDirection.DirectionRatios) if placement.RefDirection else (1.0, 0.0, 0.0)
    return len(loc) == 3 and all(abs(a - b) <= TOL_MM for a, b in zip(loc, xyz, strict=True)) and z == (0.0, 0.0, 1.0) and x == (1.0, 0.0, 0.0)


def _placement_ok(element: Any, parent: Any, xyz: tuple[float, float, float]) -> bool:
    pl = element.ObjectPlacement
    if pl is None or not pl.is_a("IfcLocalPlacement"):
        return False
    return (pl.PlacementRelTo == (parent.ObjectPlacement if parent is not None else None)) and _axis_ok(pl.RelativePlacement, xyz)


def chk_ifc_structure(c: _Ctx) -> Outcome:
    """The file contains exactly the project/site/building/storey/spaces the spec asks for, every one placed at its stated place with no rotation
    or offset, one solid per space extruded straight up: so the footprints measured from the profiles ARE the footprints in the world."""
    f, bad = c.ifc, []
    kinds = {"IfcProject": 1, "IfcSite": 1, "IfcBuilding": 1, "IfcBuildingStorey": 1, "IfcSpace": len(c.rooms)}
    for k, n in kinds.items():
        if len(f.by_type(k, include_subtypes=False)) != n:
            bad.append(f"{k} count")
    if len(f.by_type("IfcProduct")) != 3 + len(c.rooms):
        bad.append("other products present")
    if f.by_type("IfcRelContainedInSpatialStructure"):
        bad.append("spaces contained as well as aggregated")
    ids = [r.GlobalId for r in f.by_type("IfcRoot")]
    if len(ids) != len(set(ids)):
        bad.append("GlobalIds not unique")
    if len([u for u in f.by_type("IfcNamedUnit") if getattr(u, "UnitType", None) == "LENGTHUNIT"]) != 1 or f.by_type("IfcConversionBasedUnit"):
        bad.append("length unit")
    site, building, storey = f.by_type("IfcSite")[0], f.by_type("IfcBuilding")[0], f.by_type("IfcBuildingStorey")[0]
    project = f.by_type("IfcProject")[0]
    for parent, child in ((project, site), (site, building), (building, storey)):               # the spatial chain, each link exactly once
        rels = [r for r in f.by_type("IfcRelAggregates") if r.RelatingObject == parent]
        if len(rels) != 1 or list(rels[0].RelatedObjects) != [child] or len(child.Decomposes) != 1:
            bad.append("spatial chain")
    srel = [r for r in f.by_type("IfcRelAggregates") if r.RelatingObject == storey]
    if len(srel) != 1 or len(f.by_type("IfcRelAggregates")) != 4 or len(srel[0].RelatedObjects) != len(c.rooms):
        bad.append("storey aggregation")
    if f.by_type("IfcMapConversion") or f.by_type("IfcCoordinateReferenceSystem") or f.by_type("IfcProjectedCRS"):
        bad.append("map conversion present")
    roots = [x for x in f.by_type("IfcGeometricRepresentationContext") if not x.is_a("IfcGeometricRepresentationSubContext")]
    subs = f.by_type("IfcGeometricRepresentationSubContext")
    if len(roots) != 1 or len(subs) != 1 or len(project.RepresentationContexts) != 1 or project.RepresentationContexts[0] != roots[0] \
            or subs[0].ParentContext != roots[0] or roots[0].TrueNorth is not None or not _axis_ok(roots[0].WorldCoordinateSystem, (0.0, 0.0, 0.0)):
        bad.append("representation context")
    elevation = float(c.spec["storey"].get("elevation_mm", 0))
    if not (_placement_ok(site, None, (0.0, 0.0, 0.0)) and _placement_ok(building, site, (0.0, 0.0, 0.0))
            and _placement_ok(storey, building, (0.0, 0.0, elevation))):
        bad.append("site/building/storey placement")
    for sp in f.by_type("IfcSpace"):
        name = str(sp.Name)
        if not _placement_ok(sp, storey, (0.0, 0.0, 0.0)):
            bad.append(f"{name}: placement")
        reps = sp.Representation.Representations if sp.Representation else []
        if len(sp.Representation.Representations if sp.Representation else []) != 1 or len(reps[0].Items) != 1:
            bad.append(f"{name}: representation")
            continue
        solid = reps[0].Items[0]
        if reps[0].ContextOfItems != subs[0]:
            bad.append(f"{name}: representation context")
        if not (reps[0].RepresentationIdentifier == "Body" and solid.is_a("IfcExtrudedAreaSolid") and _axis_ok(solid.Position, (0.0, 0.0, 0.0))
                and tuple(float(v) for v in solid.ExtrudedDirection.DirectionRatios) == (0.0, 0.0, 1.0)
                and solid.SweptArea.is_a() == "IfcArbitraryClosedProfileDef" and solid.SweptArea.ProfileType == "AREA"
                and solid.SweptArea.OuterCurve.is_a("IfcPolyline")
                and all(len(p.Coordinates) == 2 for p in solid.SweptArea.OuterCurve.Points)):
            bad.append(f"{name}: solid")
    return not bad, "exactly the spec's project, site, building, storey and spaces, unrotated, unshifted, one straight extrusion each", bad or "ok"


def chk_ifc_space_count(c: _Ctx) -> Outcome:
    n = len(c.ifc.by_type("IfcSpace"))
    return n == len(c.rooms), len(c.rooms), n


def chk_ifc_space_names(c: _Ctx) -> Outcome:
    got = sorted(str(s.Name) for s in c.ifc.by_type("IfcSpace"))
    want = sorted(r["name"] for r in c.rooms)
    long_ok = all(str(s.LongName) == str(s.Name) for s in c.ifc.by_type("IfcSpace"))
    return got == want and long_ok, want, got


def chk_ifc_space_guids(c: _Ctx) -> Outcome:
    spaces = c.ifc_spaces()
    bad = [r["name"] for r in c.rooms if r["name"] not in spaces or spaces[r["name"]].GlobalId != _expected_guid(c.spec["mark"], r["name"])]
    return not bad, "GlobalId derived from the mark and the room name", bad or "ok"


def chk_ifc_aggregation(c: _Ctx) -> Outcome:
    import ifcopenshell.util.element as util

    storey = c.ifc.by_type("IfcBuildingStorey")[0]
    bad = [str(s.Name) for s in c.ifc.by_type("IfcSpace") if util.get_aggregate(s) != storey]
    return not bad, "every space aggregated under the storey", bad or "ok"


def chk_ifc_footprints(c: _Ctx) -> Outcome:
    spaces, bad = c.ifc_spaces(), []
    for r in c.rooms:
        s = spaces.get(r["name"])
        if s is None:
            bad.append(r["name"])
            continue
        pts, depth = c.ifc_profile(s)
        if not _same_outline(pts, c.room_outline(r)) or abs(depth - r["height_mm"]) > TOL_MM:
            bad.append(r["name"])
    return not bad, "extruded outline and depth equal the spec", bad or "ok"


def chk_ifc_area_quantity(c: _Ctx) -> Outcome:
    spaces, bad = c.ifc_spaces(), []
    for r in c.rooms:
        q = _props(spaces[r["name"]], "Qto_SpaceBaseQuantities")
        want = geo.area_m2(c.room_outline(r))
        if not (_area_ok(float(q.get("NetFloorArea", -1)), want) and _area_ok(float(q.get("GrossFloorArea", -1)), want)):
            bad.append(r["name"])
    return not bad, "NetFloorArea and GrossFloorArea equal the outline area (0.1 %)", bad or "ok"


def chk_ifc_geometry_area(c: _Ctx) -> Outcome:
    import ifcopenshell.geom
    import ifcopenshell.util.shape

    settings, spaces, bad = ifcopenshell.geom.settings(), c.ifc_spaces(), []
    for r in c.rooms:
        shape = ifcopenshell.geom.create_shape(settings, spaces[r["name"]])
        footprint = float(ifcopenshell.util.shape.get_footprint_area(shape.geometry))     # the kernel's own area, in m2
        if not _area_ok(footprint, geo.area_m2(c.room_outline(r))):
            bad.append(r["name"])
    return not bad, "the geometry kernel's footprint area equals the outline area", bad or "ok"


def chk_ifc_heights(c: _Ctx) -> Outcome:
    spaces, bad = c.ifc_spaces(), []
    for r in c.rooms:
        s = spaces[r["name"]]
        q, e = _props(s, "Qto_SpaceBaseQuantities"), _props(s, "MEP_SpaceEnvelope")
        if abs(float(q.get("Height", -1)) - r["height_mm"]) > TOL_MM or abs(float(e.get("ClearHeightMm", -1)) - r["height_mm"]) > TOL_MM:
            bad.append(r["name"])
    return not bad, "Height quantity and ClearHeightMm equal the spec", bad or "ok"


def chk_ifc_plant_flag(c: _Ctx) -> Outcome:
    spaces, bad = c.ifc_spaces(), []
    for r in c.rooms:
        s, plant = spaces[r["name"]], r["kind"] == "plant_room"
        flag = bool(_props(s, "MEP_SpaceEnvelope").get("PlantRoom"))
        if flag != plant or (str(s.PredefinedType) == "USERDEFINED") != plant or (plant and str(s.ObjectType) != "PLANT ROOM"):
            bad.append(r["name"])
    return not bad, "plant rooms (and only they) are marked as plant rooms", bad or "ok"


def chk_ifc_use(c: _Ctx) -> Outcome:
    spaces, bad = c.ifc_spaces(), []
    for r in c.rooms:
        want = r.get("use") or ("Plant" if r["kind"] == "plant_room" else "Room")
        if str(_props(spaces[r["name"]], "Pset_SpaceCommon").get("OccupancyType")) != want:
            bad.append(r["name"])
    return not bad, "OccupancyType equals the use (or Room / Plant)", bad or "ok"


def chk_ifc_ceiling_void(c: _Ctx) -> Outcome:
    spaces, bad = c.ifc_spaces(), []
    for r in c.rooms:
        got = _props(spaces[r["name"]], "MEP_SpaceEnvelope").get("CeilingVoidMm")
        want = r.get("ceiling_void_mm")
        if (want is None) != (got is None) or (want is not None and abs(float(got) - want) > TOL_MM):
            bad.append(r["name"])
    return not bad, "CeilingVoidMm present exactly when the spec has one, and equal", bad or "ok"


def chk_dxf_loads(c: _Ctx) -> Outcome:
    return len(c.dxf.modelspace()) > 0, "a readable DXF with entities", len(c.dxf.modelspace())


def chk_dxf_units(c: _Ctx) -> Outcome:
    got = (int(c.dxf.header.get("$INSUNITS", 0)), int(c.dxf.header.get("$MEASUREMENT", 0)))
    return got == (4, 1), (4, 1), got


def chk_dxf_layers(c: _Ctx) -> Outcome:
    table = {layer.dxf.name: layer.dxf.color for layer in c.dxf.layers}
    extra = {n for n in table if n not in LAYERS and n.upper() not in ALLOWED_EXTRA_LAYERS}
    wrong = [n for n, col in LAYERS.items() if table.get(n) != col]
    return not extra and not wrong, LAYERS, {"extra": sorted(extra), "wrong_or_missing": wrong}


def chk_dxf_entities(c: _Ctx) -> Outcome:
    bad: list[str] = []
    for e in c.dxf.modelspace():
        t, layer = e.dxftype(), e.dxf.layer
        if t == "LWPOLYLINE" and layer in ("A-SPACE", "A-SPACE-PLANT") and e.closed and abs(float(e.dxf.elevation)) < 1e-9 \
                and not any(abs(b) > 1e-12 for *_, b in e.get_points("xyb")) \
                and not any(abs(a) > 1e-12 or abs(b) > 1e-12 for *_, a, b, _bulge in e.get_points("xyseb")) \
                and abs(float(e.dxf.const_width)) < 1e-12 and abs(float(e.dxf.thickness)) < 1e-12 \
                and tuple(float(v) for v in e.dxf.extrusion) == (0.0, 0.0, 1.0):
            continue
        if t == "TEXT" and layer == "A-ANNO-TEXT" and abs(float(e.dxf.insert.z)) < 1e-9 and int(e.dxf.halign) == 0 and int(e.dxf.valign) == 0 \
                and abs(float(e.dxf.rotation)) < 1e-12 and abs(float(e.dxf.height) - 250.0) < 1e-9 and abs(float(e.dxf.thickness)) < 1e-12 \
                and tuple(float(v) for v in e.dxf.extrusion) == (0.0, 0.0, 1.0):
            continue
        bad.append(f"{t} on {layer}")
    paper = [e.dxftype() for lay in c.dxf.layouts if lay.name != "Model" for e in lay if lay.name != "Model"]
    paper += [f"block {b.name}" for b in c.dxf.blocks if b.name not in STANDARD_BLOCKS and not b.name.startswith(("*Model_Space", "*Paper_Space"))]
    return not bad and not paper, "only closed straight polylines on the room layers and text on A-ANNO-TEXT, all at z = 0", (bad + paper) or "ok"


def chk_dxf_room_count(c: _Ctx) -> Outcome:
    n = len(c.dxf.modelspace().query("LWPOLYLINE"))
    return n == len(c.rooms), len(c.rooms), n


def chk_dxf_outlines(c: _Ctx) -> Outcome:
    polys, bad = c.dxf_rooms(), []
    for r in c.rooms:
        e = polys.get(r["name"])
        want_layer = "A-SPACE-PLANT" if r["kind"] == "plant_room" else "A-SPACE"
        if e is None or e.dxf.layer != want_layer or not _same_outline([(float(x), float(y)) for x, y, *_ in e.get_points("xy")], c.room_outline(r)):
            bad.append(r["name"])
    return not bad, "each room's polyline equals its outline, on the layer for its kind", bad or "ok"


def chk_dxf_areas(c: _Ctx) -> Outcome:
    polys, bad = c.dxf_rooms(), []
    for r in c.rooms:
        e = polys[r["name"]]
        pts = [(float(x), float(y)) for x, y, *_ in e.get_points("xy")]
        if not _area_ok(geo.area_m2(pts), geo.area_m2(c.room_outline(r))):
            bad.append(r["name"])
    return not bad, "polyline areas equal the outline areas (0.1 %)", bad or "ok"


def chk_dxf_labels(c: _Ctx) -> Outcome:
    texts = list(c.dxf.modelspace().query("TEXT"))
    bad: list[str] = []
    for r in c.rooms:
        mine = [t for t in texts if " ".join(str(t.dxf.text).split()) == " ".join(r["name"].split())]
        if len(mine) != 1:
            bad.append(f"{r['name']}: {len(mine)} labels")
            continue
        p = (float(mine[0].dxf.insert.x), float(mine[0].dxf.insert.y))
        if not geo.point_in_polygon(p, c.room_outline(r), strict=True) or float(mine[0].dxf.height) < 1.0:
            bad.append(f"{r['name']}: label not inside its room")
        for other in c.rooms:
            if other is not r and geo.point_in_polygon(p, c.room_outline(other), strict=True):
                bad.append(f"{r['name']}: label inside {other['name']}")
    extra = len(texts) - len(c.rooms)
    if extra:
        bad.append(f"{extra} unexpected text entities")
    return not bad, "one label per room, named, inside its own room", bad or "ok"


def chk_cross_ifc_dxf(c: _Ctx) -> Outcome:
    spaces, polys, bad = c.ifc_spaces(), c.dxf_rooms(), []
    for r in c.rooms:
        ifc_pts, _ = c.ifc_profile(spaces[r["name"]])
        dxf_pts = [(float(x), float(y)) for x, y, *_ in polys[r["name"]].get_points("xy")]
        if not _same_outline(ifc_pts, dxf_pts):
            bad.append(r["name"])
    return not bad, "the IFC outline and the DXF outline of each room are the same", bad or "ok"


def chk_rooms_no_overlap(c: _Ctx) -> Outcome:
    spaces = c.ifc_spaces()
    outlines = {r["name"]: c.ifc_profile(spaces[r["name"]])[0] for r in c.rooms}
    names = list(outlines)
    bad = [f"{a} / {b}" for i, a in enumerate(names) for b in names[i + 1:] if geo.interiors_overlap(outlines[a], outlines[b])]
    return not bad, "no two rooms overlap (measured from the IFC)", bad or "ok"


def chk_manifest(c: _Ctx) -> Outcome:
    if c.manifest_path is None:
        return True, "no manifest supplied", "skipped"
    m = json.loads(c.manifest_path.read_text(encoding="utf-8"))
    bad = []
    for entry in m.get("files", []):
        p = next((x for x in (c.ifc_path, c.dxf_path) if x is not None and x.name == entry["name"]), None)
        if p is None or hashlib.sha256(p.read_bytes()).hexdigest() != entry["sha256"]:
            bad.append(entry["name"])
    return not bad, "file checksums equal the manifest", bad or "ok"


CHECKS: list[tuple[str, Any, Callable[[_Ctx], Outcome]]] = [
    ("input_files", "-", chk_input_files), ("spec_readable", "-", chk_spec_readable),
    ("ifc_loads", "-", chk_ifc_loads), ("ifc_schema", "-", chk_ifc_schema), ("ifc_units", "-", chk_ifc_units),
    ("ifc_project", "-", chk_ifc_project), ("ifc_storey", TOL_MM, chk_ifc_storey), ("ifc_structure", "-", chk_ifc_structure), ("ifc_space_count", "-", chk_ifc_space_count),
    ("ifc_space_names", "-", chk_ifc_space_names), ("ifc_space_guids", "-", chk_ifc_space_guids),
    ("ifc_aggregation", "-", chk_ifc_aggregation), ("ifc_footprints", TOL_MM, chk_ifc_footprints),
    ("ifc_area_quantity", AREA_REL_TOL, chk_ifc_area_quantity), ("ifc_geometry_area", AREA_REL_TOL, chk_ifc_geometry_area),
    ("ifc_heights", TOL_MM, chk_ifc_heights), ("ifc_plant_flag", "-", chk_ifc_plant_flag), ("ifc_use", "-", chk_ifc_use),
    ("ifc_ceiling_void", TOL_MM, chk_ifc_ceiling_void),
    ("dxf_loads", "-", chk_dxf_loads), ("dxf_units", "-", chk_dxf_units), ("dxf_layers", "-", chk_dxf_layers),
    ("dxf_entities", "-", chk_dxf_entities), ("dxf_room_count", "-", chk_dxf_room_count), ("dxf_outlines", TOL_MM, chk_dxf_outlines),
    ("dxf_areas", AREA_REL_TOL, chk_dxf_areas), ("dxf_labels", "-", chk_dxf_labels),
    ("cross_ifc_dxf", TOL_MM, chk_cross_ifc_dxf), ("rooms_no_overlap", "-", chk_rooms_no_overlap), ("manifest_checksums", "-", chk_manifest),
]


def validate(spec: dict[str, Any], files: list[Path]) -> ValidationResult:
    paths = [Path(f) for f in files]
    ifcs = [p for p in paths if p.suffix.lower() == ".ifc"]
    dxfs = [p for p in paths if p.suffix.lower() == ".dxf"]
    jsons = [p for p in paths if p.suffix.lower() == ".json"]
    others = [p for p in paths if p not in ifcs + dxfs + jsons]
    ctx = _Ctx(spec if isinstance(spec, dict) else {}, ifcs[0] if len(ifcs) == 1 else None, dxfs[0] if len(dxfs) == 1 else None,
               jsons[0] if len(jsons) == 1 else None)
    checks: list[dict[str, Any]] = []
    gate_ok = not others and len(ifcs) == 1 and len(dxfs) == 1 and ctx.rooms != []
    for name, tol, fn in CHECKS:
        if not gate_ok and name not in ("input_files", "spec_readable"):
            checks.append({"name": name, "passed": False, "expected": None, "actual": "skipped: input files or spec unusable", "tolerance": tol})
            continue
        if name.startswith("ifc_") and name not in ("ifc_loads",) and not _loaded(checks, "ifc_loads"):
            checks.append({"name": name, "passed": False, "expected": None, "actual": "skipped: the IFC did not load", "tolerance": tol})
            continue
        if (name.startswith("dxf_") or name == "cross_ifc_dxf") and name != "dxf_loads" and not _loaded(checks, "dxf_loads"):
            checks.append({"name": name, "passed": False, "expected": None, "actual": "skipped: the DXF did not load", "tolerance": tol})
            continue
        try:
            ok, expected, actual = fn(ctx)
            entry = {"name": name, "passed": bool(ok), "expected": expected, "actual": actual, "tolerance": tol}
        except Exception as exc:  # noqa: BLE001 - any failure to measure is a failed check
            entry = {"name": name, "passed": False, "expected": None, "actual": f"{type(exc).__name__}: {str(exc)[:300]}", "tolerance": tol}
        checks.append(entry)
    return ValidationResult(passed=all(c["passed"] for c in checks), checks=checks)


def _loaded(checks: list[dict[str, Any]], name: str) -> bool:
    return any(c["name"] == name and c["passed"] for c in checks)
