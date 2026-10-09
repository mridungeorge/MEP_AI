"""Write the two synthetic architect plans the demo walkthrough uploads (docs/demo-assets/office-rev-a.dxf and office-rev-b.dxf).

Revision A: four named rooms. Revision B (the architect's next issue): the open-plan office is wider and a new meeting room is added.
Plain DXF (metres), one closed polyline per room on layer A-SPACE with a text label inside it that names the room. Made up, not a real building.

    uv run python scripts/make_demo_models.py
"""
from pathlib import Path

import ezdxf

OUT = Path(__file__).resolve().parents[1] / "docs" / "demo-assets"
# (label, x, y, width, depth) in metres
REV_A = [("Open plan office", 0, 0, 12, 9), ("Meeting room 1", 13, 0, 5, 4), ("Server room", 13, 5, 4, 3), ("Reception", 19, 0, 6, 5)]
REV_B = [("Open plan office", 0, 0, 15, 9), ("Meeting room 1", 16, 0, 5, 4), ("Server room", 16, 5, 4, 3), ("Reception", 22, 0, 6, 5),
         ("Meeting room 2", 22, 6, 5, 3)]


def write(rooms: list[tuple[str, float, float, float, float]], name: str) -> None:
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6                     # metres
    msp = doc.modelspace()
    for label, x, y, w, d in rooms:
        msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + d), (x, y + d)], close=True, dxfattribs={"layer": "A-SPACE"})
        msp.add_text(label, dxfattribs={"insert": (x + 0.4, y + 0.8), "height": 0.3})
    OUT.mkdir(parents=True, exist_ok=True)
    doc.saveas(OUT / name)


if __name__ == "__main__":
    write(REV_A, "office-rev-a.dxf")
    write(REV_B, "office-rev-b.dxf")
    print(f"wrote {OUT}/office-rev-a.dxf and office-rev-b.dxf")
