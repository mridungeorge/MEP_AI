"""DXF reader: closed polylines on space-like layers become SpaceRecords (area from geometry).

The scale is never guessed. If $INSUNITS is unset or unsupported, no areas are emitted.

Stable metadata keys (read by ingest/health.py):
    units                 str      'mm' | 'cm' | 'm' | 'unitless' | 'unsupported(<code>)'
    scale_ok              bool     True only when $INSUNITS is mm, cm or m
    scale_factor_to_m     float|None  drawing units -> metres (None when scale_ok is False)
    scale_check           str      'ok' | 'implausible' | 'unknown'
                                   (largest drawing extent within 1 m .. 2 km; 'unknown' when no scale or no extents)
    extents_m             list[float]|None  [width, height] of the drawing in metres
    layer_names           list[str]   every layer in the layer table (sorted)
    space_layers          list[str]   layers matching the space-layer pattern that hold polylines (sorted)
    closed_polylines      int      closed LWPOLYLINEs on space layers (duplicates included)
    open_polylines        int      open LWPOLYLINEs on space layers
    duplicate_polylines   int      closed polylines with the same vertices as an earlier one (skipped)
    self_intersecting     int      closed polylines skipped because they cross themselves
    curved_polylines      int      closed polylines skipped because they contain arc segments (bulge)
    text_labels           int      TEXT and MTEXT entities in the drawing
    labelled_spaces       int      emitted spaces that got a name from a label inside their polygon
"""
import hashlib
import math
from collections.abc import Sequence
from pathlib import Path

import ezdxf
from ezdxf import bbox

from mep.ingest.records import IngestResult, SpaceRecord

DEFAULT_LAYER_PATTERNS = ("space", "room", "area", "zone")
UNIT_FACTORS = {4: ("mm", 0.001), 5: ("cm", 0.01), 6: ("m", 1.0)}
PLAUSIBLE_EXTENT_M = (1.0, 2000.0)
_ROUND = 6

Point = tuple[float, float]


MAX_BYTES = 200 * 1024 * 1024
MAX_VERTICES = 2000          # per polyline: the self-intersection test is quadratic


def refuse_dwg(path: str | Path) -> None:
    """DWG is a closed format; refuse it rather than parse it badly."""
    if Path(path).suffix.lower() == ".dwg":
        raise ValueError("DWG is not supported; request DXF or IFC")


def _shoelace(pts: Sequence[Point]) -> float:
    s = 0.0
    for (x1, y1), (x2, y2) in zip(pts, [*pts[1:], pts[0]], strict=True):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _point_in_polygon(p: Point, pts: Sequence[Point]) -> bool:
    x, y = p
    inside = False
    for (x1, y1), (x2, y2) in zip(pts, [*pts[1:], pts[0]], strict=True):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _orient(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(a: Point, b: Point, c: Point, d: Point) -> bool:
    d1, d2 = _orient(a, b, c), _orient(a, b, d)
    d3, d4 = _orient(c, d, a), _orient(c, d, b)
    return d1 * d2 < 0 and d3 * d4 < 0


def _self_intersects(pts: Sequence[Point]) -> bool:
    n = len(pts)
    segs = [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue  # adjacent through the closing segment
            if _segments_cross(*segs[i], *segs[j]):
                return True
    return False


def _vertex_key(pts: Sequence[Point]) -> tuple[Point, ...]:
    return tuple(sorted((round(x, _ROUND), round(y, _ROUND)) for x, y in pts))


def read_dxf(
    path: str | Path, layer_patterns: Sequence[str] = DEFAULT_LAYER_PATTERNS
) -> IngestResult:
    refuse_dwg(path)
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError(f"{path.name} is larger than {MAX_BYTES} bytes")
    data = path.read_bytes()
    res = IngestResult("dxf", path.name, hashlib.sha256(data).hexdigest())
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    code = int(doc.header.get("$INSUNITS", 0))
    if code in UNIT_FACTORS:
        units, factor = UNIT_FACTORS[code]
        scale_ok = True
    else:
        units = "unitless" if code == 0 else f"unsupported({code})"
        factor, scale_ok = None, False
        res.problems.append(
            f"drawing units are {units}; scale not known, no areas emitted (never guess a scale)"
        )

    pats = tuple(p.lower() for p in layer_patterns)
    layer_names = sorted(
        {layer.dxf.name for layer in doc.layers} | {e.dxf.layer for e in msp}
    )

    # Labels (any layer), in drawing units.
    labels: list[tuple[Point, str]] = []
    for e in msp.query("TEXT MTEXT"):
        text = e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text
        text = " ".join(str(text).split())
        if text:
            ins = e.dxf.insert
            labels.append(((float(ins.x), float(ins.y)), text))

    closed = opened = dups = crossing = curved = labelled = 0
    seen: set[tuple[Point, ...]] = set()
    space_layers: set[str] = set()
    for e in msp.query("LWPOLYLINE"):
        layer = e.dxf.layer
        if not any(p in layer.lower() for p in pats):
            continue
        space_layers.add(layer)
        pts = [(float(x), float(y)) for x, y, *_ in e.get_points("xyb")]
        has_bulge = any(abs(b) > 1e-12 for _, _, b in e.get_points("xyb"))
        is_closed = bool(e.closed) or (len(pts) > 2 and pts[0] == pts[-1])
        if not is_closed:
            opened += 1
            continue
        closed += 1
        if e.closed is False:
            pts = pts[:-1]  # drop the repeated closing vertex
        if len(pts) < 3:
            res.problems.append(f"polyline {e.dxf.handle}: fewer than 3 vertices, skipped")
            continue
        if len(pts) > MAX_VERTICES:
            res.problems.append(f"polyline {e.dxf.handle}: more than {MAX_VERTICES} vertices, skipped")
            continue
        key = _vertex_key(pts)
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        if has_bulge:
            curved += 1
            res.problems.append(f"polyline {e.dxf.handle}: arc segments not supported, skipped")
            continue
        if _self_intersects(pts):
            crossing += 1
            res.problems.append(f"polyline {e.dxf.handle}: self-intersecting, skipped")
            continue
        if not scale_ok or factor is None:
            continue
        area = _shoelace(pts) * factor * factor
        if area <= 0.0 or not math.isfinite(area):
            res.problems.append(f"polyline {e.dxf.handle}: zero area, skipped")
            continue
        inside = [t for p, t in labels if _point_in_polygon(p, pts)]
        notes = ["area from polyline geometry"]
        if len(inside) > 1:
            notes.append(f"{len(inside)} labels inside; first used: {inside}")
        if inside:
            labelled += 1
        res.spaces.append(SpaceRecord(
            key=e.dxf.handle, name=inside[0] if inside else None, area_m2=area,
            source_kind="dxf", notes=tuple(notes)))

    # Scale sanity: drawing extents against a plausible building size.
    extents_m: list[float] | None = None
    scale_check = "unknown"
    try:
        box = bbox.extents(msp)
    except Exception:  # noqa: BLE001 - odd entities must not stop ingest
        box = None
    if box is not None and box.has_data and scale_ok and factor is not None:
        extents_m = [box.size.x * factor, box.size.y * factor]
        lo, hi = PLAUSIBLE_EXTENT_M
        scale_check = "ok" if lo <= max(extents_m) <= hi else "implausible"
        if scale_check == "implausible":
            res.problems.append(
                f"drawing extent {max(extents_m):.4g} m is outside {lo:g} m .. {hi:g} m; "
                "check the drawing units; no areas emitted"
            )
            res.spaces.clear()   # wrong-scale areas must not reach Gate 1 looking normal
            labelled = 0

    res.metadata.update({
        "units": units,
        "scale_ok": scale_ok,
        "scale_factor_to_m": factor,
        "scale_check": scale_check,
        "extents_m": extents_m,
        "layer_names": layer_names,
        "space_layers": sorted(space_layers),
        "closed_polylines": closed,
        "open_polylines": opened,
        "duplicate_polylines": dups,
        "self_intersecting": crossing,
        "curved_polylines": curved,
        "text_labels": len(labels),
        "labelled_spaces": labelled,
    })
    return res
