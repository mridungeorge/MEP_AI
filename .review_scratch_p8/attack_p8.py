"""Mutation attacks on Phase 8 validators (no DB)."""
import copy, importlib.util, json, shutil, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "apps" / "api"))

def load(name, path, extra):
    for p in extra:
        if str(p) not in sys.path: sys.path.insert(0, str(p))
    s = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(s); sys.modules[name] = m; s.loader.exec_module(m); return m

import ifcopenshell, ezdxf
tmp = Path(tempfile.mkdtemp())

IM = ROOT / "skills" / "ifc-mep"
b = load("imb", IM / "scripts" / "build.py", [IM]); v = load("imv", IM / "validator.py", [IM])
spec_in = json.loads((IM / "examples" / "ahu_to_terminal" / "spec.json").read_text())
base = tmp / "base.ifc"; shutil.copy(ROOT / "tests" / "fixtures" / "ifc" / "bsi-arch-ifc4.ifc", base)
b.build(spec_in, tmp / "ok", base); spec = b.normalise_spec(spec_in)
okf = tmp / "ok" / "GF-MEP.ifc"
print("ifc-mep baseline:", v.validate(spec, [okf, base]).failed)
def im(name, edit):
    f = ifcopenshell.open(str(okf)); edit(f); out = tmp / ("im_" + name + ".ifc"); f.write(str(out))
    print("ifc-mep", name, v.validate(spec, [out, base]).failed)
def rot_duct(f):
    d = next(e for e in f.by_type("IfcDuctSegment") if e.Tag == spec["ducts"][0]["tag"])
    rp = d.ObjectPlacement.RelativePlacement; r = list(rp.RefDirection.DirectionRatios); ax = list(rp.Axis.DirectionRatios)
    nr = [ax[1]*r[2]-ax[2]*r[1], ax[2]*r[0]-ax[0]*r[2], ax[0]*r[1]-ax[1]*r[0]]
    rp.RefDirection = f.createIfcDirection(nr)
def move_wall(f):
    w = next(iter(f.by_type("IfcWall"))); loc = w.ObjectPlacement.RelativePlacement.Location
    loc.Coordinates = tuple(c + 5000.0 for c in loc.Coordinates)
def rename_space(f):
    for s in f.by_type("IfcSpace"): s.Name = "X"
def solid_offset(f):
    d = next(e for e in f.by_type("IfcDuctSegment"))
    s = next(i for r in d.Representation.Representations for i in r.Items if i.is_a("IfcExtrudedAreaSolid"))
    s.Position = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((2000.0, 0.0, 0.0)))
def extrude_dir(f):
    d = next(e for e in f.by_type("IfcDuctSegment"))
    s = next(i for r in d.Representation.Representations for i in r.Items if i.is_a("IfcExtrudedAreaSolid"))
    s.ExtrudedDirection = f.createIfcDirection((1.0, 0.0, 0.0))
def move_terminal(f):
    t = next(iter(f.by_type("IfcAirTerminal"))); loc = t.ObjectPlacement.RelativePlacement.Location
    loc.Coordinates = (loc.Coordinates[0] + 3000.0, loc.Coordinates[1], loc.Coordinates[2])
def move_ahu(f):
    t = next(iter(f.by_type("IfcUnitaryEquipment"))); loc = t.ObjectPlacement.RelativePlacement.Location
    loc.Coordinates = (loc.Coordinates[0], loc.Coordinates[1], loc.Coordinates[2] + 9000.0)
def pset_airflow(f):
    for p in f.by_type("IfcPropertySingleValue"):
        if p.Name == "AirflowLs": p.NominalValue.wrappedValue = 9999.0
for n, e in [("rotate_rect_about_axis", rot_duct), ("move_architect_wall", move_wall), ("rename_architect_spaces", rename_space),
             ("solid_position_offset", solid_offset), ("extrusion_direction", extrude_dir), ("move_terminal", move_terminal),
             ("move_ahu_up_9m", move_ahu), ("pset_airflow_9999", pset_airflow)]:
    try: im(n, e)
    except Exception as ex: print("ifc-mep", n, "ERR", type(ex).__name__, ex)

HD = ROOT / "skills" / "hvac-dxf"
hb = load("hdb", HD / "scripts" / "build.py", [HD]); hv = load("hdv", HD / "validator.py", [HD])
hspec_in = json.loads((HD / "examples" / "office_layout" / "spec.json").read_text())
hb.build(hspec_in, tmp / "hok"); hspec = hb.normalise_spec(hspec_in)
dxf = next((tmp / "hok").glob("*.dxf"))
print("hvac-dxf baseline:", hv.validate(hspec, [dxf]).failed, "mode", hspec["mode"])
def hd(name, edit):
    doc = ezdxf.readfile(str(dxf)); edit(doc); out = tmp / ("hd_" + name + ".dxf"); doc.saveas(str(out))
    print("hvac-dxf", name, hv.validate(hspec, [out]).failed)
def xrole(e):
    try: return e.get_xdata("MEPHVAC")[0][1]
    except Exception: return None
def first_duct(doc):
    return next(e for e in doc.modelspace() if xrole(e) == "duct" and e.dxftype() == "LWPOLYLINE")
def rotate_rect(doc):
    e = first_duct(doc); pts = [tuple(p) for p in e.get_points("xy")]
    cx = (pts[0][0] + pts[2][0]) / 2; cy = (pts[0][1] + pts[2][1]) / 2
    e.set_points([(cx - (y - cy), cy + (x - cx)) for x, y in pts], format="xy")
def non_rect(doc):
    e = first_duct(doc); pts = [tuple(p) for p in e.get_points("xy")]
    e.set_points([pts[0], pts[1], pts[2], (pts[3][0] + 400, pts[3][1] + 400)], format="xy")
def extra_duct(doc):
    doc.modelspace().add_lwpolyline([(0, 0), (5000, 0), (5000, 900), (0, 900)], close=True, dxfattribs={"layer": "M-DUCT-RECT"})
def wrong_text(doc):
    doc.modelspace().add_text("D1 200x100 5 L/s", dxfattribs={"layer": "M-ANNO-TAG", "insert": (0, 0)})
def tag_away(doc):
    for e in doc.modelspace():
        if xrole(e) == "duct_tag": e.dxf.insert = (9e5, 9e5)
for n, e in [("rotate_rect_90", rotate_rect), ("non_rectangle", non_rect), ("untagged_extra_duct", extra_duct),
             ("untagged_contradicting_text", wrong_text), ("tag_text_moved_away", tag_away)]:
    try: hd(n, e)
    except Exception as ex: print("hvac-dxf", n, "ERR", type(ex).__name__, ex)
bad = copy.deepcopy(hspec_in)
for grp in ("ducts", "sizing_schedule"):
    for d in bad[grp]:
        if d["size"]["shape"] == "rect": d["size"]["width_mm"] = 100; d["size"]["depth_mm"] = 50
try:
    hb.build(bad, tmp / "hbad"); print("hvac-dxf undersized-in-both: RELEASED")
except Exception as ex: print("hvac-dxf undersized-in-both:", type(ex).__name__, ex)

SS = ROOT / "skills" / "space-envelope-stack"
sb = load("ssb", SS / "scripts" / "build.py", [SS]); sv = load("ssv", SS / "validator.py", [SS])
sspec_in = json.loads((SS / "examples" / "two_storeys" / "spec.json").read_text())
sb.build(sspec_in, tmp / "sok"); sspec = sb.normalise_spec(sspec_in)
ifc = next((tmp / "sok").glob("*.ifc")); dx = sorted((tmp / "sok").glob("*.dxf"))
print("stack baseline:", sv.validate(sspec, [ifc, *dx]).failed)
def ss(name, edit):
    f = ifcopenshell.open(str(ifc)); edit(f); out = tmp / ("ss_" + name + ".ifc"); f.write(str(out))
    print("stack", name, sv.validate(sspec, [out, *dx]).failed)
def storey_z0(f):
    for s in f.by_type("IfcBuildingStorey"):
        s.ObjectPlacement.RelativePlacement.Location.Coordinates = (0.0, 0.0, 0.0)
def space_shift(f):
    sp = next(iter(f.by_type("IfcSpace"))); sp.ObjectPlacement.RelativePlacement.Location = f.createIfcCartesianPoint((20000.0, 0.0, 0.0))
def space_solid_pos(f):
    sp = next(iter(f.by_type("IfcSpace"))); s = sp.Representation.Representations[0].Items[0]
    s.Position = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, -9000.0)))
for n, e in [("all_storeys_at_z0", storey_z0), ("space_moved_20m", space_shift), ("space_solid_dropped_9m", space_solid_pos)]:
    try: ss(n, e)
    except Exception as ex: print("stack", n, "ERR", type(ex).__name__, ex)

from mep import sizing
s = sizing.clean_settings({})
print("cap 30 (< step 50):", sizing.size_duct(800, "rect", s, depth_cap_mm=30.0).as_dict())
print("huge flow 2e5 L/s round:", sizing.size_duct(200000, "round", s).as_dict())
prev = None
for q in [5, 10, 20, 40, 80, 160, 320, 640, 1280, 2560, 5000, 10000]:
    r = sizing.size_duct(q, "rect", s); a = r.width_mm * r.depth_mm
    if prev and a < prev: print("NON-MONOTONIC area at", q)
    prev = a
print("min_size 500 > max_depth 300:", sizing.size_duct(2000, "rect", sizing.clean_settings({"min_size_mm": 500, "max_depth_mm": 300})).as_dict())
