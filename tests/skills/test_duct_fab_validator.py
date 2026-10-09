"""duct-fab validator: one known-good build per fitting must pass every check; mutated builds must fail the named check.

Run inside the CAD image (cadquery + ezdxf); skipped where those are not installed.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cadquery")
pytest.importorskip("ezdxf")

from tests.skills import _duct_fab_mutations as mut

CHECK_KEYS = {"name", "passed", "expected", "actual", "tolerance"}
ALL_CHECKS = {
    "input_files", "spec_readable", "step_solid", "step_planar_faces", "step_length", "step_vertex_count",
    "step_inlet_cap", "step_outlet_cap", "step_outlet_offset", "step_bbox_xy", "dxf_loads", "dxf_layers",
    "cut_closed_single", "cut_no_zero_length", "cut_no_self_intersection", "net_connected",
    "net_no_self_intersection", "edge_lengths_match_3d", "net_area_matches_lateral_area", "net_inlet_perimeter",
    "net_outlet_perimeter", "seam_allowance", "connection_allowance", "title_block",
}


@pytest.fixture(scope="session")
def validator() -> Any:
    return mut.load_module("duct_fab_validator_under_test", mut.SKILL_DIR / "validator.py")


@pytest.fixture(scope="session")
def build_fn() -> Any:
    return mut.load_module("duct_fab_build_under_test", mut.SKILL_DIR / "scripts" / "build.py").build


@pytest.fixture(scope="session")
def good_builds(tmp_path_factory: pytest.TempPathFactory, build_fn: Any) -> dict[str, dict[str, Path]]:
    cache: dict[str, dict[str, Path]] = {}
    for key, spec in {**mut.GOOD_SPECS, **mut.EXTRA_GOOD_SPECS}.items():
        out = tmp_path_factory.mktemp(f"good_{key}")
        build_fn(copy.deepcopy(spec), out)
        cache[key] = mut.find_outputs(out)
    return cache


def failed(result: Any) -> set[str]:
    return {c["name"] for c in result.checks if not c["passed"]}


def describe(result: Any) -> str:
    return "\n".join(f"{c['name']}: expected={c['expected']} actual={c['actual']}" for c in result.checks
                     if not c["passed"])


# ------------------------------------------------------------------ known good
@pytest.mark.parametrize("key", [*mut.GOOD_SPECS, *mut.EXTRA_GOOD_SPECS])
def test_known_good_passes_every_check(validator: Any, good_builds: Any, key: str) -> None:
    spec = {**mut.GOOD_SPECS, **mut.EXTRA_GOOD_SPECS}[key]
    b = good_builds[key]
    res = validator.validate(spec, [b["step"], b["dxf"], b["manifest"]])
    assert res.passed, describe(res)
    names = {c["name"] for c in res.checks}
    assert ALL_CHECKS | {"manifest"} <= names
    for c in res.checks:
        assert set(c) == CHECK_KEYS
        assert c["passed"] is True


def test_known_good_without_manifest(validator: Any, good_builds: Any) -> None:
    b = good_builds["rect_to_round"]
    res = validator.validate(mut.GOOD_SPECS["rect_to_round"], [b["step"], b["dxf"]])
    assert res.passed, describe(res)
    assert "manifest" not in {c["name"] for c in res.checks}


# ------------------------------------------------------------------ known bad
BAD_CASES = [(k, m) for k in mut.GOOD_SPECS for m in mut.MUTATIONS]


@pytest.mark.parametrize(("key", "mutation"), BAD_CASES, ids=[f"{k}-{m}" for k, m in BAD_CASES])
def test_known_bad_fails_named_check(
    validator: Any, build_fn: Any, good_builds: Any, tmp_path: Path, key: str, mutation: str
) -> None:
    spec = mut.GOOD_SPECS[key]
    env = mut.Env(spec=spec, key=key, good=good_builds[key], work=tmp_path, build_fn=build_fn)
    m = mut.MUTATIONS[mutation]
    files = m.fn(env)
    res = validator.validate(copy.deepcopy(spec), files)
    must = set(m.must_fail)
    if mutation == "step_other_dimension":
        must = {mut.DIMENSION_CHANGE[key][2]}
    assert not res.passed
    assert must <= failed(res), f"expected {must} to fail; failed={failed(res)}\n{describe(res)}"


def test_each_fitting_has_at_least_four_distinct_bad_cases() -> None:
    assert len({m for _, m in BAD_CASES if _ == "rect_offset"}) >= 4
    assert set(mut.GOOD_SPECS) == {"rect_to_round", "rect_to_round_offset", "rect_reducer", "rect_offset"}


def test_bad_case_only_fails_what_it_should(validator: Any, build_fn: Any, good_builds: Any, tmp_path: Path) -> None:
    """A single localised defect must not fail unrelated checks (guards against a validator that fails everything)."""
    spec = mut.GOOD_SPECS["rect_reducer"]
    env = mut.Env(spec=spec, key="rect_reducer", good=good_builds["rect_reducer"], work=tmp_path, build_fn=build_fn)
    res = validator.validate(spec, mut.cut_open(env))
    assert failed(res) == {"cut_closed_single"}


# ------------------------------------------------------------------ robustness
def test_never_raises_on_garbage(validator: Any, good_builds: Any, tmp_path: Path) -> None:
    spec = mut.GOOD_SPECS["rect_to_round"]
    junk_step = tmp_path / "x.step"
    junk_step.write_bytes(bytes(range(256)) * 10)
    junk_dxf = tmp_path / "x.dxf"
    junk_dxf.write_bytes(b"0\nSECTION\n2\nENTITIES\n")
    junk_json = tmp_path / "manifest.json"
    junk_json.write_text("{not json", encoding="utf-8")
    good = good_builds["rect_to_round"]
    cases: list[tuple[dict[str, Any], list[Path]]] = [
        (spec, [junk_step, junk_dxf]),
        (spec, [junk_step, junk_dxf, junk_json]),
        (spec, []),
        (spec, [tmp_path / "missing.step", tmp_path / "missing.dxf"]),
        (spec, [good["step"], good["step"], good["dxf"]]),
        (spec, [good["dxf"]]),
        (spec, [good["step"], good["dxf"], tmp_path / "notes.txt"]),
        ({}, [good["step"], good["dxf"]]),
        ({"fitting": "rect_to_round", "geometry": {}}, [good["step"], good["dxf"]]),
        ({"fitting": "nope"}, [good["step"], good["dxf"]]),
        (spec, [tmp_path]),
    ]
    for sp, files in cases:
        res = validator.validate(sp, files)
        assert res.passed is False
        assert res.checks
        assert all(set(c) == CHECK_KEYS for c in res.checks)


def test_wrong_spec_against_good_files_fails(validator: Any, good_builds: Any) -> None:
    """Good files validated against a different card (other fitting size) must be rejected."""
    b = good_builds["rect_offset"]
    other = copy.deepcopy(mut.GOOD_SPECS["rect_offset"])
    other["geometry"]["width_mm"] = 520
    res = validator.validate(other, [b["step"], b["dxf"]])
    assert not res.passed
    assert "step_inlet_cap" in failed(res)
    assert "step_outlet_cap" in failed(res)
