"""IFC ingest against real public files (buildingSMART Sample-Test-Files, CC BY 4.0) and hand-derived expectations."""
from pathlib import Path

import pytest
import yaml
from mep.ingest.ifc import IfcReadError, load_profile, match_profile, read_ifc

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "ifc"
EXPECTED = yaml.safe_load((FIX / "expected.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["bsi-arch-ifc4", "bsi-arch-ifc2x3"])
def test_spaces_match_the_hand_derived_values(name):
    res = read_ifc(FIX / f"{name}.ifc")
    want = EXPECTED[name]
    assert res.metadata["schema"] == want["schema"]
    got = {s.key: s for s in res.spaces}
    assert set(got) == {w["guid"] for w in want["spaces"]}
    for w in want["spaces"]:
        s = got[w["guid"]]
        assert s.name == w["name"] and s.storey == w["storey"] and s.use == w["use"]
        assert s.area_m2 == pytest.approx(w["area_m2"], abs=1e-6)
        assert s.ceiling_void_mm is None
        assert s.provenance == "extracted" and s.source_kind == "ifc"


def test_the_exporter_is_read_from_the_header_and_mapped_to_a_profile():
    res = read_ifc(FIX / "bsi-arch-ifc4.ifc")
    assert "Sketchup" in res.metadata["originating_system"]
    assert res.metadata["exporter_profile"] == "sketchup" and res.metadata["profile_status"] == "verified"
    assert res.metadata["area_source"] == "geometry"
    assert any("computed from the space geometry" in n for n in res.spaces[0].notes)


@pytest.mark.parametrize("system,profile", [
    ("Autodesk Revit 2024 (ENU)", "revit"), ("Revit", "revit"), ("ARCHICAD 27.0.0 INT", "archicad"),
    ("GRAPHISOFT ArchiCAD", "archicad"), ("SketchUp 2026", "sketchup"), ("", "unknown"), (None, "unknown"),
    ("Some CAD tool 4.2", "unknown"), ("Revit-lookalike archicad", "revit"),
])
def test_profile_matching_is_by_originating_system(system, profile):
    assert match_profile(system)["id"] == profile


def test_profiles_exist_for_revit_archicad_and_unknown_and_unverified_ones_say_so():
    for pid in ("revit", "archicad", "unknown", "sketchup"):
        assert load_profile(pid)["id"] == pid
    assert load_profile("revit")["status"] == "unverified" and load_profile("sketchup")["status"] == "verified"


def test_a_file_with_no_spaces_reads_cleanly_with_a_problem_note():
    res = read_ifc(FIX / "bsi-hvac-ifc4.ifc")
    assert res.spaces == [] and res.metadata["spaces_total"] == 0
    assert any("no IfcSpace" in p for p in res.problems)


def test_the_file_hash_is_recorded():
    res = read_ifc(FIX / "bsi-arch-ifc4.ifc")
    assert len(res.source_sha256) == 64 and res.source_name == "bsi-arch-ifc4.ifc"


def test_garbage_and_missing_files_are_refused_not_crashed_on(tmp_path):
    bad = tmp_path / "bad.ifc"
    bad.write_text("not an ifc file at all", encoding="utf-8")
    with pytest.raises(IfcReadError):
        read_ifc(bad)
    with pytest.raises(IfcReadError):
        read_ifc(tmp_path / "missing.ifc")
    empty = tmp_path / "empty.ifc"
    empty.write_bytes(b"")
    with pytest.raises(IfcReadError):
        read_ifc(empty)


def test_a_non_ifc_extension_is_refused(tmp_path):
    f = tmp_path / "model.dwg"
    f.write_bytes(b"AC1027")
    with pytest.raises(IfcReadError):
        read_ifc(f)


def test_quantities_are_preferred_over_geometry_and_converted_to_square_metres(tmp_path):
    """A space with Qto_SpaceBaseQuantities.NetFloorArea uses it (and says so), not the geometry."""
    import ifcopenshell
    import ifcopenshell.api

    model = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="p")
    ifcopenshell.api.run("unit.assign_unit", model)
    site = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSite", name="s")
    building = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuilding", name="b")
    storey = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcBuildingStorey", name="Level 1")
    ifcopenshell.api.run("aggregate.assign_object", model, products=[site], relating_object=project)
    ifcopenshell.api.run("aggregate.assign_object", model, products=[building], relating_object=site)
    ifcopenshell.api.run("aggregate.assign_object", model, products=[storey], relating_object=building)
    space = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSpace", name="Plant room")
    ifcopenshell.api.run("aggregate.assign_object", model, products=[space], relating_object=storey)
    qto = ifcopenshell.api.run("pset.add_qto", model, product=space, name="Qto_SpaceBaseQuantities")
    ifcopenshell.api.run("pset.edit_qto", model, qto=qto, properties={"NetFloorArea": 12.5})
    pset = ifcopenshell.api.run("pset.add_pset", model, product=space, name="Pset_SpaceCommon")
    ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={"OccupancyType": "Plant"})
    path = tmp_path / "made.ifc"
    model.write(str(path))
    res = read_ifc(path)
    s = res.spaces[0]
    assert (s.name, s.storey, s.use, s.area_m2) == ("Plant room", "Level 1", "Plant", 12.5)
    assert res.metadata["area_source"] == "quantities"
    assert res.metadata["exporter_profile"] == "unknown"


def test_duplicate_and_unnamed_spaces_are_counted_for_the_health_score(tmp_path):
    import ifcopenshell
    import ifcopenshell.api

    model = ifcopenshell.api.run("project.create_file", version="IFC4")
    ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcProject", name="p")
    ifcopenshell.api.run("unit.assign_unit", model)
    for name in ("Office", "Office", None):
        sp = ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSpace", name=name)
        qto = ifcopenshell.api.run("pset.add_qto", model, product=sp, name="Qto_SpaceBaseQuantities")
        ifcopenshell.api.run("pset.edit_qto", model, qto=qto, properties={"NetFloorArea": 20.0})
    path = tmp_path / "dups.ifc"
    model.write(str(path))
    res = read_ifc(path)
    assert res.metadata["spaces_total"] == 3
    assert res.metadata["duplicate_spaces"] == 1 and res.metadata["unnamed_spaces"] == 1
    assert res.metadata["spaces_with_area"] == 3 and res.metadata["spaces_without_storey"] == 3
