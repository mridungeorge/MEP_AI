"""hvac-dxf: build, validator mutations (a wrong size, tag, balance or position must be refused), determinism and the form description."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "hvac-dxf"
EXAMPLE = SKILL / "examples" / "office_layout"


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
    return _load("hvac_dxf_build_t", SKILL / "scripts" / "build.py")


@pytest.fixture(scope="module")
def validator():
    return _load("hvac_dxf_validator_t", SKILL / "validator.py")


def _spec() -> dict:
    return json.loads((EXAMPLE / "spec.json").read_text(encoding="utf-8"))


def _cli(spec: Path, out: Path, hashseed: str = "0"):
    return subprocess.run([sys.executable, str(SKILL / "scripts" / "build.py"), "--spec", str(spec), "--out", str(out)], capture_output=True, text=True,
                          env={**os.environ, "PYTHONHASHSEED": hashseed}, cwd=ROOT, check=False)


def test_the_example_builds_and_matches_the_expected_manifest(build_mod, tmp_path):
    expected = json.loads((EXAMPLE / "expected_manifest.json").read_text(encoding="utf-8"))
    got = build_mod.build(_spec(), tmp_path)
    for key in ("skill", "skill_version", "inputs", "spec_sha256", "measures", "validation"):
        assert got[key] == expected[key], key
    assert got["validation"]["passed"] and "tags_match_sizing_schedule" in got["validation"]["checks"] and "airflow_balance" in got["validation"]["checks"]
    if got["toolchain"] == expected["toolchain"]:
        assert got["files"] == expected["files"]


@pytest.mark.parametrize("mode", ["layout", "schematic"])
def test_both_modes_build_and_a_schematic_has_centrelines(build_mod, tmp_path, mode):
    spec = _spec() | {"mode": mode}
    build_mod.build(spec, tmp_path)
    kinds = {e.dxftype() for e in ezdxf.readfile(str(tmp_path / "L1-SA-LAYOUT.dxf")).modelspace()}
    assert ("LINE" in kinds) == (mode == "schematic")


@pytest.mark.parametrize("seed", ["0", "1", "99"])
def test_output_is_identical_whatever_the_hash_seed(tmp_path, seed):
    ref, out = tmp_path / "ref", tmp_path / "o"
    assert _cli(EXAMPLE / "spec.json", ref, "0").returncode == 0 and _cli(EXAMPLE / "spec.json", out, seed).returncode == 0
    assert (out / "L1-SA-LAYOUT.dxf").read_bytes() == (ref / "L1-SA-LAYOUT.dxf").read_bytes()


def _built(build_mod, tmp_path):
    build_mod.build(_spec(), tmp_path)
    return tmp_path / "L1-SA-LAYOUT.dxf"


def _validate(validator, spec, dxf):
    return validator.validate(build_norm(spec), [dxf])


def build_norm(spec):
    mod = sys.modules["hvac_dxf_build_t"]
    return mod.normalise_spec(spec)


def test_a_schedule_that_disagrees_with_the_drawing_is_refused(build_mod, tmp_path):
    spec = _spec()
    spec["sizing_schedule"][0]["size"]["width_mm"] = 700
    with pytest.raises(build_mod.ValidationFailed) as e:
        build_mod.build(spec, tmp_path)
    assert {"schedule_matches_drawing", "tags_match_sizing_schedule"} <= set(e.value.result.failed)
    assert not list(tmp_path.glob("*.dxf"))


def test_an_unbalanced_system_is_refused(build_mod, tmp_path):
    spec = _spec()
    spec["terminals"][0]["airflow_ls"] = 400
    with pytest.raises(build_mod.ValidationFailed) as e:
        build_mod.build(spec, tmp_path)
    assert e.value.result.failed == ["airflow_balance"]


def test_the_validator_catches_edits_made_to_the_file(build_mod, validator, tmp_path):
    dxf = _built(build_mod, tmp_path)
    spec = build_norm(_spec())
    assert validator.validate(spec, [dxf]).passed
    doc = ezdxf.readfile(str(dxf))
    msp = doc.modelspace()
    text = next(e for e in msp if e.dxftype() == "TEXT" and e.dxf.text.startswith("D1 "))
    text.dxf.text = "D1 500x300 600 L/s"
    doc.saveas(str(tmp_path / "edited.dxf"))
    assert "tags_match_sizing_schedule" in validator.validate(spec, [tmp_path / "edited.dxf"]).failed
    doc = ezdxf.readfile(str(dxf))
    poly = next(e for e in doc.modelspace() if e.dxftype() == "LWPOLYLINE" and e.get_xdata("MEPHVAC")[1][1] == "D2")
    poly.translate(500, 0, 0)
    doc.saveas(str(tmp_path / "moved.dxf"))
    assert "duct_geometry" in validator.validate(spec, [tmp_path / "moved.dxf"]).failed
    doc = ezdxf.readfile(str(dxf))
    doc.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "ROGUE"}) if "ROGUE" in doc.layers else (doc.layers.add("ROGUE"), doc.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "ROGUE"}))
    doc.saveas(str(tmp_path / "rogue.dxf"))
    assert "no_foreign_layers" in validator.validate(spec, [tmp_path / "rogue.dxf"]).failed
    doc = ezdxf.readfile(str(dxf))
    for e in list(doc.modelspace()):
        if e.dxftype() == "INSERT":
            doc.modelspace().delete_entity(e)
            break
    doc.saveas(str(tmp_path / "noterm.dxf"))
    assert "terminals" in validator.validate(spec, [tmp_path / "noterm.dxf"]).failed


@pytest.mark.parametrize("mutate,message", [
    (lambda s: s["ducts"].append(copy.deepcopy(s["ducts"][0])), "unique"),
    (lambda s: s["ducts"][0].update(end_mm=[0, 0]), "shorter"),
    (lambda s: s["ducts"][0]["size"].update(diameter_mm=300), "rectangular size"),
    (lambda s: s.update(mark="CON"), "reserved"),
    (lambda s: s.update(layers={"duct_rect": "A", "duct_round": "A"}), "own layer"),
    (lambda s: s["ducts"][0].update(airflow_ls=float("nan")), "finite"),
    (lambda s: s["title_block"].update(project="bad\x01text"), "project"),
])
def test_bad_specs_are_refused_before_building(build_mod, tmp_path, mutate, message):
    spec = copy.deepcopy(_spec())
    mutate(spec)
    with pytest.raises(build_mod.SpecError, match=message):
        build_mod.build(spec, tmp_path)
    assert not list(tmp_path.iterdir())


def test_the_app_can_describe_the_spec_card_as_a_form():
    from mep.skills_runner import form
    schema = json.loads((SKILL / "spec_card.json").read_text(encoding="utf-8"))
    fields = form.build_form(schema)
    assert fields
