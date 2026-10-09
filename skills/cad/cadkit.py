"""CadQuery / ezdxf helpers that make deterministic files. No network, no database, no LLM.

Determinism rules (the lesson taken from upstream text-to-cad, see NOTICE):
- build every face from an explicit ordered vertex list, never from a set of shapes (OCCT orders sets by heap address);
- round coordinates before they reach the kernel or the DXF writer;
- strip the clock and temp paths from STEP headers and the GUIDs/timestamps from DXF headers.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from skills.cad.develop import Tube, base

STEP_TIMESTAMP = "2000-01-01T00:00:00"
DXF_APPID = "MEPFAB"
LAYERS = {"CUT": 1, "BEND": 5, "ANNOTATION": 3}  # name -> ACI colour


def r6(x: float) -> float:
    """Round to 1e-6 mm and drop negative zero, so equal geometry writes equal text."""
    return round(x, 6) + 0.0


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------- STEP
def _cq() -> Any:
    import cadquery as cq  # imported lazily so develop.py and pure-Python tests need no CAD kernel

    return cq


def solid_from_tube(tube: Tube) -> Any:
    """A closed polyhedral solid: the tube's side faces plus planar inlet and outlet caps."""
    cq = _cq()
    verts = {k: cq.Vector(*(r6(c) for c in v)) for k, v in tube.vertices.items()}

    def face(labels: tuple[str, ...]) -> Any:
        pts = [verts[base(label)] for label in labels]
        return cq.Face.makeFromWires(cq.Wire.makePolygon([*pts, pts[0]]))

    faces = [face(f) for f in tube.faces]
    for chain in (tube.inlet, tube.outlet):
        labels = tuple(dict.fromkeys(base(label) for label in chain))  # drop the primed seam copy, keep order
        faces.append(face(labels))
    shell = cq.Shell.makeShell(faces)
    solid = cq.Solid.makeSolid(shell)
    if not solid.isValid():
        raise ValueError("polyhedral solid is not valid")
    if solid.Volume() <= 0:
        raise ValueError("polyhedral solid has non-positive volume")
    return solid


def write_step(shape: Any, path: Path, product_name: str) -> None:
    """Export a STEP file whose bytes depend only on the geometry (header clock and temp path removed)."""
    cq = _cq()
    path = Path(path)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / path.name
        cq.exporters.export(cq.Workplane(obj=shape), str(tmp), exportType="STEP")
        text = tmp.read_text(encoding="utf-8")
    text = re.sub(r"FILE_NAME\s*\(.*?\)\s*;", f"FILE_NAME('{product_name}.step','{STEP_TIMESTAMP}',(''),(''),'','','');",
                  text, count=1, flags=re.DOTALL)
    # OCCT numbers each export in a process ("... translator 7.9 3"); that counter is not geometry.
    text = re.sub(r"(Open CASCADE STEP translator \d+\.\d+) \d+", r"\1 1", text)
    write_atomic(path, text.replace("\r\n", "\n").encode("utf-8"))


# ---------------------------------------------------------------- DXF
def new_dxf() -> Any:
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # millimetres
    doc.header["$MEASUREMENT"] = 1
    for name, colour in LAYERS.items():
        doc.layers.add(name, color=colour)
    doc.layers.get("BEND").dxf.linetype = "DASHED"
    doc.appids.add(DXF_APPID)
    return doc


def save_dxf(doc: Any, path: Path) -> None:
    """Write with fixed GUIDs and timestamps so the same drawing gives the same bytes."""
    import ezdxf

    previous = ezdxf.options.write_fixed_meta_data_for_testing
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "out.dxf"
            doc.saveas(str(tmp))
            data = tmp.read_bytes()
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous
    write_atomic(Path(path), canonical_dxf(data.decode("utf-8")).encode("utf-8"))


_WRITTEN_BY = re.compile(r"(\d+\.\d+\.\d+) @ \d{4}-\d\d-\d\dT[\d:.]+\+00:00")




def canonical_dxf(text: str) -> str:
    """Remove what ezdxf leaves to chance: the 'written by' timestamp, and the order of the CLASSES and OBJECTS
    sections (both follow set iteration, so they change with PYTHONHASHSEED). CLASSES are put in name order and
    OBJECTS in handle order, so the same drawing always gives the same bytes."""
    text = _WRITTEN_BY.sub(r"\1 @ 2000-01-01T00:00:00+00:00", text.replace("\r\n", "\n"))
    lines = text.split("\n")
    trailing = lines[-1] == ""
    if trailing:
        lines = lines[:-1]
    pairs = [(lines[i], lines[i + 1]) for i in range(0, len(lines) - 1, 2)]

    def first_value(chunk: list[tuple[str, str]], code: str) -> str:
        return next((v for c, v in chunk if c.strip() == code), "")

    sort_keys: dict[str, Callable[[list[tuple[str, str]]], Any]] = {
        "CLASSES": lambda ch: (first_value(ch, "1"), first_value(ch, "2"), first_value(ch, "3")),
        "OBJECTS": lambda ch: int(first_value(ch, "5") or "0", 16),
    }
    for section, key in sort_keys.items():
        start = next((i for i, p in enumerate(pairs) if p[0].strip() == "2" and p[1] == section), None)
        if start is None:
            continue
        end = next(i for i in range(start, len(pairs)) if pairs[i][0].strip() == "0" and pairs[i][1] == "ENDSEC")
        chunks: list[list[tuple[str, str]]] = []
        for pair in pairs[start + 1 : end]:
            if pair[0].strip() == "0":
                chunks.append([])
            chunks[-1].append(pair)
        chunks.sort(key=key)  # stable: chunks with equal keys keep their relative order
        pairs = pairs[: start + 1] + [p for c in chunks for p in c] + pairs[end:]
    out = "\n".join(f"{a}\n{b}" for a, b in pairs)
    return out + ("\n" if trailing else "")
