"""The skills runner's pure parts: the card as a form, firm defaults, and the plain-language shortcut (no database, no CAD kernel)."""
import time

import pytest
from mep.skills_runner import form as skill_form
from mep.skills_runner.registry import ENABLED, UnknownSkill, get_skill
from mep.skills_runner.shortcut import missing_fields, parse_shortcut


def test_only_the_enabled_skills_can_be_looked_up():
    assert ENABLED == ("duct-fab", "space-envelope")
    for bad in ("../duct-fab", "duct-fab/../x", "", "Duct-Fab", "x" * 500, "duct-fab\x00"):
        with pytest.raises(UnknownSkill):
            get_skill(bad)


@pytest.mark.parametrize("skill", ENABLED)
def test_every_required_field_of_a_card_is_in_its_form(skill):
    schema = get_skill(skill).schema
    names = {f["name"] for f in skill_form.build_form(schema)}
    assert {r for r in schema["required"] if "const" not in schema["properties"][r]} <= names
    assert "spec_version" not in names and "units" not in names              # fixed by the card, never asked


def test_a_sheet_thickness_can_never_have_a_firm_default():
    form = skill_form.build_form(get_skill("duct-fab").schema)
    assert not {"sheet_thickness_mm", "seam.allowance_mm", "connection.allowance_mm", "geometry.width_mm"} & skill_form.default_paths(form)
    assert {"material", "notes", "geometry.circle_segments"} <= skill_form.default_paths(form)


def test_defaults_apply_only_where_the_chosen_variant_has_the_field():
    schema = get_skill("duct-fab").schema
    spec = {"fitting": "rect_reducer", "geometry": {}}
    filled, paths = skill_form.apply_defaults(schema, spec, {"material": "GALV", "geometry.circle_segments": 24, "sheet_thickness_mm": 0.5})
    assert filled["material"] == "GALV" and "circle_segments" not in filled["geometry"] and "sheet_thickness_mm" not in filled
    assert paths == ["material"]
    spec2 = {"fitting": "rect_to_round", "geometry": {}, "material": "mine"}
    filled2, paths2 = skill_form.apply_defaults(schema, spec2, {"material": "GALV", "geometry.circle_segments": 24})
    assert filled2["material"] == "mine" and filled2["geometry"]["circle_segments"] == 24 and paths2 == ["geometry.circle_segments"]


def test_room_defaults_fill_each_empty_room_and_keep_chosen_values():
    schema = get_skill("space-envelope").schema
    spec = {"rooms": [{"name": "A", "use": "Mine"}, {"name": "B"}, "not a room"]}
    filled, paths = skill_form.apply_defaults(schema, spec, {"rooms[].use": "Office", "rooms[].ceiling_void_mm": 500})
    assert filled["rooms"][0]["use"] == "Mine" and filled["rooms"][1]["use"] == "Office" and filled["rooms"][1]["ceiling_void_mm"] == 500
    assert sorted(paths) == ["rooms[0].ceiling_void_mm", "rooms[1].ceiling_void_mm", "rooms[1].use"]


@pytest.mark.parametrize("text,expect", [
    ("600x400 to 300 dia, 500 long, 0.8 mm sheet, pittsburgh seam 25, tdc 30, mark TR-01",
     {"fitting": "rect_to_round", "geometry": {"width_mm": 600, "height_mm": 400, "diameter_mm": 300, "length_mm": 500},
      "sheet_thickness_mm": 0.8, "seam": {"type": "pittsburgh", "allowance_mm": 25}, "connection": {"type": "tdc", "allowance_mm": 30}, "mark": "TR-01"}),
    ("800 x 500 to 600 x 300, length 700, concentric, 1.0 mm galv, snap lock seam 20, slip and drive 25",
     {"fitting": "rect_reducer", "geometry": {"width_in_mm": 800, "height_in_mm": 500, "width_out_mm": 600, "height_out_mm": 300, "length_mm": 700,
                                                "alignment": "concentric"},
      "sheet_thickness_mm": 1.0, "seam": {"type": "snap_lock", "allowance_mm": 20}, "connection": {"type": "slip_and_drive", "allowance_mm": 25}}),
    ("500x300 offset 100, 50, length 600", {"fitting": "rect_offset", "geometry": {"width_mm": 500, "height_mm": 300, "offset_x_mm": 100,
                                                                                 "offset_y_mm": 50, "length_mm": 600}}),
    ("1200×600 to ø400, 900 long", {"fitting": "rect_to_round", "geometry": {"width_mm": 1200, "height_mm": 600, "diameter_mm": 400, "length_mm": 900}}),
])
def test_duct_fab_sentences_are_read_into_the_fields_they_state(text, expect):
    spec = parse_shortcut("duct-fab", text).spec
    for k, v in expect.items():
        assert spec[k] == v, (k, spec)


def test_what_is_not_said_is_asked_never_invented():
    p = parse_shortcut("duct-fab", "600x400 to 300 dia")
    assert "sheet_thickness_mm" not in p.spec and "seam" not in p.spec and "mark" not in p.spec
    fields = {m["field"] for m in p.missing}
    assert {"geometry.length_mm", "sheet_thickness_mm", "seam.type", "seam.allowance_mm", "connection.type", "connection.allowance_mm", "mark"} <= fields
    assert all("?" in m["question"] for m in p.missing)
    empty = parse_shortcut("duct-fab", "please make something nice")
    assert empty.understood == [] and any(m["field"] == "fitting" for m in empty.missing)


def test_metres_are_flagged_and_a_half_given_offset_is_flagged():
    assert any("MILLIMETRES" in a for a in parse_shortcut("duct-fab", "0.6 m x 0.4 m to 300 dia, 500 long").assumptions)
    p = parse_shortcut("duct-fab", "500x300 offset 100")
    assert p.spec["geometry"]["offset_x_mm"] == 100 and any("only one offset" in a for a in p.assumptions)


def test_rooms_are_read_with_units_assumptions_and_plant_rooms():
    p = parse_shortcut("space-envelope", "Level 3, 4 m floor to floor\nStore 3000 x 2000 mm, 2400 mm high\nPlant room 6 x 4, 3.2 high; Lobby 5 x 5 m")
    assert p.spec["storey"] == {"name": "Level 3", "floor_to_floor_mm": 4000}
    rooms = p.spec["rooms"]
    assert [r["name"] for r in rooms] == ["Store", "Plant room", "Lobby"] and rooms[1]["kind"] == "plant_room"
    assert rooms[0]["outline"]["width_mm"] == 3000 and rooms[1]["outline"]["width_mm"] == 6000 and rooms[1]["height_mm"] == 3200
    assert [r["outline"]["x_mm"] for r in rooms] == [0, 3500, 10000]
    assert any("had no unit" in a for a in p.assumptions) and any("left to right" in a for a in p.assumptions)
    fields = {m["field"] for m in p.missing}
    assert "mark" in fields and "rooms[0].height_mm" not in fields and "rooms[2].height_mm" in fields


def test_missing_fields_for_a_partial_card_name_the_room_and_the_shape():
    spec = {"mark": "X", "storey": {"name": "L1"}, "rooms": [{"name": "A"}, {"name": "B", "outline": {"type": "polygon"}, "height_mm": 2700}]}
    got = {m["field"] for m in missing_fields("space-envelope", spec)}
    assert {"storey.floor_to_floor_mm", "rooms[0].outline", "rooms[0].height_mm", "rooms[1].outline.points_mm"} <= got
    assert "rooms[1].height_mm" not in got


@pytest.mark.parametrize("hostile", ["x" * 5000, "1" + "x1" * 2000, "9" * 1900 + " x " + "9" * 90, "a;" * 1000, "\n" * 1900, "(((" * 600,
                                     "Level " + "1" * 1900, "ø" * 1000, "600x400 to " * 190])
def test_hostile_sentences_are_read_quickly_or_not_at_all(hostile):
    for skill in ("duct-fab", "space-envelope"):
        t = time.monotonic()
        parse_shortcut(skill, hostile)
        assert time.monotonic() - t < 2.0
