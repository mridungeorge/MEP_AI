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
from typing import Any

import ezdxf
from ezdxf import bbox

from mep.ingest.records import IngestRefused, IngestResult, SpaceRecord

DEFAULT_LAYER_PATTERNS = ("space", "room", "area", "zone")
UNIT_FACTORS = {4: ("mm", 0.001), 5: ("cm", 0.01), 6: ("m", 1.0)}
PLAUSIBLE_EXTENT_M = (1.0, 2000.0)
_ROUND = 6

Point = tuple[float, float]


MAX_BYTES = 200 * 1024 * 1024
MAX_VERTICES = 2000          # per polyline: the self-intersection test is quadratic
# Complexity budget: a small file can still ask for hours of work (nested block references multiply, polygon x label tests
# are quadratic). Past any of these the drawing is refused with a message; the upload path also runs the reader in a
# sandboxed process with CPU, memory and wall-clock limits (ingest/sandbox.py).
MAX_MODEL_ENTITIES = 500_000       # entities directly in model space
MAX_EXPANDED_ENTITIES = 2_000_000  # entities after expanding block references (INSERT) everywhere
MAX_SPACE_POLYLINES = 5_000        # closed polylines on space layers
MAX_LABELS = 2_000                 # TEXT / MTEXT entities (each is tested against each polygon)
MAX_LABEL_CHARS = 200              # a label longer than this is cut (it only names a room)
# The extents (a scale sanity check) are taken over plain geometry only. Block references, dimensions, leaders and tables are
# deliberately NOT expanded: expanding them is where a small file can ask for billions of entities.
_EXTENT_TYPES = frozenset({"LWPOLYLINE", "POLYLINE", "LINE", "ARC", "CIRCLE", "ELLIPSE", "POINT", "TEXT", "MTEXT", "SOLID"})
MAX_TOTAL_VERTICES = 100_000       # vertices over all space polylines


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


def _centroid(pts: Sequence[Point]) -> Point:
    """Area-weighted centroid of a simple polygon (falls back to the vertex mean for a degenerate one)."""
    a2 = cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(pts, [*pts[1:], pts[0]], strict=True):
        cross = x1 * y2 - x2 * y1
        a2 += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(a2) < 1e-12:
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    return cx / (3.0 * a2), cy / (3.0 * a2)


def _expanded_entity_count(doc: ezdxf.document.Drawing) -> int:
    """Entities in model space after expanding every block reference, counted once per block (memoised), so a nest of
    references that would multiply out to billions is measured, never expanded. A circular reference is refused."""
    memo: dict[str, int] = {}
    visiting: set[str] = set()

    def count(layout: Any) -> int:
        name = layout.name
        if name in memo:
            return memo[name]
        if name in visiting:
            raise IngestRefused("the drawing has a block that contains itself")
        visiting.add(name)
        total = 0
        for e in layout:
            total += 1
            if e.dxftype() == "INSERT":
                block = doc.blocks.get(e.dxf.name, None)
                if block is not None:
                    times = max(1, int(e.dxf.get("row_count", 1))) * max(1, int(e.dxf.get("column_count", 1)))
                    total += times * count(block)
            if total > MAX_EXPANDED_ENTITIES:
                raise IngestRefused(f"the drawing is too complex: nested block references expand to more than "
                                    f"{MAX_EXPANDED_ENTITIES:,} entities")
        visiting.discard(name)
        memo[name] = total
        return total

    return count(doc.modelspace())


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
    if len(msp) > MAX_MODEL_ENTITIES:
        raise IngestRefused(f"the drawing is too complex: {len(msp):,} entities in model space (limit {MAX_MODEL_ENTITIES:,})")
    _expanded_entity_count(doc)

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
        text = " ".join(str(text).split())[:MAX_LABEL_CHARS]
        if text:
            ins = e.dxf.insert
            labels.append(((float(ins.x), float(ins.y)), text))

    if len(labels) > MAX_LABELS:
        raise IngestRefused(f"the drawing has {len(labels):,} text labels (limit {MAX_LABELS:,}); remove unrelated annotation")
    closed = opened = dups = crossing = curved = labelled = vertices = 0
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
        if closed > MAX_SPACE_POLYLINES:
            raise IngestRefused(f"more than {MAX_SPACE_POLYLINES:,} closed polylines on space layers")
        vertices += len(pts)
        if vertices > MAX_TOTAL_VERTICES:
            raise IngestRefused(f"the space polylines have more than {MAX_TOTAL_VERTICES:,} vertices in total")
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
        lo_x, hi_x = min(p[0] for p in pts), max(p[0] for p in pts)
        lo_y, hi_y = min(p[1] for p in pts), max(p[1] for p in pts)
        inside = [t for p, t in labels if lo_x <= p[0] <= hi_x and lo_y <= p[1] <= hi_y and _point_in_polygon(p, pts)]
        notes = ["area from polyline geometry"]
        if len(inside) > 1:
            notes.append(f"{len(inside)} labels inside; first used: {inside[0][:80]!r}")
        if inside:
            labelled += 1
        res.spaces.append(SpaceRecord(
            key=e.dxf.handle, name=inside[0] if inside else None, area_m2=area,
            source_kind="dxf", notes=tuple(notes), centroid_m=(round(_centroid(pts)[0] * factor, 3),
                                                                 round(_centroid(pts)[1] * factor, 3))))

    # Scale sanity: drawing extents against a plausible building size.
    extents_m: list[float] | None = None
    scale_check = "unknown"
    try:
        box = bbox.extents(e for e in msp if e.dxftype() in _EXTENT_TYPES)
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
