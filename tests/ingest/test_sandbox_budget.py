"""The upload reader's limits: a small hostile file must be refused or read cheaply, and its output must stay small (no database)."""
import math
import time
from pathlib import Path

import ezdxf
import pytest
from mep.ingest.dxf import MAX_EXPANDED_ENTITIES, read_dxf
from mep.ingest.records import IngestRefused
from mep.ingest.sandbox import read_isolated

ROOT = Path(__file__).resolve().parents[2]
IFC = ROOT / "tests/fixtures/ifc/bsi-arch-ifc4.ifc"


def nested(doc, depth: int, fanout: int, prefix: str = "B"):
    block = doc.blocks.new(f"{prefix}0")
    block.add_line((0, 0), (1, 1))
    for i in range(1, depth):
        block = doc.blocks.new(f"{prefix}{i}")
        for _ in range(fanout):
            block.add_blockref(f"{prefix}{i - 1}", (0, 0))
    return f"{prefix}{depth - 1}"


def save(doc, tmp_path: Path, name: str = "t.dxf") -> Path:
    path = tmp_path / name
    doc.saveas(path)
    return path


def test_nested_block_references_are_refused_by_the_budget_not_expanded(tmp_path):
    doc = ezdxf.new("R2018")
    doc.modelspace().add_blockref(nested(doc, 8, 10), (0, 0))
    started = time.monotonic()
    with pytest.raises(IngestRefused, match="nested block references"):
        read_dxf(save(doc, tmp_path))
    assert time.monotonic() - started < 5
    assert MAX_EXPANDED_ENTITIES < 10 ** 7


def test_a_dimension_block_bomb_costs_nothing_because_extents_never_expand_blocks(tmp_path):
    """Round 2 finding: a DIMENSION's geometry block full of nested INSERTs was expanded by the extents computation."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (6, 0), (6, 5), (0, 5)], close=True, dxfattribs={"layer": "A-SPACE"})
    dim = msp.add_linear_dim(base=(0, -2), p1=(0, 0), p2=(6, 0))
    dim.render()
    doc.blocks.get(dim.dimension.dxf.geometry).add_blockref(nested(doc, 9, 20, "N"), (0, 0))
    started = time.monotonic()
    res = read_isolated("dxf", save(doc, tmp_path))
    assert time.monotonic() - started < 20
    assert len(res.spaces) == 1 and res.spaces[0].area_m2 == pytest.approx(30.0)


def test_many_long_labels_do_not_blow_up_the_output(tmp_path):
    """Round 2 finding: every space's notes listed every label inside it (hundreds of MB of notes from one MB of DXF)."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    for k in range(300):
        msp.add_lwpolyline([(k * 0.01, 0), (10 + k * 0.01, 0), (10 + k * 0.01, 10), (k * 0.01, 10)], close=True,
                           dxfattribs={"layer": "SPACE"})
    for i in range(1500):
        msp.add_text("L" * 400, dxfattribs={"insert": (5, 5), "height": 0.1})
    path = save(doc, tmp_path)
    started = time.monotonic()
    res = read_isolated("dxf", path)
    assert time.monotonic() - started < 40
    assert res.spaces and all(len("; ".join(s.notes)) < 400 for s in res.spaces)
    assert all(len(s.name or "") <= 200 for s in res.spaces)


def test_output_larger_than_the_cap_is_refused(tmp_path):
    with pytest.raises(IngestRefused, match="too complex"):
        read_isolated("ifc", IFC, max_output=500)


def test_the_sandbox_stops_a_reader_that_runs_too_long_or_uses_too_much_cpu(tmp_path):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    for k in range(40):       # within the budget, but about a second each in the self-intersection test
        pts = [(100 * k + 40 * math.cos(2 * math.pi * i / 1999), 40 * math.sin(2 * math.pi * i / 1999)) for i in range(1999)]
        doc.modelspace().add_lwpolyline(pts, close=True, dxfattribs={"layer": "A-SPACE"})
    path = save(doc, tmp_path, "slow.dxf")
    started = time.monotonic()
    with pytest.raises(IngestRefused, match="took longer"):
        read_isolated("dxf", path, wall_seconds=2)
    with pytest.raises(IngestRefused, match="more processing time"):
        read_isolated("dxf", path, cpu_seconds=1, wall_seconds=60)
    assert time.monotonic() - started < 30


def test_the_sandbox_reads_a_normal_file_identically_and_hides_internals(tmp_path):
    from mep.ingest.ifc import read_ifc
    direct, isolated = read_ifc(IFC), read_isolated("ifc", IFC)
    assert [(s.key, s.name, s.area_m2, s.centroid_m) for s in isolated.spaces] == \
        [(s.key, s.name, s.area_m2, s.centroid_m) for s in direct.spaces]
    assert isolated.metadata == direct.metadata and isolated.source_sha256 == direct.source_sha256
    bad = tmp_path / "bad.ifc"
    bad.write_bytes(b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n#1=GARBAGE(((;\nENDSEC;\nEND-ISO-10303-21;\n")
    with pytest.raises(IngestRefused) as e:
        read_isolated("ifc", bad)
    assert "Traceback" not in str(e.value) and len(str(e.value)) < 120


def test_the_worker_does_not_inherit_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("MEP_DB_URL", "postgresql://secret")
    monkeypatch.setenv("MEP_JWT_SECRET", "s" * 40)
    import subprocess
    import sys

    from mep.ingest import sandbox
    seen: dict[str, str] = {}
    real = subprocess.run

    def spy(cmd, **kw):
        seen.update(kw["env"])
        return real(cmd, **kw)

    monkeypatch.setattr(sandbox.subprocess, "run", spy)
    read_isolated("ifc", IFC)
    assert "MEP_DB_URL" not in seen and "MEP_JWT_SECRET" not in seen and sys.executable
