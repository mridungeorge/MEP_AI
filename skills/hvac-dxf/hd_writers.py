"""hvac-dxf writer: layout or schematic DXF from a normalised spec. Deterministic; geometry and text only, no compliance."""
import math
import sys
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent
APPID = "MEPHVAC"
DEFAULT_LAYERS = {"duct_rect": "M-DUCT-RECT", "duct_round": "M-DUCT-ROUND", "terminal": "M-TERMINAL", "tag": "M-ANNO-TAG", "frame": "M-ANNO-FRAME", "title": "M-ANNO-TITB"}
LAYER_COLOURS = {"duct_rect": 5, "duct_round": 4, "terminal": 3, "tag": 7, "frame": 8, "title": 7}
TEXT_HEIGHT = 100.0
TERMINAL_RADIUS = 150.0
FRAME_MARGIN = 800.0
TITLE_LINES = (("project", "PROJECT"), ("drawing_title", "TITLE"), ("drawing_no", "DRAWING NO"), ("revision", "REVISION"), ("date", "DATE"), ("drawn_by", "DRAWN BY"),
               ("firm", "FIRM"))


def size_text(size: dict[str, Any]) -> str:
    return f"{size['width_mm']:g}x{size['depth_mm']:g}" if size["shape"] == "rect" else f"dia{size['diameter_mm']:g}"


def duct_tag_text(tag: str, size: dict[str, Any], airflow_ls: float) -> str:
    return f"{tag} {size_text(size)} {airflow_ls:g} L/s"


def terminal_tag_text(tag: str, airflow_ls: float) -> str:
    return f"{tag} {airflow_ls:g} L/s"


def layer_names(spec: dict[str, Any]) -> dict[str, str]:
    return {**DEFAULT_LAYERS, **(spec.get("layers") or {})}


def duct_polygon(start: tuple[float, float], end: tuple[float, float], width: float) -> list[tuple[float, float]]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    nx, ny = -dy / length * width / 2.0, dx / length * width / 2.0
    return [(start[0] + nx, start[1] + ny), (end[0] + nx, end[1] + ny), (end[0] - nx, end[1] - ny), (start[0] - nx, start[1] - ny)]


def duct_width(size: dict[str, Any]) -> float:
    return float(size["width_mm"]) if size["shape"] == "rect" else float(size["diameter_mm"])


def extents(spec: dict[str, Any]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for d in spec["ducts"]:
        w = duct_width(d["size"])
        for p in (d["start_mm"], d["end_mm"]):
            xs += [p[0] - w, p[0] + w]
            ys += [p[1] - w, p[1] + w]
    for t in spec.get("terminals", []):
        xs += [t["at_mm"][0] - 2 * TERMINAL_RADIUS, t["at_mm"][0] + 2 * TERMINAL_RADIUS]
        ys += [t["at_mm"][1] - 2 * TERMINAL_RADIUS, t["at_mm"][1] + 2 * TERMINAL_RADIUS]
    return min(xs), min(ys), max(xs), max(ys)


def build_dxf(spec: dict[str, Any], path: Path) -> None:
    import ezdxf

    repo_root = str(SKILL_DIR.parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from skills.cad import cadkit

    r6 = cadkit.r6
    names = layer_names(spec)
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    for role, name in names.items():
        if name not in doc.layers:
            doc.layers.add(name, color=LAYER_COLOURS[role])
    doc.appids.add(APPID)
    blk = doc.blocks.new("TERMINAL")
    blk.add_circle((0, 0), TERMINAL_RADIUS)
    blk.add_line((-TERMINAL_RADIUS, 0), (TERMINAL_RADIUS, 0))
    blk.add_line((0, -TERMINAL_RADIUS), (0, TERMINAL_RADIUS))
    msp = doc.modelspace()

    def tagged(entity: Any, role: str, tag: str) -> None:
        entity.set_xdata(APPID, [(1000, role), (1000, tag)])

    for d in spec["ducts"]:
        s, e = (r6(d["start_mm"][0]), r6(d["start_mm"][1])), (r6(d["end_mm"][0]), r6(d["end_mm"][1]))
        w = duct_width(d["size"])
        layer = names["duct_rect"] if d["size"]["shape"] == "rect" else names["duct_round"]
        if spec["mode"] == "layout":
            ent = msp.add_lwpolyline([(r6(x), r6(y)) for x, y in duct_polygon(s, e, w)], close=True, dxfattribs={"layer": layer})
        else:
            ent = msp.add_line(s, e, dxfattribs={"layer": layer})
        tagged(ent, "duct", d["tag"])
        mid = ((s[0] + e[0]) / 2.0, (s[1] + e[1]) / 2.0)
        length = math.hypot(e[0] - s[0], e[1] - s[1])
        nx, ny = -(e[1] - s[1]) / length, (e[0] - s[0]) / length
        off = (w / 2.0 if spec["mode"] == "layout" else 0.0) + TEXT_HEIGHT
        angle = math.degrees(math.atan2(e[1] - s[1], e[0] - s[0]))
        if angle > 90 or angle <= -90:
            angle += 180
        txt = msp.add_text(duct_tag_text(d["tag"], d["size"], d["airflow_ls"]),
                           dxfattribs={"layer": names["tag"], "height": TEXT_HEIGHT, "rotation": r6(angle), "insert": (r6(mid[0] + nx * off), r6(mid[1] + ny * off))})
        tagged(txt, "duct_tag", d["tag"])
    for t in spec.get("terminals", []):
        at = (r6(t["at_mm"][0]), r6(t["at_mm"][1]))
        ins = msp.add_blockref("TERMINAL", at, dxfattribs={"layer": names["terminal"]})
        tagged(ins, "terminal", t["tag"])
        txt = msp.add_text(terminal_tag_text(t["tag"], t["airflow_ls"]),
                           dxfattribs={"layer": names["tag"], "height": TEXT_HEIGHT, "insert": (r6(at[0] + TERMINAL_RADIUS * 1.3), r6(at[1] - TEXT_HEIGHT / 2.0))})
        tagged(txt, "terminal_tag", t["tag"])
    x0, y0, x1, y1 = extents(spec)
    x0, y0, x1, y1 = x0 - FRAME_MARGIN, y0 - FRAME_MARGIN, x1 + FRAME_MARGIN, y1 + FRAME_MARGIN
    msp.add_lwpolyline([(r6(x0), r6(y0)), (r6(x1), r6(y0)), (r6(x1), r6(y1)), (r6(x0), r6(y1))], close=True, dxfattribs={"layer": names["frame"]})
    lines = [(label, spec["title_block"][key]) for key, label in TITLE_LINES if spec["title_block"].get(key)]
    height = TEXT_HEIGHT * 1.8
    box_w, box_h = 4200.0, height * (len(lines) + 1)
    bx, by = x1 - box_w, y0
    msp.add_lwpolyline([(r6(bx), r6(by)), (r6(x1), r6(by)), (r6(x1), r6(by + box_h)), (r6(bx), r6(by + box_h))], close=True, dxfattribs={"layer": names["title"]})
    for i, (label, value) in enumerate(reversed(lines)):
        t = msp.add_text(f"{label}: {value}", dxfattribs={"layer": names["title"], "height": TEXT_HEIGHT, "insert": (r6(bx + 100), r6(by + height * (i + 0.5)))})
        tagged(t, "title", label)
    cadkit.save_dxf(doc, path)
