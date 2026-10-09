"""Build, determinism, gate and example (expected manifest) tests for skills/duct-fab."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("cadquery")
pytest.importorskip("ezdxf")

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "duct-fab"
EXAMPLES = sorted(p.name for p in (SKILL / "examples").iterdir() if p.is_dir())


@pytest.fixture(scope="module")
def build_mod():
    spec = importlib.util.spec_from_file_location("duct_fab_build_t", SKILL / "scripts" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _spec(name: str) -> dict:
    return json.loads((SKILL / "examples" / name / "spec.json").read_text(encoding="utf-8"))


def _run_cli(spec: Path, out: Path, hashseed: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    return subprocess.run([sys.executable, str(SKILL / "scripts" / "build.py"), "--spec", str(spec), "--out", str(out)],
                          capture_output=True, text=True, env=env, cwd=ROOT, check=False)


def test_there_is_one_example_per_fitting():
    kinds = {_spec(n)["fitting"] for n in EXAMPLES}
    assert kinds == {"rect_to_round", "rect_reducer", "rect_offset"}


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_matches_expected_manifest(build_mod, name, tmp_path):
    expected = json.loads((SKILL / "examples" / name / "expected_manifest.json").read_text(encoding="utf-8"))
    got = build_mod.build(_spec(name), tmp_path)
    # Everything that does not depend on the CAD kernel version must match exactly.
    for key in ("skill", "skill_version", "inputs", "spec_sha256", "measures", "validation"):
        assert got[key] == expected[key], key
    assert [f["name"] for f in got["files"]] == [f["name"] for f in expected["files"]]
    if got["toolchain"] != expected["toolchain"]:
        pytest.skip(f"toolchain {got['toolchain']} differs from the one the checksums were taken with "
                    f"{expected['toolchain']}; regenerate expected_manifest.json after reviewing the change")
    assert got["files"] == expected["files"]
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize("name", EXAMPLES)
def test_same_spec_gives_identical_bytes_across_hash_seeds(name, tmp_path):
    """The DXF section order follows set iteration, so it used to change with PYTHONHASHSEED (review round 1, B1):
    eight seeds are enough to hit both orders of the two CLASSES that vary."""
    spec = SKILL / "examples" / name / "spec.json"
    outs = []
    for seed in map(str, range(8)):
        out = tmp_path / f"seed{seed}"
        r = _run_cli(spec, out, seed)
        assert r.returncode == 0, r.stderr
        outs.append({p.name: p.read_bytes() for p in sorted(out.iterdir())})
    assert all(o == outs[0] for o in outs)


def test_failed_validator_releases_nothing(build_mod, tmp_path, monkeypatch):
    real = build_mod._load_validator()

    class Bad:
        def validate(self, spec, files):
            res = real.validate(spec, files)
            res.passed = False
            res.checks[0]["passed"] = False
            return res

    monkeypatch.setattr(build_mod, "_load_validator", lambda: Bad())
    out = tmp_path / "out"
    with pytest.raises(build_mod.ValidationFailed):
        build_mod.build(_spec("rect_reducer"), out)
    assert not out.exists() or not any(out.iterdir())


def test_cli_exit_codes(tmp_path):
    bad = tmp_path / "bad.json"
    spec = _spec("rect_offset")
    spec["geometry"]["offset_x_mm"] = 0
    spec["geometry"]["offset_y_mm"] = 0
    bad.write_text(json.dumps(spec), encoding="utf-8")
    r = _run_cli(bad, tmp_path / "o", "0")
    assert r.returncode == 2 and "straight duct" in r.stderr
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize(
    "mutate, text",
    [
        (lambda s: s.pop("sheet_thickness_mm"), "sheet_thickness_mm"),
        (lambda s: s["seam"].pop("allowance_mm"), "allowance_mm"),
        (lambda s: s["geometry"].update(diameter_mm=-5), "diameter_mm"),
        (lambda s: s["geometry"].update(circle_segments=12), "circle_segments"),
        (lambda s: s.update(units="in"), "units"),
        (lambda s: s.update(mark="../evil"), "mark"),
    ],
)
def test_spec_card_rejects_bad_inputs(build_mod, mutate, text):
    spec = _spec("rect_to_round")
    mutate(spec)
    with pytest.raises(build_mod.SpecError, match=text):
        build_mod.normalise_spec(spec)


def test_reducer_with_same_size_ends_is_rejected(build_mod):
    spec = _spec("rect_reducer")
    spec["geometry"].update(width_out_mm=600, height_out_mm=400)
    with pytest.raises(build_mod.SpecError, match="not a reducer"):
        build_mod.normalise_spec(spec)


def test_oversized_allowance_is_either_refused_or_emitted_validated(build_mod, tmp_path):
    spec = _spec("rect_offset")
    spec["connection"]["allowance_mm"] = 200
    spec["geometry"]["length_mm"] = 30
    out = tmp_path / "o"
    try:
        manifest = build_mod.build(spec, out)
    except (build_mod.SpecError, build_mod.ValidationFailed):
        assert not out.exists() or not any(out.iterdir())        # refused: nothing was written
    else:                                                         # built: only because the validator passed it
        assert manifest["validation"]["passed"] is True
        assert sorted(p.name for p in out.iterdir()) == ["O-01.dxf", "O-01.step", "manifest.json"]


# ---- review round 1 fixes: degenerate specs, exit codes, partial output ---------------------------------------------
def test_circle_segments_given_as_a_float_is_an_integer(build_mod):
    spec = _spec("rect_to_round")
    spec["geometry"]["circle_segments"] = 16.0       # JSON Schema counts 16.0 as an integer
    n = build_mod.normalise_spec(spec)["geometry"]["circle_segments"]
    assert n == 16 and isinstance(n, int)


def test_tiny_dimensions_are_refused_by_the_spec_card(build_mod):
    spec = _spec("rect_to_round")
    spec["geometry"].update(width_mm=1e-6, height_mm=1e-6, diameter_mm=1e-6, length_mm=1e-6)
    spec["seam"]["allowance_mm"] = spec["connection"]["allowance_mm"] = 0
    with pytest.raises(build_mod.SpecError, match="width_mm"):
        build_mod.normalise_spec(spec)


def test_a_near_zero_offset_is_not_an_offset(build_mod):
    spec = _spec("rect_offset")
    spec["geometry"].update(offset_x_mm=1e-9, offset_y_mm=0)
    with pytest.raises(build_mod.SpecError, match="straight duct"):
        build_mod.normalise_spec(spec)


def test_a_reducer_whose_ends_differ_by_less_than_a_hundredth_of_a_millimetre_is_not_a_reducer(build_mod):
    spec = _spec("rect_reducer")
    spec["geometry"].update(width_out_mm=spec["geometry"]["width_in_mm"] + 1e-7,
                            height_out_mm=spec["geometry"]["height_in_mm"])
    with pytest.raises(build_mod.SpecError, match="not a reducer"):
        build_mod.normalise_spec(spec)


@pytest.mark.parametrize("mark", ["CON", "nul", "COM1", "lpt9"])
def test_reserved_windows_file_names_are_refused_as_marks(build_mod, mark):
    spec = _spec("rect_offset")
    spec["mark"] = mark
    with pytest.raises(build_mod.SpecError, match="reserved"):
        build_mod.normalise_spec(spec)


@pytest.mark.parametrize("field", ["material", "notes"])
def test_control_characters_are_refused_in_free_text(build_mod, field):
    spec = _spec("rect_offset")
    spec[field] = "GALV" + chr(0) + "x"
    with pytest.raises(build_mod.SpecError, match=field):
        build_mod.normalise_spec(spec)


def test_cli_exit_codes_for_unreadable_specs_and_unwritable_output(tmp_path):
    good = SKILL / "examples" / "rect_offset" / "spec.json"
    assert _run_cli(good, tmp_path / "ok", "0").returncode == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _run_cli(bad, tmp_path / "x", "0").returncode == 2
    assert _run_cli(tmp_path / "missing.json", tmp_path / "y", "0").returncode == 2
    blocker = tmp_path / "afile"
    blocker.write_text("occupied", encoding="utf-8")
    r = _run_cli(good, blocker, "0")             # --out names an existing file: the build cannot publish
    assert r.returncode == 4 and "build failed" in r.stderr
    assert blocker.read_text(encoding="utf-8") == "occupied"


def test_a_failed_publish_leaves_no_partial_output(tmp_path):
    good = SKILL / "examples" / "rect_offset" / "spec.json"
    out = tmp_path / "out"
    (out / "O-01.dxf").mkdir(parents=True)       # the DXF cannot be written: the STEP written before it must go too
    r = _run_cli(good, out, "0")
    assert r.returncode == 4, r.stderr
    assert sorted(p.name for p in out.iterdir()) == ["O-01.dxf"]
