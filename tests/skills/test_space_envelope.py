"""space-envelope: spec checks, build, determinism, the validator (mutation tests) and a round trip through the APP's own readers."""
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "space-envelope"
EXAMPLES = sorted(p.name for p in (SKILL / "examples").iterdir() if p.is_dir())


def _load(name: str, path: Path):
    for p in (str(ROOT), str(SKILL)):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def build_mod():
    return _load("space_envelope_build_t", SKILL / "scripts" / "build.py")


@pytest.fixture(scope="module")
def validator():
    return _load("space_envelope_validator_t", SKILL / "validator.py")


@pytest.fixture(scope="module")
def geo():
    return _load("se_geometry", SKILL / "se_geometry.py")


def _spec(name: str) -> dict:
    return json.loads((SKILL / "examples" / name / "spec.json").read_text(encoding="utf-8"))


def _cli(spec: Path, out: Path, hashseed: str = "0") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SKILL / "scripts" / "build.py"), "--spec", str(spec), "--out", str(out)],
                          capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": hashseed}, cwd=ROOT, check=False)


# ---- the examples ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", EXAMPLES)
def test_example_builds_validates_and_matches_the_expected_manifest(build_mod, name, tmp_path):
    expected = json.loads((SKILL / "examples" / name / "expected_manifest.json").read_text(encoding="utf-8"))
    got = build_mod.build(_spec(name), tmp_path)
    for key in ("skill", "skill_version", "inputs", "spec_sha256", "measures", "validation"):
        assert got[key] == expected[key], key
    assert len(got["validation"]["checks"]) == 30 and got["validation"]["passed"] is True
    if got["toolchain"] != expected["toolchain"]:
        pytest.skip(f"toolchain {got['toolchain']} differs from {expected['toolchain']}: regenerate the expected manifest after review")
    assert got["files"] == expected["files"]


def test_there_is_a_plain_example_and_a_plant_room_example():
    assert any(r["kind"] == "plant_room" for n in EXAMPLES for r in _spec(n)["rooms"])
    assert any(r["kind"] == "room" for n in EXAMPLES for r in _spec(n)["rooms"])
    assert any(r["outline"]["type"] == "polygon" for n in EXAMPLES for r in _spec(n)["rooms"])


@pytest.mark.parametrize("seed", ["0", "1", "7", "12345"])
def test_output_is_identical_whatever_the_hash_seed(tmp_path, seed):
    ref = tmp_path / "ref"
    assert _cli(SKILL / "examples" / "plant_room_l_shape" / "spec.json", ref, "0").returncode == 0
    out = tmp_path / f"s{seed}"
    assert _cli(SKILL / "examples" / "plant_room_l_shape" / "spec.json", out, seed).returncode == 0
    for name in ("PLANT-ROOF.ifc", "PLANT-ROOF.dxf", "manifest.json"):
        assert (out / name).read_bytes() == (ref / name).read_bytes(), name


def test_a_room_keeps_its_global_id_when_the_drawing_is_reissued_with_another_room(build_mod, tmp_path):
    import ifcopenshell
    a = _spec("office_floor")
    b = copy.deepcopy(a)
    b["rooms"].append({"name": "Meeting room 2", "outline": {"type": "rectangle", "x_mm": 12000, "y_mm": 7000, "width_mm": 4000, "depth_mm": 2000},
                       "height_mm": 2700})
    ids = {}
    for tag, spec in (("a", a), ("b", b)):
        out = tmp_path / tag
        build_mod.build(spec, out)
        ids[tag] = {s.Name: s.GlobalId for s in ifcopenshell.open(str(out / "OFFICE-L1.ifc")).by_type("IfcSpace")}
    assert all(ids["a"][n] == ids["b"][n] for n in ids["a"]) and "Meeting room 2" in ids["b"]


# ---- the app's own readers see what was drawn ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", EXAMPLES)
def test_the_apps_ifc_and_dxf_readers_agree_with_the_spec(build_mod, geo, name, tmp_path):
    from mep.ingest.dxf import read_dxf
    from mep.ingest.ifc import read_ifc
    spec = build_mod.normalise_spec(_spec(name))
    build_mod.build(_spec(name), tmp_path)
    want = {r["name"]: geo.area_m2([tuple(p) for p in r["outline"]]) for r in spec["rooms"]}
    ifc = read_ifc(tmp_path / f"{spec['mark']}.ifc")
    dxf = read_dxf(tmp_path / f"{spec['mark']}.dxf")
    assert {s.name: round(s.area_m2, 3) for s in ifc.spaces} == {n: round(a, 3) for n, a in want.items()}
    assert {s.name: round(s.area_m2, 3) for s in dxf.spaces} == {n: round(a, 3) for n, a in want.items()}
    assert ifc.metadata["area_source"] == "quantities" and dxf.metadata["scale_ok"] is True
    assert {s.storey for s in ifc.spaces} == {spec["storey"]["name"]}
    kinds = {r["name"]: r["kind"] for r in spec["rooms"]}
    assert all((s.use == "Plant") == (kinds[s.name] == "plant_room") or kinds[s.name] == "plant_room" for s in ifc.spaces)


# ---- the spec card ------------------------------------------------------------------------------------------------------

def _mut(path: str, value):
    spec = _spec("office_floor")
    node = spec
    keys = path.split(".")
    for k in keys[:-1]:
        node = node[int(k)] if isinstance(node, list) else node[k]
    last = keys[-1]
    if value is KeyError:
        del node[int(last) if isinstance(node, list) else last]
    elif isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return spec


@pytest.mark.parametrize("spec,why", [
    (_mut("mark", "bad mark!"), "mark"),
    (_mut("mark", "CON"), "reserved"),
    (_mut("units", "m"), "units"),
    (_mut("extra", 1), "extra"),
    (_mut("storey.floor_to_floor_mm", 1000), "floor_to_floor"),
    (_mut("storey", KeyError), "storey"),
    (_mut("rooms", []), "rooms"),
    (_mut("rooms.0.height_mm", 1000), "height"),
    (_mut("rooms.0.height_mm", 3400), "storey height"),
    (_mut("rooms.0.outline.width_mm", 100), "width"),
    (_mut("rooms.0.name", "Server room"), "already used"),
    (_mut("rooms.0.name", "Server\x00room"), "name"),
    (_mut("rooms.0.outline.width_mm", float("nan")), "finite"),
    (_mut("rooms.0.outline.width_mm", "12000"), "width"),
    (_mut("rooms.1.outline.x_mm", 11000), "overlap"),
    (_mut("rooms.1.outline", {"type": "polygon", "points_mm": [[12000, 0], [17000, 4000], [17000, 0], [12000, 4000]]}), "not a simple polygon"),
    (_mut("rooms.1.outline", {"type": "polygon", "points_mm": [[12000, 0], [12010, 0], [12010, 10]]}), "smaller"),
    (_mut("rooms.1.kind", "bathroom"), "kind"),
    (_mut("notes", "x" + chr(0x202E) + "y"), "notes"),
])
def test_a_bad_spec_is_rejected_with_a_reason_and_nothing_is_written(build_mod, spec, why, tmp_path):
    f = tmp_path / "spec.json"
    f.write_text(json.dumps(spec), encoding="utf-8")
    p = _cli(f, tmp_path / "out")
    assert p.returncode == 2 and "spec rejected" in p.stderr, p.stderr
    assert not (tmp_path / "out").exists() or not list((tmp_path / "out").iterdir())
    with pytest.raises(build_mod.SpecError):
        build_mod.normalise_spec(json.loads(json.dumps(spec)) if why != "finite" else spec)


def test_touching_rooms_are_allowed_but_overlapping_ones_are_not(geo):
    a = geo.rectangle(0, 0, 1000, 1000)
    assert not geo.interiors_overlap(a, geo.rectangle(1000, 0, 1000, 1000))           # share an edge
    assert not geo.interiors_overlap(a, geo.rectangle(1000, 1000, 1000, 1000))        # share a corner
    assert geo.interiors_overlap(a, geo.rectangle(500, 0, 1000, 1000))                # partly collinear but overlapping
    assert geo.interiors_overlap(a, geo.rectangle(0, 0, 1000, 1000))                  # identical
    assert geo.interiors_overlap(a, geo.rectangle(250, 250, 500, 500))                # contained
    l_shape = [(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (6.0, 4.0), (6.0, 8.0), (0.0, 8.0)]
    assert not geo.interiors_overlap(l_shape, geo.rectangle(6, 4, 4, 4))              # sits in the notch
    assert geo.interiors_overlap(l_shape, geo.rectangle(5, 3, 2, 2))                  # straddles the notch corner


def test_exit_code_4_when_the_output_cannot_be_written(tmp_path):
    blocker = tmp_path / "out"
    blocker.write_text("a file, not a folder")
    p = _cli(SKILL / "examples" / "office_floor" / "spec.json", blocker)
    assert p.returncode == 4 and "build failed" in p.stderr


# ---- the validator rejects tampered output (mutation tests) ------------------------------------------------------------

@pytest.fixture
def good(build_mod, tmp_path):
    out = tmp_path / "good"
    manifest = build_mod.build(_spec("plant_room_l_shape"), out)
    return out, manifest["inputs"]


def _validate(validator, spec, out: Path, with_manifest: bool = False):
    files = [out / "PLANT-ROOF.ifc", out / "PLANT-ROOF.dxf"] + ([out / "manifest.json"] if with_manifest else [])
    return validator.validate(spec, files)


def test_the_untouched_output_passes_every_check(validator, good):
    out, spec = good
    r = _validate(validator, spec, out, with_manifest=True)
    assert r.passed and r.failed == [] and len(r.checks) == 30


def _edit_ifc(out: Path, pattern: str, repl: str, count: int = 1) -> None:
    p = out / "PLANT-ROOF.ifc"
    text = p.read_text(encoding="utf-8")
    new, n = re.subn(pattern, repl, text, count=count)
    assert n >= 1, pattern
    p.write_text(new, encoding="utf-8")


def _edit_dxf(out: Path, fn) -> None:
    p = out / "PLANT-ROOF.dxf"
    doc = ezdxf.readfile(p)
    fn(doc)
    doc.saveas(p)


@pytest.mark.parametrize("label,mutate,expected_failure", [
    ("a vertex of the IFC outline moved", lambda o: _edit_ifc(o, r"IFCCARTESIANPOINT\(\(10000\.,4000\.\)\)", "IFCCARTESIANPOINT((10050.,4000.))"), "ifc_footprints"),
    ("a room renamed in the IFC", lambda o: _edit_ifc(o, r"'AHU plant room'", "'AHU plant rm'", 2), "ifc_space_names"),
    ("the IFC schema changed", lambda o: _edit_ifc(o, r"FILE_SCHEMA\(\('IFC4'\)\)", "FILE_SCHEMA(('IFC2X3'))"), "ifc_schema"),
    ("the extrusion depth changed", lambda o: _edit_ifc(o, r"IFCEXTRUDEDAREASOLID\(#(\d+),#(\d+),#(\d+),3800\.\)", r"IFCEXTRUDEDAREASOLID(#\1,#\2,#\3,3000.)"), "ifc_footprints"),
    ("the area quantity changed", lambda o: _edit_ifc(o, r"IFCQUANTITYAREA\('NetFloorArea',\$,\$,[0-9.]+,\$\)", "IFCQUANTITYAREA('NetFloorArea',$,$,99.,$)"), "ifc_area_quantity"),
    ("a plant room no longer flagged", lambda o: _edit_ifc(o, r"'PlantRoom',\$,IFCBOOLEAN\(\.T\.\)", "'PlantRoom',$,IFCBOOLEAN(.F.)"), "ifc_plant_flag"),
    ("the length unit changed to metres", lambda o: _edit_ifc(o, r"IFCSIUNIT\(\*,\.LENGTHUNIT\.,\.MILLI\.,\.METRE\.\)", "IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.)"), "ifc_units"),
    ("the IFC file is garbage", lambda o: (o / "PLANT-ROOF.ifc").write_text("not an ifc file"), "ifc_loads"),
    ("a DXF vertex moved", lambda o: _edit_dxf(o, lambda d: d.modelspace().query("LWPOLYLINE")[0].set_points([(0, 0), (10000, 0), (10000, 4000), (6000, 4000), (6000, 8100), (0, 8000)])), "dxf_outlines"),
    ("a stray line was added to the DXF", lambda o: _edit_dxf(o, lambda d: d.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "A-SPACE"})), "dxf_entities"),
    ("a stray circle on another layer", lambda o: _edit_dxf(o, lambda d: (d.layers.add("JUNK"), d.modelspace().add_circle((0, 0), 5, dxfattribs={"layer": "JUNK"}))), "dxf_layers"),
    ("a room label was deleted", lambda o: _edit_dxf(o, lambda d: d.modelspace().delete_entity(d.modelspace().query("TEXT")[0])), "dxf_labels"),
    ("a label moved into another room", lambda o: _edit_dxf(o, lambda d: setattr(d.modelspace().query("TEXT")[0].dxf, "insert", (11500, 2000, 0))), "dxf_labels"),
    ("the DXF units changed", lambda o: _edit_dxf(o, lambda d: d.header.__setitem__("$INSUNITS", 6)), "dxf_units"),
    ("a plant room moved to the room layer", lambda o: _edit_dxf(o, lambda d: [setattr(e.dxf, "layer", "A-SPACE") for e in d.modelspace().query("LWPOLYLINE")]), "dxf_outlines"),
    ("a polyline got an arc segment", lambda o: _edit_dxf(o, lambda d: d.modelspace().query("LWPOLYLINE")[0].set_points([(0, 0, 0, 0, 0.5), (10000, 0), (10000, 4000), (6000, 4000), (6000, 8000), (0, 8000)], format="xyseb")), "dxf_entities"),
    ("the DXF was swapped for another building's", lambda o: _edit_dxf(o, lambda d: [d.modelspace().delete_entity(e) for e in list(d.modelspace())]), "dxf_loads"),
])
def test_the_validator_rejects_tampered_output(validator, good, label, mutate, expected_failure):
    out, spec = good
    mutate(out)
    r = _validate(validator, spec, out)
    assert not r.passed, label
    assert expected_failure in r.failed, (label, r.failed)


def test_the_validator_checks_the_manifest_checksums(validator, good):
    out, spec = good
    m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    m["files"][0]["sha256"] = "0" * 64
    (out / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    assert "manifest_checksums" in _validate(validator, spec, out, with_manifest=True).failed


def test_the_validator_refuses_missing_extra_or_duplicate_files(validator, good):
    out, spec = good
    assert not validator.validate(spec, [out / "PLANT-ROOF.ifc"]).passed
    assert not validator.validate(spec, [out / "PLANT-ROOF.ifc", out / "PLANT-ROOF.dxf", out / "PLANT-ROOF.dxf"]).passed
    assert not validator.validate(spec, [out / "PLANT-ROOF.ifc", out / "PLANT-ROOF.dxf", out / "notes.txt"]).passed
    assert not validator.validate({}, [out / "PLANT-ROOF.ifc", out / "PLANT-ROOF.dxf"]).passed


def test_a_build_the_validator_rejects_releases_nothing(build_mod, tmp_path, monkeypatch):
    import se_writers
    real = se_writers.build_dxf

    def wrong(spec, path):
        spec = copy.deepcopy(spec)
        spec["rooms"][0]["outline"][2][0] += 50           # the DXF gets a different corner than the IFC
        real(spec, path)

    monkeypatch.setattr(se_writers, "build_dxf", wrong)
    out = tmp_path / "out"
    with pytest.raises(build_mod.ValidationFailed) as e:
        build_mod.build(_spec("office_floor"), out)
    assert "cross_ifc_dxf" in e.value.result.failed
    assert not out.exists() or not list(out.iterdir())


# ---- review round 1: the world geometry is checked, not just the local profiles ----------------------------------------

def _ifc_edit(out: Path, fn) -> None:
    import ifcopenshell
    p = out / "PLANT-ROOF.ifc"
    f = ifcopenshell.open(str(p))
    fn(f)
    f.write(str(p))


def _shift_first_space(f) -> None:
    sp = f.by_type("IfcSpace")[0]
    sp.ObjectPlacement.RelativePlacement.Location.Coordinates = (50000.0, 0.0, 0.0)


def _lift_solid(f) -> None:
    f.by_type("IfcExtrudedAreaSolid")[0].Position.Location.Coordinates = (0.0, 0.0, 9000.0)


def _rotate_space(f) -> None:
    sp = f.by_type("IfcSpace")[0]
    sp.ObjectPlacement.RelativePlacement.RefDirection = f.createIfcDirection((0.0, 1.0, 0.0))


def _extra_wall(f) -> None:
    f.createIfcWall(ifcopenshell_guid(), None, "stray")


def ifcopenshell_guid() -> str:
    import ifcopenshell.guid
    return ifcopenshell.guid.new()


def _flip_extrusion(f) -> None:
    f.by_type("IfcExtrudedAreaSolid")[0].ExtrudedDirection.DirectionRatios = (0.0, 0.0, -1.0)


def _second_length_unit(f) -> None:
    proj = f.by_type("IfcProject")[0]
    units = list(proj.UnitsInContext.Units)
    proj.UnitsInContext.Units = [f.createIfcSIUnit(UnitType="LENGTHUNIT", Name="METRE"), *units]


def _storey_z(f) -> None:
    f.by_type("IfcBuildingStorey")[0].ObjectPlacement.RelativePlacement.Location.Coordinates = (0.0, 0.0, 99000.0)


@pytest.mark.parametrize("label,mutate", [
    ("space placement moved 50 m", _shift_first_space), ("solid position lifted", _lift_solid), ("space rotated", _rotate_space),
    ("stray wall", _extra_wall), ("extrusion flipped", _flip_extrusion), ("second length unit", _second_length_unit),
    ("storey placement not its elevation", _storey_z),
])
def test_the_validator_rejects_a_changed_placement_unit_or_extra_entity_in_the_ifc(validator, good, label, mutate):
    out, spec = good
    _ifc_edit(out, mutate)
    r = _validate(validator, spec, out)
    assert not r.passed and "ifc_structure" in r.failed, label


@pytest.mark.parametrize("label,mutate", [
    ("extrusion mirrored", lambda d: setattr(d.modelspace().query("LWPOLYLINE")[0].dxf, "extrusion", (0, 0, -1))),
    ("constant width", lambda d: setattr(d.modelspace().query("LWPOLYLINE")[0].dxf, "const_width", 3000)),
    ("label rotated", lambda d: setattr(d.modelspace().query("TEXT")[0].dxf, "rotation", 45)),
    ("label height", lambda d: setattr(d.modelspace().query("TEXT")[0].dxf, "height", 100000)),
    ("junk block", lambda d: d.blocks.new("JUNK").add_line((0, 0), (1, 1))),
])
def test_the_validator_rejects_hidden_dxf_drawing_attributes(validator, good, label, mutate):
    out, spec = good
    _edit_dxf(out, mutate)
    r = _validate(validator, spec, out)
    assert not r.passed and "dxf_entities" in r.failed, label


def test_a_room_name_with_a_double_space_builds(build_mod, tmp_path):
    spec = _spec("plant_room_l_shape")
    spec["rooms"][0]["name"] = "AHU  plant room"
    assert build_mod.build(spec, tmp_path / "o")["validation"]["passed"] is True


def test_a_spec_with_too_many_outline_points_is_refused(build_mod):
    spec = _spec("plant_room_l_shape")
    ring = [[i * 10, 0] for i in range(64)]
    room = {"name": "R0", "kind": "room", "height_mm": 2400, "outline": {"type": "polygon", "points_mm": ring}}
    spec["rooms"] = [dict(room, name=f"R{i}") for i in range(20)]
    with pytest.raises(Exception, match="outline points|more than"):
        build_mod.normalise_spec(spec)
