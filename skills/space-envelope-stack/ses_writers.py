"""Writers for space-envelope-stack: ONE IFC4 file holding several storeys, built from a NORMALISED multi-storey spec, and a plan DXF per storey (made by the
single-storey space-envelope writer). Same conventions as space-envelope: millimetres, deterministic GlobalIds, no compliance values.
"""
import sys
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent
SE_DIR = SKILL_DIR.parent / "space-envelope"
for p in (str(SE_DIR), str(SKILL_DIR.parents[1])):
    if p not in sys.path:
        sys.path.insert(0, p)

import se_geometry as geo
import se_writers as se

PSET_ENVELOPE = se.PSET_ENVELOPE


def storey_mark(mark: str, index: int) -> str:
    return f"{mark}-S{index + 1:02d}"


def storey_spec(spec: dict[str, Any], index: int) -> dict[str, Any]:
    """The single-storey card for storey `index` (what space-envelope's writer and checks take)."""
    s = spec["storeys"][index]
    return {"spec_version": "1", "mark": storey_mark(spec["mark"], index), "units": "mm",
            "storey": {"name": s["name"], "elevation_mm": s["elevation_mm"], "floor_to_floor_mm": s["floor_to_floor_mm"]}, "rooms": s["rooms"]}


def build_ifc(spec: dict[str, Any], path: Path) -> None:
    import ifcopenshell
    import ifcopenshell.api

    mark = spec["mark"]
    f = ifcopenshell.file(schema="IFC4")
    f.header.file_description.description = ("ViewDefinition [ReferenceView_V1.2]",)
    hdr = f.header.file_name
    hdr.name = f"{mark}.ifc"
    hdr.time_stamp = se.IFC_TIMESTAMP
    hdr.author = ("",)
    hdr.organization = ("",)
    hdr.preprocessor_version = "ifcopenshell"
    hdr.originating_system = "MEP Co-pilot space-envelope-stack"
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
    project = f.createIfcProject(se._guid(mark, "project", 0), None, mark, None, None, None, None, [ctx], f.createIfcUnitAssignment([length, area, volume]))
    site = f.createIfcSite(se._guid(mark, "site", 0), None, "Site", None, None, f.createIfcLocalPlacement(None, axis()), None, None, "ELEMENT")
    building = f.createIfcBuilding(se._guid(mark, "building", 0), None, "Building", None, None, f.createIfcLocalPlacement(site.ObjectPlacement, axis()), None, None, "ELEMENT")
    f.createIfcRelAggregates(se._guid(mark, "rel", 0), None, None, None, project, [site])
    f.createIfcRelAggregates(se._guid(mark, "rel", 1), None, None, None, site, [building])
    storeys = []
    for si, st in enumerate(spec["storeys"]):
        elevation = float(st["elevation_mm"])
        storeys.append((st, f.createIfcBuildingStorey(se._guid(mark, "storey", si), None, st["name"], None, None,
                                                      f.createIfcLocalPlacement(building.ObjectPlacement, axis(0, 0, elevation)), None, None, "ELEMENT", elevation)))
    f.createIfcRelAggregates(se._guid(mark, "rel", 2), None, None, None, building, [s for _, s in storeys])
    for si, (st, storey) in enumerate(storeys):
        spaces = []
        for room in st["rooms"]:
            outline = [tuple(p) for p in room["outline"]]
            polyline = f.createIfcPolyline([pt(x, y) for x, y in [*outline, outline[0]]])
            profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
            solid = f.createIfcExtrudedAreaSolid(profile, axis(), f.createIfcDirection((0.0, 0.0, 1.0)), float(room["height_mm"]))
            shape = f.createIfcShapeRepresentation(body, "Body", "SweptSolid", [solid])
            plant = room["kind"] == "plant_room"
            space = f.createIfcSpace(se.room_guid(mark, room["name"]), None, room["name"], None, "PLANT ROOM" if plant else None,
                                     f.createIfcLocalPlacement(storey.ObjectPlacement, axis()), f.createIfcProductDefinitionShape(None, None, [shape]),
                                     room["name"], "ELEMENT", "USERDEFINED" if plant else "INTERNAL", float(st["elevation_mm"]))
            spaces.append(space)
            a_m2 = geo.area_m2(outline)
            common = {"OccupancyType": room.get("use") or ("Plant" if plant else "Room")}
            if plant:
                common["Category"] = "Plant room"
            ifcopenshell.api.run("pset.edit_pset", f, pset=ifcopenshell.api.run("pset.add_pset", f, product=space, name="Pset_SpaceCommon"), properties=common)
            ifcopenshell.api.run("pset.edit_qto", f, qto=ifcopenshell.api.run("pset.add_qto", f, product=space, name="Qto_SpaceBaseQuantities"),
                                 properties={"NetFloorArea": round(a_m2, 6), "GrossFloorArea": round(a_m2, 6), "Height": float(room["height_mm"])})
            envelope = {"ClearHeightMm": float(room["height_mm"]), "PlantRoom": plant}
            if room.get("ceiling_void_mm") is not None:
                envelope["CeilingVoidMm"] = float(room["ceiling_void_mm"])
            ifcopenshell.api.run("pset.edit_pset", f, pset=ifcopenshell.api.run("pset.add_pset", f, product=space, name=PSET_ENVELOPE), properties=envelope)
        f.createIfcRelAggregates(se._guid(mark, "rel", 3 + si), None, None, None, storey, spaces)
    n = 0
    for e in f:
        if e.is_a() in ("IfcPropertySet", "IfcElementQuantity", "IfcRelDefinesByProperties"):
            e.GlobalId = se._guid(mark, e.is_a(), n)
            n += 1
    f.write(str(path))
    se._strip_ifc_clock(path)


def build_dxf(spec: dict[str, Any], index: int, path: Path) -> None:
    se.build_dxf(storey_spec(spec, index), path)
