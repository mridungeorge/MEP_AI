"""Writer for the ifc-mep skill: adds ducts, terminals and equipment (with ports, systems and connections) to a COPY of the architect's IFC.

Coordinates in the spec are millimetres relative to the named storey; they are converted to the file's own length unit. The architect's entities are not
changed. Deterministic GlobalIds for everything added (a function of the mark, class and tag). No compliance value is computed.
"""
import hashlib
import math
import uuid
from pathlib import Path
from typing import Any

GUID_NAMESPACE = uuid.UUID("8f1b2f64-3b1d-4d57-9a3e-6f0c5a1d2b77")
ORIGINATING_SYSTEM = "MEP Co-pilot ifc-mep"
PSET = "MEP_Services"
FIXED_TIME = 946684800                                    # 2000-01-01T00:00:00Z
DUCT_PORTS = ("start", "end")
SUPPORTED_SCHEMAS = ("IFC4", "IFC4X3", "IFC4X3_ADD2")
EQUIPMENT_CLASS = {"ahu": ("IfcUnitaryEquipment", "AIRHANDLER"), "fan": ("IfcFan", "CENTRIFUGALBACKWARDCURVED")}


def element_guid(mark: str, kind: str, key: str) -> str:
    import ifcopenshell.guid

    return str(ifcopenshell.guid.compress(uuid.uuid5(GUID_NAMESPACE, f"{mark}|{kind}|{key}").hex))


def unit_factor(f: Any) -> float:
    """Multiply millimetres by this to get the file's length unit."""
    import ifcopenshell.util.unit as u

    return 0.001 / float(u.calculate_unit_scale(f))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def storeys_by_name(f: Any) -> dict[str, Any]:
    out: dict[str, list[Any]] = {}
    for s in f.by_type("IfcBuildingStorey"):
        out.setdefault(s.Name or "", []).append(s)
    return {k: v[0] for k, v in out.items() if len(v) == 1}


def direction(start: list[float], end: list[float]) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
    d = [e - s for s, e in zip(start, end, strict=True)]
    length = math.sqrt(sum(c * c for c in d))
    u = (d[0] / length, d[1] / length, d[2] / length)
    if abs(u[2]) > 0.99:
        r = (1.0, 0.0, 0.0)
    else:
        r = (-u[1], u[0], 0.0)
        n = math.hypot(r[0], r[1])
        r = (r[0] / n, r[1] / n, 0.0)
    return u, r, length


def build_ifc(spec: dict[str, Any], base: Path, out: Path) -> dict[str, Any]:
    import ifcopenshell
    import ifcopenshell.api
    import ifcopenshell.util.representation as rep

    f = ifcopenshell.open(str(base))
    if f.schema not in SUPPORTED_SCHEMAS:
        raise ValueError(f"the architect's file is {f.schema}; ifc-mep writes IFC4 and IFC4X3 only")
    base_max = max(e.id() for e in f)
    original_history = {h.id(): (h.CreationDate, h.LastModifiedDate) for h in f.by_type("IfcOwnerHistory")}
    k = unit_factor(f)
    storeys = storeys_by_name(f)
    wanted = {e["storey"] for g in ("ducts", "terminals", "equipment") for e in spec.get(g, [])}
    missing = sorted(wanted - set(storeys))
    if missing:
        raise ValueError(f"storeys not found (or not unique by name) in the architect's file: {missing}")
    ctx = rep.get_context(f, "Model", "Body", "MODEL_VIEW") or next(iter(f.by_type("IfcGeometricRepresentationContext")), None)
    if ctx is None:
        raise ValueError("the architect's file has no geometric representation context")
    mark = spec["mark"]

    def pt(v: Any) -> Any:
        return f.createIfcCartesianPoint([float(c) * k for c in v])

    def place(parent: Any, origin: Any, axis: Any = (0.0, 0.0, 1.0), ref: Any = (1.0, 0.0, 0.0)) -> Any:
        return f.createIfcLocalPlacement(parent, f.createIfcAxis2Placement3D(pt(origin), f.createIfcDirection(list(axis)), f.createIfcDirection(list(ref))))

    def body(profile: Any, depth_mm: float, offset_mm: float = 0.0) -> Any:
        solid = f.createIfcExtrudedAreaSolid(profile, f.createIfcAxis2Placement3D(pt((0, 0, offset_mm))), f.createIfcDirection([0.0, 0.0, 1.0]), depth_mm * k)
        shape = f.createIfcShapeRepresentation(ctx, "Body", "SweptSolid", [solid])
        return f.createIfcProductDefinitionShape(None, None, [shape])

    def systems_for(tags: list[str]) -> dict[str, Any]:
        out_sys = {}
        for tag in sorted(set(tags)):
            s = ifcopenshell.api.run("system.add_system", f, ifc_class="IfcDistributionSystem")
            s.Name = tag
            s.GlobalId = element_guid(mark, "system", tag)
            out_sys[tag] = s
        return out_sys

    all_systems = systems_for([e["system"] for g in ("ducts", "terminals", "equipment") for e in spec.get(g, [])])
    ports: dict[str, Any] = {}
    created: list[Any] = []

    def finish(el: Any, kind: str, e: dict[str, Any], storey: Any, props: dict[str, Any]) -> None:
        el.Name = e["tag"]
        el.Tag = e["tag"]
        el.GlobalId = element_guid(mark, kind, e["tag"])
        ifcopenshell.api.run("spatial.assign_container", f, products=[el], relating_structure=storey)
        ifcopenshell.api.run("system.assign_system", f, products=[el], system=all_systems[e["system"]])
        pset = ifcopenshell.api.run("pset.add_pset", f, product=el, name=PSET)
        ifcopenshell.api.run("pset.edit_pset", f, pset=pset, properties={"System": e["system"], **props})
        created.append(el)

    def add_port(el: Any, name: str, local_origin: Any, flow: str) -> None:
        p = ifcopenshell.api.run("system.add_port", f, element=el)
        p.Name = f"{el.Name}.{name}"
        p.FlowDirection = flow
        p.GlobalId = element_guid(mark, "port", p.Name)
        p.ObjectPlacement = place(el.ObjectPlacement, local_origin)
        ports[p.Name] = p

    for d in spec["ducts"]:
        storey = storeys[d["storey"]]
        u, r, length = direction(d["start_mm"], d["end_mm"])
        el = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcDuctSegment", predefined_type="RIGIDSEGMENT")
        el.ObjectPlacement = place(storey.ObjectPlacement, [c * 1.0 for c in d["start_mm"]], u, r)
        size = d["size"]
        if size["shape"] == "rect":
            profile = f.createIfcRectangleProfileDef("AREA", None, f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([0.0, 0.0])), size["width_mm"] * k, size["depth_mm"] * k)
            props = {"AirflowLs": d["airflow_ls"], "WidthMm": size["width_mm"], "DepthMm": size["depth_mm"]}
        else:
            profile = f.createIfcCircleProfileDef("AREA", None, f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([0.0, 0.0])), size["diameter_mm"] / 2.0 * k)
            props = {"AirflowLs": d["airflow_ls"], "DiameterMm": size["diameter_mm"]}
        el.Representation = body(profile, length)
        finish(el, "duct", d, storey, props)
        add_port(el, "start", (0.0, 0.0, 0.0), "SINK")
        add_port(el, "end", (0.0, 0.0, length), "SOURCE")
    for t in spec.get("terminals", []):
        storey = storeys[t["storey"]]
        el = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcAirTerminal", predefined_type="DIFFUSER")
        el.ObjectPlacement = place(storey.ObjectPlacement, t["at_mm"])
        b = t.get("box") or {"length_mm": 300.0, "width_mm": 300.0, "height_mm": 100.0}
        profile = f.createIfcRectangleProfileDef("AREA", None, f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([0.0, 0.0])), b["length_mm"] * k, b["width_mm"] * k)
        el.Representation = body(profile, b["height_mm"])
        finish(el, "terminal", t, storey, {"AirflowLs": t["airflow_ls"]})
        add_port(el, "in", (0.0, 0.0, 0.0), "SINK")
    for q in spec.get("equipment", []):
        storey = storeys[q["storey"]]
        cls, predefined = EQUIPMENT_CLASS[q["kind"]]
        el = ifcopenshell.api.run("root.create_entity", f, ifc_class=cls, predefined_type=predefined)
        el.ObjectPlacement = place(storey.ObjectPlacement, q["at_mm"])
        b = q["box"]
        profile = f.createIfcRectangleProfileDef("AREA", None, f.createIfcAxis2Placement2D(f.createIfcCartesianPoint([0.0, 0.0])), b["length_mm"] * k, b["width_mm"] * k)
        el.Representation = body(profile, b["height_mm"])
        finish(el, "equipment", q, storey, {"Kind": q["kind"]})
        add_port(el, "in", (-b["length_mm"] / 2.0, 0.0, b["height_mm"] / 2.0), "SINK")
        add_port(el, "out", (b["length_mm"] / 2.0, 0.0, b["height_mm"] / 2.0), "SOURCE")
    for c in spec.get("connections", []):
        a, b2 = ports.get(c["from"]), ports.get(c["to"])
        if a is None or b2 is None:
            raise ValueError(f"connection {c['from']} -> {c['to']}: no such port")
        ifcopenshell.api.run("system.connect_port", f, port1=a, port2=b2, direction="SOURCE")
    # every other rooted entity we created (nests, connections, assignments, property sets) gets a GlobalId from the file order, so the bytes are repeatable
    n = 0
    for e in f:
        if e.id() > base_max and e.is_a() in ("IfcRelNests", "IfcRelConnectsPorts", "IfcRelAssignsToGroup", "IfcRelContainedInSpatialStructure", "IfcPropertySet",
                                                    "IfcRelDefinesByProperties"):
            e.GlobalId = element_guid(mark, e.is_a(), str(n))
            n += 1
    for h in f.by_type("IfcOwnerHistory"):                 # the clock must not reach the file: new histories get a fixed time, old ones keep theirs
        h.CreationDate, h.LastModifiedDate = original_history.get(h.id(), (FIXED_TIME, FIXED_TIME))
    f.write(str(out))
    return {"ducts": len(spec["ducts"]), "terminals": len(spec.get("terminals", [])), "equipment": len(spec.get("equipment", [])),
            "connections": len(spec.get("connections", [])), "added_entities": sum(1 for e in f if e.id() > base_max)}
