"""DXF reader: areas only from a known unit scale, never guessed."""
from pathlib import Path

import ezdxf
import pytest
from mep.ingest.dxf import read_dxf, refuse_dwg

SQUARE_M = [(0, 0), (5, 0), (5, 4), (0, 4)]


def _save(tmp_path: Path, build, insunits: int, name: str = "t.dxf") -> Path:
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = insunits
    build(doc.modelspace())
    path = tmp_path / name
    doc.saveas(path)
    return path


def _poly(msp, pts, layer="A-SPACE", close=True):
    return msp.add_lwpolyline(pts, close=close, dxfattribs={"layer": layer})


def test_square_in_metres(tmp_path):
    path = _save(tmp_path, lambda m: _poly(m, SQUARE_M), 6)
    res = read_dxf(path)
    assert res.source_kind == "dxf"
    assert len(res.spaces) == 1
    sp = res.spaces[0]
    assert sp.area_m2 == pytest.approx(20.0)
    assert sp.provenance == "extracted" and sp.source_kind == "dxf"
    assert sp.key
    assert res.metadata["units"] == "m"
    assert res.metadata["scale_ok"] is True
    assert res.metadata["scale_check"] == "ok"
    assert res.metadata["closed_polylines"] == 1
    assert res.metadata["open_polylines"] == 0
    assert len(res.source_sha256) == 64


def test_same_drawing_in_mm(tmp_path):
    pts = [(x * 1000, y * 1000) for x, y in SQUARE_M]
    path = _save(tmp_path, lambda m: _poly(m, pts), 4)
    res = read_dxf(path)
    assert res.metadata["units"] == "mm"
    assert res.spaces[0].area_m2 == pytest.approx(20.0)
    assert res.metadata["scale_check"] == "ok"


def test_cm_supported(tmp_path):
    pts = [(x * 100, y * 100) for x, y in SQUARE_M]
    res = read_dxf(_save(tmp_path, lambda m: _poly(m, pts), 5))
    assert res.spaces[0].area_m2 == pytest.approx(20.0)


def test_no_insunits_emits_no_areas(tmp_path):
    path = _save(tmp_path, lambda m: _poly(m, SQUARE_M), 0)
    res = read_dxf(path)
    assert res.spaces == []
    assert res.metadata["scale_ok"] is False
    assert res.metadata["scale_check"] == "unknown"
    assert res.problems


def test_unsupported_units_emit_no_areas(tmp_path):
    res = read_dxf(_save(tmp_path, lambda m: _poly(m, SQUARE_M), 1))  # inches
    assert res.spaces == []
    assert res.metadata["scale_ok"] is False
    assert any("unit" in p.lower() for p in res.problems)


def test_open_polyline_counted_not_emitted(tmp_path):
    def build(m):
        _poly(m, SQUARE_M)
        _poly(m, [(10, 0), (15, 0), (15, 4)], close=False)

    res = read_dxf(_save(tmp_path, build, 6))
    assert len(res.spaces) == 1
    assert res.metadata["closed_polylines"] == 1
    assert res.metadata["open_polylines"] == 1


def test_first_equals_last_vertex_is_closed(tmp_path):
    pts = [*SQUARE_M, SQUARE_M[0]]
    res = read_dxf(_save(tmp_path, lambda m: _poly(m, pts, close=False), 6))
    assert len(res.spaces) == 1
    assert res.spaces[0].area_m2 == pytest.approx(20.0)
    assert res.metadata["open_polylines"] == 0


def test_duplicate_polyline_emitted_once(tmp_path):
    def build(m):
        _poly(m, SQUARE_M)
        _poly(m, SQUARE_M)

    res = read_dxf(_save(tmp_path, build, 6))
    assert len(res.spaces) == 1
    assert res.metadata["duplicate_polylines"] == 1


def test_label_inside_polygon_names_space(tmp_path):
    def build(m):
        _poly(m, SQUARE_M)
        m.add_text("Office 1", dxfattribs={"layer": "A-TEXT", "insert": (2, 2)})
        m.add_text("Elsewhere", dxfattribs={"layer": "A-TEXT", "insert": (50, 50)})

    res = read_dxf(_save(tmp_path, build, 6))
    assert res.spaces[0].name == "Office 1"
    assert res.metadata["labelled_spaces"] == 1
    assert res.metadata["text_labels"] == 2


def test_mtext_label(tmp_path):
    def build(m):
        _poly(m, SQUARE_M)
        mt = m.add_mtext("Plant Room")
        mt.dxf.insert = (1, 1)

    assert read_dxf(_save(tmp_path, build, 6)).spaces[0].name == "Plant Room"


def test_other_layers_ignored_and_layer_names_recorded(tmp_path):
    def build(m):
        _poly(m, SQUARE_M, layer="WALLS")
        _poly(m, SQUARE_M, layer="Rooms")

    res = read_dxf(_save(tmp_path, build, 6))
    assert len(res.spaces) == 1
    assert {"WALLS", "Rooms"} <= set(res.metadata["layer_names"])
    assert res.metadata["space_layers"] == ["Rooms"]


def test_custom_layer_pattern(tmp_path):
    res = read_dxf(_save(tmp_path, lambda m: _poly(m, SQUARE_M, layer="WALLS"), 6),
                   layer_patterns=("wall",))
    assert len(res.spaces) == 1


def test_self_intersecting_skipped_with_problem(tmp_path):
    bow = [(0, 0), (4, 4), (4, 0), (0, 4)]
    res = read_dxf(_save(tmp_path, lambda m: _poly(m, bow), 6))
    assert res.spaces == []
    assert res.metadata["self_intersecting"] == 1
    assert any("self-intersect" in p for p in res.problems)


def test_scale_check_flags_implausible_extent(tmp_path):
    # A 5 x 4 drawing in mm is a 5 mm building: implausible.
    res = read_dxf(_save(tmp_path, lambda m: _poly(m, SQUARE_M), 4))
    assert res.metadata["scale_check"] == "implausible"
    assert res.problems


def test_dwg_refused(tmp_path):
    p = tmp_path / "plan.DWG"
    p.write_bytes(b"AC1027")
    with pytest.raises(ValueError, match="DWG is not supported; request DXF or IFC"):
        refuse_dwg(p)
    with pytest.raises(ValueError, match="DWG is not supported; request DXF or IFC"):
        read_dxf(p)
