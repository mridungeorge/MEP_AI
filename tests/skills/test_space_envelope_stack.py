"""space-envelope-stack: build, validator mutations, determinism, bad cards, and the app's own readers on the combined file."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import ezdxf
import ifcopenshell
import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "space-envelope-stack"
EXAMPLE = SKILL / "examples" / "two_storeys"


def _load(name: str, path: Path):
    for p in (str(ROOT), str(SKILL), str(ROOT / "skills" / "space-envelope")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def build_mod():
    return _load("ses_build_t", SKILL / "scripts" / "build.py")


@pytest.fixture(scope="module")
def validator():
    return _load("ses_validator_t", SKILL / "validator.py")


def _spec() -> dict:
    return json.loads((EXAMPLE / "spec.json").read_text(encoding="utf-8"))


def test_the_example_builds_validates_and_matches_the_expected_manifest(build_mod, tmp_path):
    expected = json.loads((EXAMPLE / "expected_manifest.json").read_text(encoding="utf-8"))
    got = build_mod.build(_spec(), tmp_path)
    for key in ("skill", "skill_version", "inputs", "spec_sha256", "measures", "validation"):
        assert got[key] == expected[key], key
    assert {"ifc_storeys", "ifc_spaces", "dxf_storey_01", "dxf_storey_02"} <= set(got["validation"]["checks"])
    if got["toolchain"] == expected["toolchain"]:
        assert got["files"] == expected["files"]


def test_the_combined_file_has_each_room_on_its_storey_and_the_app_reads_it(build_mod, tmp_path):
    build_mod.build(_spec(), tmp_path)
    f = ifcopenshell.open(str(tmp_path / "TOWER-A.ifc"))
    names = {s.Name: [o.Name for r in (s.IsDecomposedBy or []) for o in r.RelatedObjects] for s in f.by_type("IfcBuildingStorey")}
    assert names == {"Level 1": ["Open office L1", "Meeting room L1"], "Level 2": ["Open office L2", "Plant L2"]}
    from mep.ingest.ifc import read_ifc
    got = read_ifc(tmp_path / "TOWER-A.ifc")
    assert {s.name: s.storey for s in got.spaces} == {"Open office L1": "Level 1", "Meeting room L1": "Level 1", "Open office L2": "Level 2", "Plant L2": "Level 2"}


@pytest.mark.parametrize("seed", ["0", "3", "2024"])
def test_output_is_identical_whatever_the_hash_seed(tmp_path, seed):
    def run(out, s):
        return subprocess.run([sys.executable, str(SKILL / "scripts" / "build.py"), "--spec", str(EXAMPLE / "spec.json"), "--out", str(out)], capture_output=True, text=True,
                              env={**os.environ, "PYTHONHASHSEED": s}, cwd=ROOT, check=False)
    ref, out = tmp_path / "ref", tmp_path / "o"
    assert run(ref, "0").returncode == 0 and run(out, seed).returncode == 0
    for name in ("TOWER-A.ifc", "TOWER-A-S01.dxf", "TOWER-A-S02.dxf", "manifest.json"):
        assert (out / name).read_bytes() == (ref / name).read_bytes(), name


@pytest.mark.parametrize("mutate,message", [
    (lambda s: s["storeys"][1].update(elevation_mm=4000), "do not stack"),
    (lambda s: s["storeys"][1]["rooms"][0].update(name="Open office L1"), "already used"),
    (lambda s: s["storeys"][1].update(name="level 1"), "unique"),
    (lambda s: s["storeys"].pop(), "storeys"),
    (lambda s: s.update(mark="CON"), "reserved"),
    (lambda s: s["storeys"][0]["rooms"][0].update(height_mm=3500), "storey height"),
    (lambda s: s["storeys"][0]["rooms"][1]["outline"].update(x_mm=1000), "overlap"),
])
def test_bad_cards_are_refused(build_mod, tmp_path, mutate, message):
    spec = copy.deepcopy(_spec())
    mutate(spec)
    with pytest.raises(build_mod.SpecError, match=message):
        build_mod.build(spec, tmp_path)
    assert not list(tmp_path.iterdir())


def _files(tmp_path):
    return [tmp_path / n for n in ("TOWER-A.ifc", "TOWER-A-S01.dxf", "TOWER-A-S02.dxf")]


def test_the_validator_refuses_each_kind_of_damage(build_mod, validator, tmp_path):
    spec = build_mod.normalise_spec(_spec())
    build_mod.build(_spec(), tmp_path / "ok")
    ok_files = _files(tmp_path / "ok")
    assert validator.validate(spec, ok_files).passed

    def ifc_case(edit, expect):
        f = ifcopenshell.open(str(ok_files[0]))
        edit(f)
        bad = tmp_path / "bad.ifc"
        f.write(str(bad))
        res = validator.validate(spec, [bad, *ok_files[1:]])
        assert expect in res.failed, (expect, res.failed)

    def move_storey(f):
        next(s for s in f.by_type("IfcBuildingStorey") if s.Name == "Level 2").Elevation = 3000.0

    def rename_room(f):
        next(s for s in f.by_type("IfcSpace") if s.Name == "Plant L2").Name = "Boiler"

    def drop_room(f):
        f.remove(next(s for s in f.by_type("IfcSpace") if s.Name == "Meeting room L1"))

    def widen(f):
        sp = next(s for s in f.by_type("IfcSpace") if s.Name == "Open office L1")
        sp.Representation.Representations[0].Items[0].Depth = 2500.0

    def swap_storey(f):
        a = next(s for s in f.by_type("IfcSpace") if s.Name == "Open office L1")
        l2 = next(s for s in f.by_type("IfcBuildingStorey") if s.Name == "Level 2")
        l1 = next(s for s in f.by_type("IfcBuildingStorey") if s.Name == "Level 1")
        for r in l1.IsDecomposedBy:
            r.RelatedObjects = [o for o in r.RelatedObjects if o != a]
        l2.IsDecomposedBy[0].RelatedObjects = [*l2.IsDecomposedBy[0].RelatedObjects, a]

    def storey_placement(f):
        next(s for s in f.by_type("IfcBuildingStorey") if s.Name == "Level 2").ObjectPlacement.RelativePlacement.Location.Coordinates = (0.0, 0.0, 0.0)

    def move_space(f):
        next(s for s in f.by_type("IfcSpace") if s.Name == "Open office L1").ObjectPlacement.RelativePlacement.Location.Coordinates = (7777.0, 0.0, 0.0)

    def void_pset(f):
        import ifcopenshell.api
        sp = next(s for s in f.by_type("IfcSpace") if s.Name == "Open office L1")
        ps = next(p for r in sp.IsDefinedBy for p in [r.RelatingPropertyDefinition] if p.Name == "MEP_SpaceEnvelope")
        ifcopenshell.api.run("pset.edit_pset", f, pset=ps, properties={"CeilingVoidMm": 5.0})

    ifc_case(storey_placement, "ifc_storey_placement")
    ifc_case(move_space, "ifc_spaces")
    ifc_case(void_pset, "ifc_spaces")
    ifc_case(move_storey, "ifc_storeys")
    ifc_case(rename_room, "ifc_spaces")
    ifc_case(drop_room, "ifc_spaces")
    ifc_case(widen, "ifc_spaces")
    ifc_case(swap_storey, "ifc_spaces")
    doc = ezdxf.readfile(str(ok_files[1]))
    next(iter(doc.modelspace().query("LWPOLYLINE"))).translate(300, 0, 0)
    doc.saveas(str(tmp_path / "bad.dxf"))
    res = validator.validate(spec, [ok_files[0], tmp_path / "bad.dxf", ok_files[2]])
    assert "dxf_storey_01" in res.failed
    assert "input_files" in validator.validate(spec, ok_files[:2]).failed


def test_the_app_can_describe_the_card_as_a_form():
    from mep.skills_runner import form
    assert form.build_form(json.loads((SKILL / "spec_card.json").read_text(encoding="utf-8")))
