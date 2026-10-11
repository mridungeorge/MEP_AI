"""ifc-mep: build into a copy of the architect's IFC, the validator's mutations (each wrong thing must be refused), determinism and the app's own reader."""
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import ifcopenshell
import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "ifc-mep"
EXAMPLE = SKILL / "examples" / "ahu_to_terminal"
BASE_FIXTURE = ROOT / "tests" / "fixtures" / "ifc" / "bsi-arch-ifc4.ifc"


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
    return _load("ifc_mep_build_t", SKILL / "scripts" / "build.py")


@pytest.fixture(scope="module")
def validator():
    return _load("ifc_mep_validator_t", SKILL / "validator.py")


def _spec() -> dict:
    return json.loads((EXAMPLE / "spec.json").read_text(encoding="utf-8"))


@pytest.fixture()
def job(tmp_path):
    """in/spec.json + in/base.ifc, the layout the worker gives a job."""
    d = tmp_path / "in"
    d.mkdir()
    shutil.copy(BASE_FIXTURE, d / "base.ifc")
    shutil.copy(EXAMPLE / "spec.json", d / "spec.json")
    return d


def _cli(job_dir: Path, out: Path, hashseed: str = "0"):
    return subprocess.run([sys.executable, str(SKILL / "scripts" / "build.py"), "--spec", str(job_dir / "spec.json"), "--out", str(out)],
                          capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": hashseed}, cwd=ROOT, check=False)


def test_the_example_builds_validates_and_matches_the_expected_manifest(build_mod, job, tmp_path):
    expected = json.loads((EXAMPLE / "expected_manifest.json").read_text(encoding="utf-8"))
    got = build_mod.build(_spec(), tmp_path / "out", job / "base.ifc")
    for key in ("skill", "skill_version", "inputs", "spec_sha256", "measures", "validation"):
        assert got[key] == expected[key], key
    assert {"architect_model_preserved", "schema_valid", "connections_match_spec", "connected_ports_meet_with_flow", "element_geometry"} <= set(got["validation"]["checks"])
    if got["toolchain"] == expected["toolchain"]:
        assert got["files"] == expected["files"]


def test_the_architect_model_is_kept_and_the_services_are_in_the_right_storey(build_mod, job, tmp_path):
    build_mod.build(_spec(), tmp_path / "out", job / "base.ifc")
    base, out = ifcopenshell.open(str(job / "base.ifc")), ifcopenshell.open(str(tmp_path / "out" / "GF-MEP.ifc"))
    assert {e.GlobalId for e in base if e.is_a("IfcRoot")} <= {e.GlobalId for e in out if e.is_a("IfcRoot")}
    import ifcopenshell.util.element as eu
    for cls in ("IfcDuctSegment", "IfcAirTerminal", "IfcUnitaryEquipment"):
        for el in out.by_type(cls):
            assert eu.get_container(el).Name == "00 groundfloor"
    assert len(out.by_type("IfcDuctSegment")) == 2 and len(out.by_type("IfcRelConnectsPorts")) == 3
    from mep.ingest.ifc import read_ifc  # the app's own reader still reads the combined file
    assert read_ifc(tmp_path / "out" / "GF-MEP.ifc") is not None


@pytest.mark.parametrize("seed", ["0", "5", "4242"])
def test_output_is_identical_whatever_the_hash_seed(job, tmp_path, seed):
    ref, out = tmp_path / "ref", tmp_path / "o"
    assert _cli(job, ref, "0").returncode == 0 and _cli(job, out, seed).returncode == 0
    assert (out / "GF-MEP.ifc").read_bytes() == (ref / "GF-MEP.ifc").read_bytes()


def test_a_missing_or_different_architect_file_is_refused(build_mod, job, tmp_path):
    (job / "base.ifc").write_bytes((job / "base.ifc").read_bytes() + b"\n")
    assert _cli(job, tmp_path / "o").returncode == 2
    (job / "base.ifc").unlink()
    assert _cli(job, tmp_path / "o2").returncode == 2
    assert not (tmp_path / "o2").exists() or not list((tmp_path / "o2").glob("*.ifc"))


@pytest.mark.parametrize("mutate,message", [
    (lambda s: s["ducts"][0].update(storey="no such storey"), "storeys not found"),
    (lambda s: s["ducts"].append(copy.deepcopy(s["ducts"][0])), "unique"),
    (lambda s: s["connections"].append({"from": "D1.end", "to": "T1.in"}), "already connected"),
    (lambda s: s["connections"].append({"from": "NOPE.start", "to": "T1.in"}), "no such port"),
    (lambda s: s["ducts"][0]["size"].update(diameter_mm=300), "rectangular size"),
    (lambda s: s["ducts"][0].update(end_mm=s["ducts"][0]["start_mm"]), "shorter"),
    (lambda s: s.update(mark="NUL"), "reserved"),
])
def test_bad_specs_are_refused(build_mod, job, tmp_path, mutate, message):
    spec = _spec()
    mutate(spec)
    with pytest.raises(build_mod.SpecError, match=message):
        build_mod.build(spec, tmp_path / "out", job / "base.ifc")


def test_an_unbalanced_system_is_refused(build_mod, job, tmp_path):
    spec = _spec()
    spec["terminals"][0]["airflow_ls"] = 300
    with pytest.raises(build_mod.ValidationFailed) as e:
        build_mod.build(spec, tmp_path / "out", job / "base.ifc")
    assert e.value.result.failed == ["airflow_balance"]


def _mutated(build_mod, job, tmp_path, edit):
    build_mod.build(_spec(), tmp_path / "ok", job / "base.ifc")
    f = ifcopenshell.open(str(tmp_path / "ok" / "GF-MEP.ifc"))
    edit(f)
    out = tmp_path / "bad.ifc"
    f.write(str(out))
    return out


def test_the_validator_refuses_each_kind_of_damage(build_mod, validator, job, tmp_path):
    spec = build_mod.normalise_spec(_spec())
    assert validator.validate(spec, [tmp_path / "x.txt"]).failed == ["ifc_present"]

    def check(edit, expect):
        bad = _mutated(build_mod, job, tmp_path, edit)
        res = validator.validate(spec, [bad, job / "base.ifc"])
        assert expect in res.failed, (expect, res.failed)

    def size(f):
        next(p for p in f.by_type("IfcRectangleProfileDef")).XDim *= 1.5

    def unlink_ports(f):
        f.remove(f.by_type("IfcRelConnectsPorts")[0])

    def move_port(f):
        port = next(p for p in f.by_type("IfcDistributionPort") if p.Name == "D2.start")
        port.ObjectPlacement.RelativePlacement.Location.Coordinates = (0.0, 0.0, 50.0)

    def wrong_storey(f):
        import ifcopenshell.api
        duct = next(e for e in f.by_type("IfcDuctSegment") if e.Tag == "D1")
        other = f.createIfcBuildingStorey(ifcopenshell.guid.new(), None, "Elsewhere")
        ifcopenshell.api.run("spatial.assign_container", f, products=[duct], relating_structure=other)

    def drop_element(f):
        f.remove(next(e for e in f.by_type("IfcAirTerminal")))

    def stray_duct(f):
        import ifcopenshell.api
        ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcDuctSegment", name="STRAY")

    def lose_architect_wall(f):
        wall = next(iter(f.by_type("IfcWall")))
        f.remove(wall)

    def turn_duct(f):
        d1 = next(e for e in f.by_type("IfcDuctSegment") if e.Tag == "D1")
        d1.ObjectPlacement.RelativePlacement.RefDirection.DirectionRatios = (0.0, 0.0, 1.0)

    def offset_solid(f):
        d1 = next(e for e in f.by_type("IfcDuctSegment") if e.Tag == "D1")
        d1.Representation.Representations[0].Items[0].Position.Location.Coordinates = (0.0, 0.0, 2000.0)

    def move_wall(f):
        wall = next(iter(f.by_type("IfcWall")))
        wall.ObjectPlacement.RelativePlacement.Location.Coordinates = (5000.0, 0.0, 0.0)

    def extra_wall(f):
        import ifcopenshell.api
        ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcWall", name="STRAY WALL")

    def airflow_9999(f):
        import ifcopenshell.api
        import ifcopenshell.util.element as eu
        for el in f.by_type("IfcDuctSegment"):
            ps = next(p for r in el.IsDefinedBy for p in [r.RelatingPropertyDefinition] if p.Name == "MEP_Services")
            ifcopenshell.api.run("pset.edit_pset", f, pset=ps, properties={"AirflowLs": 9999.0})
        assert eu is not None

    def big_ahu(f):
        ahu = next(iter(f.by_type("IfcUnitaryEquipment")))
        ahu.Representation.Representations[0].Items[0].SweptArea.XDim *= 3

    def move_terminal(f):
        t = next(iter(f.by_type("IfcAirTerminal")))
        t.ObjectPlacement.RelativePlacement.Location.Coordinates = (6750.0, 4000.0, 2000.0)

    def unlink_wall(f):
        import ifcopenshell.api
        ifcopenshell.api.run("spatial.unassign_container", f, products=[next(iter(f.by_type("IfcWall")))])

    def wall_pset(f):
        import ifcopenshell.api
        wall = next(iter(f.by_type("IfcWall")))
        ifcopenshell.api.run("pset.edit_pset", f, pset=ifcopenshell.api.run("pset.add_pset", f, product=wall, name="Pset_WallCommon_X"), properties={"FireRating": "240"})

    def wall_into_system(f):
        import ifcopenshell.guid
        f.createIfcRelAssignsToGroup(ifcopenshell.guid.new(), None, None, None, [next(iter(f.by_type("IfcWall")))], None, next(iter(f.by_type("IfcDistributionSystem"))))

    def extra_duct_pset(f):
        import ifcopenshell.api
        d1 = next(e for e in f.by_type("IfcDuctSegment") if e.Tag == "D1")
        ifcopenshell.api.run("pset.edit_pset", f, pset=ifcopenshell.api.run("pset.add_pset", f, product=d1, name="Extra"), properties={"A": "b"})

    check(unlink_wall, "architect_relationships_kept")
    check(wall_pset, "architect_relationships_kept")
    check(wall_into_system, "architect_relationships_kept")
    check(extra_duct_pset, "element_properties_read_back")
    check(size, "element_geometry")
    check(turn_duct, "element_geometry")
    check(offset_solid, "element_geometry")
    check(move_wall, "architect_model_preserved")
    check(extra_wall, "nothing_else_added")
    check(airflow_9999, "element_properties_read_back")
    check(big_ahu, "element_geometry")
    check(move_terminal, "port_positions")
    check(unlink_ports, "connections_match_spec")
    check(move_port, "connected_ports_meet_with_flow")
    check(wrong_storey, "storey_assignment")
    check(drop_element, "elements_present_once")
    check(stray_duct, "nothing_else_added")
    check(lose_architect_wall, "architect_model_preserved")


def test_without_the_architect_file_the_validator_cannot_pass(build_mod, validator, job, tmp_path):
    build_mod.build(_spec(), tmp_path / "ok", job / "base.ifc")
    assert "architect_file_supplied" in validator.validate(build_mod.normalise_spec(_spec()), [tmp_path / "ok" / "GF-MEP.ifc"]).failed


def test_the_app_can_describe_the_spec_card_as_a_form():
    from mep.skills_runner import form
    assert form.build_form(json.loads((SKILL / "spec_card.json").read_text(encoding="utf-8")))
