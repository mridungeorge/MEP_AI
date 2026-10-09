"""Writers for the space-envelope skill: a deterministic IFC4 file and a deterministic plan DXF from a NORMALISED spec.

Normalised spec: every room has `outline` as a counter-clockwise list of (x, y) points in mm, `kind`, optional `use` and `ceiling_void_mm`.
No network, no database, no LLM. Same spec, same bytes (on one toolchain).
"""
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))
import se_geometry as geo  # noqa: E402

IFC_TIMESTAMP = "2000-01-01T00:00:00"
ORIGINATING_SYSTEM = "MEP Co-pilot space-envelope"
GUID_NAMESPACE = uuid.UUID("6d2f7c1e-0b57-4c0e-9d0e-5b6a3b0f1a11")
LAYER_ROOM, LAYER_PLANT, LAYER_TEXT = "A-SPACE", "A-SPACE-PLANT", "A-ANNO-TEXT"
LAYERS = {LAYER_ROOM: 7, LAYER_PLANT: 1, LAYER_TEXT: 3}
APPID = "MEPSPACE"
TEXT_HEIGHT_MM = 250.0
PSET_ENVELOPE = "MEP_SpaceEnvelope"


def room_guid(mark: str, name: str) -> str:
    """Stable per (mark, room name): the same room keeps its GlobalId when the drawing is re-issued with other rooms added."""
    import ifcopenshell.guid

    return str(ifcopenshell.guid.compress(uuid.uuid5(GUID_NAMESPACE, f"{mark}|space|{name.strip().lower()}").hex))


def _guid(mark: str, kind: str, ordinal: int) -> str:
    import ifcopenshell.guid

    return str(ifcopenshell.guid.compress(uuid.uuid5(GUID_NAMESPACE, f"{mark}|{kind}|{ordinal}").hex))


def build_ifc(spec: dict[str, Any], path: Path) -> None:
    import ifcopenshell
    import ifcopenshell.api

    mark, storey_spec = spec["mark"], spec["storey"]
    f = ifcopenshell.file(schema="IFC4")
    f.header.file_description.description = ("ViewDefinition [ReferenceView_V1.2]",)
    hdr = f.header.file_name
    hdr.name = f"{mark}.ifc"
    hdr.time_stamp = IFC_TIMESTAMP
    hdr.author = ("",)
    hdr.organization = ("",)
    hdr.preprocessor_version = "ifcopenshell"
    hdr.originating_system = ORIGINATING_SYSTEM
    hdr.authorization = ""

    def pt(x: float, y: float, z: float | None = None) -> Any:
        return f.createIfcCartesianPoint((float(x), float(y)) if z is None else (float(x), float(y), float(z)))

    def axis(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> Any:
        return f.createIfcAxis2Placement3D(pt(x, y, z), f.createIfcDirection((0.0, 0.0, 1.0)), f.createIfcDirection((1.0, 0.0, 0.0)))

    length = f.createIfcSIUnit(UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE")
    area = f.createIfcSIUnit(UnitType="AREAUNIT", Name="SQUARE_METRE")
    volume = f.createIfcSIUnit(UnitType="VOLUMEUNIT", Name="CUBIC_METRE")
    ctx = f.createIfcGeometricRepresentationContext(None, "Model", 3, 1e-5, axis(), None)
    body = f.createIfcGeometricRepresentationSubContext("Body", "Model", None, None, None, None, ctx, None, "MODEL_VIEW", None)
    project = f.createIfcProject(_guid(mark, "project", 0), None, mark, None, None, None, None, [ctx], f.createIfcUnitAssignment([length, area, volume]))
    site = f.createIfcSite(_guid(mark, "site", 0), None, "Site", None, None, f.createIfcLocalPlacement(None, axis()), None, None, "ELEMENT")
    building = f.createIfcBuilding(_guid(mark, "building", 0), None, "Building", None, None, f.createIfcLocalPlacement(site.ObjectPlacement, axis()),
                                   None, None, "ELEMENT")
    elevation = float(storey_spec.get("elevation_mm", 0.0))
    storey = f.createIfcBuildingStorey(_guid(mark, "storey", 0), None, storey_spec["name"], None, None,
                                       f.createIfcLocalPlacement(building.ObjectPlacement, axis(0, 0, elevation)), None, None, "ELEMENT", elevation)
    f.createIfcRelAggregates(_guid(mark, "rel", 0), None, None, None, project, [site])
    f.createIfcRelAggregates(_guid(mark, "rel", 1), None, None, None, site, [building])
    f.createIfcRelAggregates(_guid(mark, "rel", 2), None, None, None, building, [storey])

    spaces = []
    for i, room in enumerate(spec["rooms"]):
        outline = [tuple(p) for p in room["outline"]]
        polyline = f.createIfcPolyline([pt(x, y) for x, y in [*outline, outline[0]]])
        profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
        solid = f.createIfcExtrudedAreaSolid(profile, axis(), f.createIfcDirection((0.0, 0.0, 1.0)), float(room["height_mm"]))
        shape = f.createIfcShapeRepresentation(body, "Body", "SweptSolid", [solid])
        plant = room["kind"] == "plant_room"
        space = f.createIfcSpace(room_guid(mark, room["name"]), None, room["name"], None, "PLANT ROOM" if plant else None,
                                 f.createIfcLocalPlacement(storey.ObjectPlacement, axis()), f.createIfcProductDefinitionShape(None, None, [shape]),
                                 room["name"], "ELEMENT", "USERDEFINED" if plant else "INTERNAL", elevation)
        spaces.append(space)
        a_m2 = geo.area_m2(outline)
        common = {"OccupancyType": room.get("use") or ("Plant" if plant else "Room")}
        if plant:
            common["Category"] = "Plant room"
        ifcopenshell.api.run("pset.edit_pset", f, pset=ifcopenshell.api.run("pset.add_pset", f, product=space, name="Pset_SpaceCommon"),
                             properties=common)
        ifcopenshell.api.run("pset.edit_qto", f, qto=ifcopenshell.api.run("pset.add_qto", f, product=space, name="Qto_SpaceBaseQuantities"),
                             properties={"NetFloorArea": round(a_m2, 6), "GrossFloorArea": round(a_m2, 6), "Height": float(room["height_mm"])})
        envelope = {"ClearHeightMm": float(room["height_mm"]), "PlantRoom": plant}
        if room.get("ceiling_void_mm") is not None:
            envelope["CeilingVoidMm"] = float(room["ceiling_void_mm"])
        ifcopenshell.api.run("pset.edit_pset", f, pset=ifcopenshell.api.run("pset.add_pset", f, product=space, name=PSET_ENVELOPE), properties=envelope)
    f.createIfcRelAggregates(_guid(mark, "rel", 3), None, None, None, storey, spaces)

    # every other rooted entity (property sets, relationships) gets a GlobalId derived from the file order, so the bytes are repeatable
    n = 0
    for e in f:
        if e.is_a("IfcRoot") and not e.is_a("IfcSpace") and not str(e.GlobalId or "").startswith("~"):
            if e.is_a() in ("IfcPropertySet", "IfcElementQuantity", "IfcRelDefinesByProperties"):
                e.GlobalId = _guid(mark, e.is_a(), n)
                n += 1
    f.write(str(path))
    _strip_ifc_clock(path)


def _strip_ifc_clock(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8")


def build_dxf(spec: dict[str, Any], path: Path) -> None:
    import ezdxf

    repo_root = str(SKILL_DIR.parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from skills.cad import cadkit

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    for name, colour in LAYERS.items():
        doc.layers.add(name, color=colour)
    doc.appids.add(APPID)
    msp = doc.modelspace()
    for room in spec["rooms"]:
        outline = [(cadkit.r6(x), cadkit.r6(y)) for x, y in room["outline"]]
        layer = LAYER_PLANT if room["kind"] == "plant_room" else LAYER_ROOM
        pl = msp.add_lwpolyline(outline, close=True, dxfattribs={"layer": layer})
        pl.set_xdata(APPID, [(1000, room["name"])])
        at = geo.label_point(room["outline"])
        msp.add_text(room["name"], dxfattribs={"layer": LAYER_TEXT, "insert": (at[0], at[1], 0.0), "height": TEXT_HEIGHT_MM})
    cadkit.save_dxf(doc, path)


def spec_sha256(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
