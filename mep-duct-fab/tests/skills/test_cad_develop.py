"""Pure-Python tests for skills/cad/develop.py (no CAD kernel needed)."""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

from skills.cad import develop

BUILD = Path(__file__).resolve().parents[2] / "skills" / "duct-fab" / "scripts" / "build.py"


@pytest.fixture(scope="module")
def build_mod():
    pytest.importorskip("jsonschema")
    spec = importlib.util.spec_from_file_location("duct_fab_build", BUILD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tubes(m):
    return {
        "round": m.rect_to_round_tube(
            {"width_mm": 600, "height_mm": 400, "diameter_mm": 300, "length_mm": 450,
             "offset_x_mm": 0, "offset_y_mm": 0, "circle_segments": 16}),
        "round_off": m.rect_to_round_tube(
            {"width_mm": 500, "height_mm": 500, "diameter_mm": 315, "length_mm": 400,
             "offset_x_mm": 100, "offset_y_mm": 0, "circle_segments": 24}),
        "reducer": m.frustum_tube(600, 400, 400, 300, 500, 0, 50),
        "offset": m.frustum_tube(500, 300, 500, 300, 600, 150, 100),
    }


@pytest.mark.parametrize("name", ["round", "round_off", "reducer", "offset"])
def test_every_developed_edge_has_its_3d_length(build_mod, name):
    tube = _tubes(build_mod)[name]
    pos = develop.unroll(tube)
    for face in tube.faces:
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            assert math.dist(pos[a], pos[b]) == pytest.approx(develop.dist3(tube.xyz(a), tube.xyz(b)), abs=1e-6)


@pytest.mark.parametrize("name", ["round", "round_off", "reducer", "offset"])
def test_developed_area_equals_surface_area(build_mod, name):
    tube = _tubes(build_mod)[name]
    pos = develop.unroll(tube)
    net, _ = develop.net_outline(tube, pos)
    area3d = sum(develop.face_area([tube.xyz(label) for label in f]) for f in tube.faces)
    assert abs(develop.signed_area(net)) == pytest.approx(area3d, rel=1e-9)
    assert develop.self_intersections(net) == []


@pytest.mark.parametrize("name", ["round", "round_off", "reducer", "offset"])
def test_faces_are_outward_and_form_a_closed_strip(build_mod, name):
    tube = _tubes(build_mod)[name]
    # every face shares exactly one edge with the previous one
    for a, b in zip(tube.faces, tube.faces[1:]):
        assert len(set(a) & set(b)) == 2
    # the strip is closed in 3D: the primed seam copies are the same points as the unprimed ones
    assert develop.base(tube.inlet[0]) == develop.base(tube.inlet[-1])
    assert develop.base(tube.outlet[0]) == develop.base(tube.outlet[-1])


def test_outline_offset_moves_edges_by_the_allowance():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    out = develop.offset_outline(square, [1.0, 2.0, 3.0, 0.0])
    # edge 0 (bottom) moved down by 1, edge 1 (right) moved right by 2, edge 2 (top) up by 3, edge 3 (left) stays
    assert min(y for _, y in out) == pytest.approx(-1.0)
    assert max(x for x, _ in out) == pytest.approx(12.0)
    assert max(y for _, y in out) == pytest.approx(13.0)
    assert min(x for x, _ in out) == pytest.approx(0.0)


def test_self_intersection_detects_a_bowtie():
    assert develop.self_intersections([(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)])
