"""duct-fab validator: re-measures the STEP and DXF on disk against the spec card. It never trusts the builder.

Independence rules:
- imports neither skills.cad nor build.py; the only third-party readers are cadquery (STEP importer) and ezdxf;
- the expected values are re-derived here from the spec (defaults applied here, alignment offsets computed here);
- every check is wrapped: a bad or missing file produces failing checks, never an exception.

Geometry only: this validator decides whether the output matches the spec card, nothing about compliance.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

TOL_MM = 0.5  # general dimensional tolerance
AREA_REL_TOL = 1e-3  # 0.1 %
CONNECT_TOL = 1e-3  # net loop end-to-end gap
ZERO_EDGE_MM = 1e-3  # shorter than this counts as a zero-length segment
TOUCH_MM = 1e-4  # non-adjacent segments closer than this are touching
PARALLEL_SIN = 1e-4  # sin of the angle below which two segments are parallel
APPID = "MEPFAB"
LAYERS = ("CUT", "BEND", "ANNOTATION")
ROLES = ("inlet", "outlet", "seam_start", "seam_end", "ruling")

Pt = tuple[float, float]


@dataclass
class ValidationResult:
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def failed(self) -> list[str]:
        return [c["name"] for c in self.checks if not c["passed"]]


# ------------------------------------------------------------------ small helpers
def _num(v: float) -> str:
    return format(round(float(v), 6) + 0.0, "g")


def _r(v: Any, nd: int = 4) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        return round(v, nd) + 0.0
    if isinstance(v, (list, tuple)):
        return [_r(x, nd) for x in v]
    if isinstance(v, dict):
        return {k: _r(x, nd) for k, x in v.items()}
    return v


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalise(spec: dict[str, Any]) -> dict[str, Any]:
    """Apply the spec-card defaults (own copy of the rules; no schema validation here)."""
    out = copy.deepcopy(spec)
    if out.get("fitting") == "rect_to_round":
        g = out["geometry"]
        g.setdefault("offset_x_mm", 0)
        g.setdefault("offset_y_mm", 0)
        g.setdefault("circle_segments", 16)
    return out


@dataclass
class Expected:
    kind: str
    length: float
    inlet: tuple[float, float]  # W, H
    outlet_rect: tuple[float, float] | None  # W2, H2 (None for a round outlet)
    diameter: float | None
    segments: int | None
    offset: tuple[float, float]  # outlet centre relative to inlet centre
    outlet_vertices: list[tuple[float, float]]  # expected outlet corner / chord points relative to inlet centre


def _expected(spec: dict[str, Any]) -> Expected:
    g = spec["geometry"]
    kind = spec["fitting"]
    if kind == "rect_to_round":
        n = int(g["circle_segments"])
        d = float(g["diameter_mm"])
        o = (float(g["offset_x_mm"]), float(g["offset_y_mm"]))
        pts = [(o[0] + d / 2 * math.cos(2 * math.pi * j / n), o[1] + d / 2 * math.sin(2 * math.pi * j / n))
               for j in range(n)]
        return Expected(kind, float(g["length_mm"]), (float(g["width_mm"]), float(g["height_mm"])), None, d, n, o, pts)
    if kind == "rect_reducer":
        w1, h1, w2, h2 = (float(g[k]) for k in ("width_in_mm", "height_in_mm", "width_out_mm", "height_out_mm"))
        dw, dh = w2 - w1, h2 - h1
        o = {
            "concentric": (0.0, 0.0),
            "flat_bottom": (0.0, dh / 2),
            "flat_top": (0.0, -dh / 2),
            "flat_left": (dw / 2, 0.0),
            "flat_right": (-dw / 2, 0.0),
        }[g["alignment"]]
        length = float(g["length_mm"])
    elif kind == "rect_offset":
        w1, h1 = w2, h2 = float(g["width_mm"]), float(g["height_mm"])
        o = (float(g["offset_x_mm"]), float(g["offset_y_mm"]))
        length = float(g["length_mm"])
    else:
        raise ValueError(f"unknown fitting {kind!r}")
    corners = [(o[0] + sx * w2 / 2, o[1] + sy * h2 / 2) for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1))]
    return Expected(kind, length, (w1, h1), (w2, h2), None, None, o, corners)


# ------------------------------------------------------------------ 2D geometry
def _sub(a: Pt, b: Pt) -> Pt:
    return (a[0] - b[0], a[1] - b[1])


def _dot(a: Pt, b: Pt) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _cross(a: Pt, b: Pt) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _len(a: Pt) -> float:
    return math.hypot(a[0], a[1])


def _orient(a: Pt, b: Pt, c: Pt) -> float:
    return _cross(_sub(b, a), _sub(c, a))


def _pt_seg(p: Pt, a: Pt, b: Pt) -> float:
    d = _sub(b, a)
    l2 = _dot(d, d)
    if l2 == 0:
        return _len(_sub(p, a))
    t = max(0.0, min(1.0, _dot(_sub(p, a), d) / l2))
    return _len(_sub(p, (a[0] + t * d[0], a[1] + t * d[1])))


def _seg_dist(a: Pt, b: Pt, c: Pt, d: Pt) -> float:
    """Distance between two segments (0 when they cross, touch or overlap)."""
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    if o1 * o2 < 0 and o3 * o4 < 0:
        return 0.0
    return min(_pt_seg(a, c, d), _pt_seg(b, c, d), _pt_seg(c, a, b), _pt_seg(d, a, b))


def _dedupe(poly: list[Pt]) -> list[Pt]:
    out: list[Pt] = []
    for p in poly:
        if not out or _len(_sub(p, out[-1])) >= ZERO_EDGE_MM:
            out.append(p)
    while len(out) > 1 and _len(_sub(out[0], out[-1])) < ZERO_EDGE_MM:
        out.pop()
    return out


def _self_intersections(poly_in: list[Pt]) -> list[str]:
    """Problems in a closed polygon: crossing or touching non-adjacent segments, and spikes (reversed adjacent)."""
    poly = _dedupe(poly_in)
    n = len(poly)
    if n < 3:
        return [f"fewer than 3 distinct vertices ({n})"]
    bad: list[str] = []
    segs = [(poly[i], poly[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            adjacent = j == i + 1 or (i == 0 and j == n - 1)
            (a, b), (c, d) = segs[i], segs[j]
            if adjacent:
                u, v = _sub(b, a), _sub(d, c)  # consecutive segments: a fold-back (antiparallel) overlaps itself
                sin = abs(_cross(u, v)) / (_len(u) * _len(v))
                if sin < 1e-9 and _dot(u, v) < 0:
                    bad.append(f"spike at segments {i},{j}")
                continue
            if _seg_dist(a, b, c, d) < TOUCH_MM:
                bad.append(f"segments {i},{j} cross or touch")
    return bad


def _signed_area(poly: list[Pt]) -> float:
    s = 0.0
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        s += p[0] * q[1] - q[0] * p[1]
    return 0.5 * s


# ------------------------------------------------------------------ loaded data
@dataclass
class _BendLine:
    p: Pt
    q: Pt
    role: str | None
    index: int | None

    @property
    def length(self) -> float:
        return _len(_sub(self.q, self.p))


@dataclass
class _Step:
    solids: int
    valid: bool
    volume: float
    stray_faces: int
    all_planar: bool
    vertices: list[tuple[float, float, float]]
    edge_lengths: list[float]
    lateral_area: float


@dataclass
class _Net:
    inlet: list[_BendLine]
    outlet: list[_BendLine]
    seam_start: _BendLine
    seam_end: _BendLine
    trav: list[tuple[Pt, Pt, str]]  # traversal edges (a, b, role) in loop order

    @property
    def polygon(self) -> list[Pt]:
        return [a for a, _, _ in self.trav]

    @property
    def max_gap(self) -> float:
        n = len(self.trav)
        return max(_len(_sub(self.trav[i][1], self.trav[(i + 1) % n][0])) for i in range(n))


def _read_step(path: Path) -> _Step:
    import cadquery as cq

    wp = cq.importers.importStep(str(path))
    shapes = wp.vals()
    solids = wp.solids().vals()
    all_faces = sum(len(s.Faces()) for s in shapes)
    if len(solids) != 1:
        return _Step(len(solids), False, 0.0, all_faces, False, [], [], 0.0)
    solid = solids[0]
    pts: list[tuple[float, float, float]] = []
    for v in solid.Vertices():
        p = (float(v.X), float(v.Y), float(v.Z))
        if not any(math.dist(p, q) < 1e-4 for q in pts):
            pts.append(p)
    faces = solid.Faces()
    lateral = 0.0
    planar = True
    for f in faces:
        if f.geomType() != "PLANE":
            planar = False
        if f.BoundingBox().zlen >= 1e-3:  # a cap lies flat in a z plane; everything else is lateral
            lateral += float(f.Area())
    return _Step(
        solids=len(solids),
        valid=bool(solid.isValid()),
        volume=float(solid.Volume()),
        stray_faces=all_faces - len(faces),
        all_planar=planar,
        vertices=pts,
        edge_lengths=[float(e.Length()) for e in solid.Edges()],
        lateral_area=lateral,
    )


class _Dxf:
    def __init__(self, path: Path):
        import ezdxf

        self.doc = ezdxf.readfile(str(path))
        msp = self.doc.modelspace()
        self.layers = {layer.dxf.name for layer in self.doc.layers}
        self.cut_entities = [e for e in msp if e.dxf.layer == "CUT"]
        self.bend: list[_BendLine] = []
        for e in msp.query("LINE"):
            if e.dxf.layer != "BEND":
                continue
            role: str | None = None
            index: int | None = None
            if e.has_xdata(APPID):
                for tag in e.get_xdata(APPID):
                    if tag.code == 1000 and role is None:
                        role = str(tag.value)
                    elif tag.code == 1070 and index is None:
                        index = int(tag.value)
            s, t = e.dxf.start, e.dxf.end
            self.bend.append(_BendLine((float(s.x), float(s.y)), (float(t.x), float(t.y)), role, index))
        self.texts = [str(e.dxf.text) for e in msp.query("TEXT") if e.dxf.layer == "ANNOTATION"]

    def cut_polyline(self) -> Any:
        if len(self.cut_entities) != 1 or self.cut_entities[0].dxftype() != "LWPOLYLINE":
            kinds = [e.dxftype() for e in self.cut_entities]
            raise ValueError(f"expected one LWPOLYLINE on CUT, found {kinds}")
        return self.cut_entities[0]

    def cut_points(self) -> list[Pt]:
        pl = self.cut_polyline()
        return [(float(x), float(y)) for x, y in pl.get_points("xy")]


def _build_net(lines: list[_BendLine]) -> _Net:
    untagged = [ln for ln in lines if ln.role is None or ln.index is None]
    if untagged:
        raise ValueError(f"{len(untagged)} BEND line(s) carry no MEPFAB role/index tag")
    unknown = {ln.role for ln in lines if ln.role not in ROLES}
    if unknown:
        raise ValueError(f"unknown BEND roles {sorted(unknown)}")  # type: ignore[type-var]

    def role(r: str) -> list[_BendLine]:
        return sorted((ln for ln in lines if ln.role == r), key=lambda ln: ln.index or 0)

    inlet, outlet = role("inlet"), role("outlet")
    starts, ends = role("seam_start"), role("seam_end")
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError(f"need exactly one seam_start and one seam_end, found {len(starts)} and {len(ends)}")
    for name, chain in (("inlet", inlet), ("outlet", outlet)):
        if len(chain) < 3 or [ln.index for ln in chain] != list(range(len(chain))):
            raise ValueError(f"{name} chain indices are not 0..n-1 ({[ln.index for ln in chain]})")
    trav: list[tuple[Pt, Pt, str]] = [(ln.p, ln.q, "inlet") for ln in inlet]
    trav.append((ends[0].p, ends[0].q, "seam_end"))
    trav += [(ln.q, ln.p, "outlet") for ln in reversed(outlet)]
    trav.append((starts[0].q, starts[0].p, "seam_start"))
    return _Net(inlet, outlet, starts[0], ends[0], trav)


# ------------------------------------------------------------------ context with cached loaders
class _Ctx:
    def __init__(self, spec: dict[str, Any], step: Path | None, dxf: Path | None, manifest: Path | None):
        self.spec_raw = spec
        self.step_path, self.dxf_path, self.manifest_path = step, dxf, manifest
        self._cache: dict[str, Any] = {}

    def _get(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self._cache:
            try:
                self._cache[key] = (True, fn())
            except Exception as exc:  # noqa: BLE001 - failure is stored and re-raised on every use
                self._cache[key] = (False, exc)
        ok, val = self._cache[key]
        if not ok:
            raise val
        return val

    @property
    def spec(self) -> dict[str, Any]:
        return self._get("spec", lambda: _normalise(self.spec_raw))

    @property
    def exp(self) -> Expected:
        return self._get("exp", lambda: _expected(self.spec))

    @property
    def step(self) -> _Step:
        def load() -> _Step:
            if self.step_path is None:
                raise ValueError("no .step file supplied")
            return _read_step(self.step_path)

        return self._get("step", load)

    @property
    def dxf(self) -> _Dxf:
        def load() -> _Dxf:
            if self.dxf_path is None:
                raise ValueError("no .dxf file supplied")
            return _Dxf(self.dxf_path)

        return self._get("dxf", load)

    @property
    def net(self) -> _Net:
        return self._get("net", lambda: _build_net(self.dxf.bend))

    @property
    def net_strict(self) -> _Net:
        def strict() -> _Net:
            net = self.net
            if net.max_gap > CONNECT_TOL:
                raise ValueError(f"net loop is not closed/connected (max gap {net.max_gap:.4f} mm)")
            return net

        return self._get("net_strict", strict)

    @property
    def solid_ok(self) -> _Step:
        step = self.step
        if step.solids != 1:
            raise ValueError(f"STEP holds {step.solids} solids, expected 1")
        return step


# ------------------------------------------------------------------ the checks
Outcome = tuple[bool, Any, Any]  # passed, expected, actual


def _caps(ctx: _Ctx) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]], list[Any]]:
    step, exp = ctx.solid_ok, ctx.exp
    inlet = [p for p in step.vertices if abs(p[2]) <= TOL_MM]
    outlet = [p for p in step.vertices if abs(p[2] - exp.length) <= TOL_MM]
    other = [p for p in step.vertices if abs(p[2]) > TOL_MM and abs(p[2] - exp.length) > TOL_MM]
    return inlet, outlet, other


def _bbox(pts: list[tuple[float, ...]]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def chk_step_solid(ctx: _Ctx) -> Outcome:
    s = ctx.step
    ok = s.solids == 1 and s.valid and s.volume > 0 and s.stray_faces == 0
    return ok, "1 valid closed Solid, volume > 0, nothing else", {
        "solids": s.solids, "valid": s.valid, "volume_mm3": _r(s.volume, 1), "stray_faces": s.stray_faces}


def chk_step_planar(ctx: _Ctx) -> Outcome:
    s = ctx.solid_ok
    return s.all_planar, "all faces planar (polyhedral)", s.all_planar


def chk_step_length(ctx: _Ctx) -> Outcome:
    s, exp = ctx.solid_ok, ctx.exp
    zs = [p[2] for p in s.vertices]
    zmin, zmax = min(zs), max(zs)
    ok = abs(zmin) <= TOL_MM and abs((zmax - zmin) - exp.length) <= TOL_MM and abs(zmax - exp.length) <= TOL_MM
    return ok, {"z_min": 0.0, "z_max": exp.length}, {"z_min": _r(zmin), "z_max": _r(zmax)}


def chk_step_vertex_count(ctx: _Ctx) -> Outcome:
    exp = ctx.exp
    inlet, outlet, other = _caps(ctx)
    n_out = exp.segments if exp.segments else 4
    expected = {"inlet": 4, "outlet": n_out, "off_cap_planes": 0}
    actual = {"inlet": len(inlet), "outlet": len(outlet), "off_cap_planes": len(other)}
    return expected == actual, expected, actual


def _rect_corners_match(pts: list[tuple[float, ...]], centre: tuple[float, float], w: float, h: float) -> float:
    """Worst distance from an expected corner to its nearest vertex."""
    corners = [(centre[0] + sx * w / 2, centre[1] + sy * h / 2) for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1))]
    if not pts:
        return math.inf
    return max(min(math.hypot(c[0] - p[0], c[1] - p[1]) for p in pts) for c in corners)


def chk_step_inlet_cap(ctx: _Ctx) -> Outcome:
    exp = ctx.exp
    inlet, _, _ = _caps(ctx)
    w, h = exp.inlet
    if len(inlet) != 4:
        return False, {"vertices": 4, "size": [w, h]}, {"vertices": len(inlet)}
    x0, x1, y0, y1 = _bbox(inlet)
    centre = ((x0 + x1) / 2, (y0 + y1) / 2)
    worst = _rect_corners_match(inlet, (0.0, 0.0), w, h)
    ok = abs((x1 - x0) - w) <= TOL_MM and abs((y1 - y0) - h) <= TOL_MM and worst <= TOL_MM
    return ok, {"size": [w, h], "centre": [0.0, 0.0], "z": 0.0}, {
        "size": _r([x1 - x0, y1 - y0]), "centre": _r(centre), "worst_corner_error": _r(worst)}


def chk_step_outlet_cap(ctx: _Ctx) -> Outcome:
    exp = ctx.exp
    _, outlet, _ = _caps(ctx)
    if exp.outlet_rect is not None:
        w2, h2 = exp.outlet_rect
        if len(outlet) != 4:
            return False, {"vertices": 4, "size": [w2, h2]}, {"vertices": len(outlet)}
        x0, x1, y0, y1 = _bbox(outlet)
        ok = abs((x1 - x0) - w2) <= TOL_MM and abs((y1 - y0) - h2) <= TOL_MM
        return ok, {"size": [w2, h2]}, {"size": _r([x1 - x0, y1 - y0])}
    assert exp.segments is not None and exp.diameter is not None
    if len(outlet) != exp.segments:
        return False, {"vertices": exp.segments, "radius": exp.diameter / 2}, {"vertices": len(outlet)}
    cx = sum(p[0] for p in outlet) / len(outlet)
    cy = sum(p[1] for p in outlet) / len(outlet)
    radii = [math.hypot(p[0] - cx, p[1] - cy) for p in outlet]
    worst = max(abs(r - exp.diameter / 2) for r in radii)
    return worst <= TOL_MM, {"vertices": exp.segments, "radius": exp.diameter / 2}, {
        "vertices": len(outlet), "radius_min": _r(min(radii)), "radius_max": _r(max(radii))}


def chk_step_outlet_offset(ctx: _Ctx) -> Outcome:
    exp = ctx.exp
    inlet, outlet, _ = _caps(ctx)
    if not inlet or not outlet:
        return False, list(exp.offset), "missing cap vertices"
    if exp.outlet_rect is not None:
        x0, x1, y0, y1 = _bbox(outlet)
        oc = ((x0 + x1) / 2, (y0 + y1) / 2)
    else:
        oc = (sum(p[0] for p in outlet) / len(outlet), sum(p[1] for p in outlet) / len(outlet))
    x0, x1, y0, y1 = _bbox(inlet)
    ic = ((x0 + x1) / 2, (y0 + y1) / 2)
    off = (oc[0] - ic[0], oc[1] - ic[1])
    ok = abs(off[0] - exp.offset[0]) <= TOL_MM and abs(off[1] - exp.offset[1]) <= TOL_MM
    return ok, _r(list(exp.offset)), _r(list(off))


def chk_step_bbox_xy(ctx: _Ctx) -> Outcome:
    exp, step = ctx.exp, ctx.solid_ok
    w, h = exp.inlet
    pts = [(-w / 2, -h / 2), (w / 2, h / 2)] + exp.outlet_vertices
    ex = _bbox(pts)
    ax = _bbox(step.vertices)
    ok = all(abs(a - e) <= TOL_MM for a, e in zip(ax, ex, strict=True))
    return ok, {"x": _r([ex[0], ex[1]]), "y": _r([ex[2], ex[3]])}, {"x": _r([ax[0], ax[1]]), "y": _r([ax[2], ax[3]])}


def chk_dxf_loads(ctx: _Ctx) -> Outcome:
    d = ctx.dxf
    return True, "readable DXF", {"bend_lines": len(d.bend), "texts": len(d.texts)}


def chk_dxf_layers(ctx: _Ctx) -> Outcome:
    have = ctx.dxf.layers
    missing = [name for name in LAYERS if name not in have]
    return not missing, list(LAYERS), {"missing": missing}


def chk_cut_closed_single(ctx: _Ctx) -> Outcome:
    d = ctx.dxf
    kinds = [e.dxftype() for e in d.cut_entities]
    if kinds != ["LWPOLYLINE"]:
        return False, "exactly one closed LWPOLYLINE on CUT", {"cut_entities": kinds}
    pl = d.cut_polyline()
    n = len(pl)
    return bool(pl.closed) and n >= 3, "exactly one closed LWPOLYLINE on CUT", {"closed": bool(pl.closed), "vertices": n}


def chk_cut_zero_length(ctx: _Ctx) -> Outcome:
    pts = ctx.dxf.cut_points()
    n = len(pts)
    lens = [_len(_sub(pts[(i + 1) % n], pts[i])) for i in range(n)]
    short = [i for i, ln in enumerate(lens) if ln < ZERO_EDGE_MM]
    return not short, f"every edge >= {ZERO_EDGE_MM} mm", {"short_edges": short, "min_edge_mm": _r(min(lens))}


def chk_cut_self_intersection(ctx: _Ctx) -> Outcome:
    bad = _self_intersections(ctx.dxf.cut_points())
    return not bad, "no crossing/touching/overlapping segments", {"problems": bad[:5], "count": len(bad)}


def chk_net_connected(ctx: _Ctx) -> Outcome:
    net = ctx.net
    gap = net.max_gap
    return gap <= CONNECT_TOL, f"net loop closed end to end within {CONNECT_TOL} mm", {
        "max_gap_mm": _r(gap, 6), "edges": len(net.trav)}


def chk_net_self_intersection(ctx: _Ctx) -> Outcome:
    bad = _self_intersections(ctx.net_strict.polygon)
    return not bad, "net loop does not intersect itself", {"problems": bad[:5], "count": len(bad)}


def chk_edge_lengths(ctx: _Ctx) -> Outcome:
    s, d = ctx.solid_ok, ctx.dxf
    l3 = sorted(s.edge_lengths)
    ld = [ln.length for ln in d.bend]
    exp = {"dxf_bend_lines": len(l3) + 1, "step_edges": len(l3), "max_dev_mm": f"<= {TOL_MM}"}
    if len(ld) != len(l3) + 1:
        return False, exp, {"dxf_bend_lines": len(ld), "step_edges": len(l3)}
    # The duplicated seam edge is the one extra line. Tagged seam lines are the only legitimate candidates (skipping an
    # arbitrary line could hide a wrong ruling when several edges share a length); untagged files try every line.
    seam_idx = [i for i, ln in enumerate(d.bend) if ln.role in ("seam_start", "seam_end")]
    candidates = seam_idx if seam_idx else list(range(len(ld)))
    best = math.inf
    tried: set[float] = set()
    for skip in candidates:
        key = round(ld[skip], 4)
        if key in tried:
            continue
        tried.add(key)
        rest = sorted(ld[:skip] + ld[skip + 1:])
        dev = max(abs(a - b) for a, b in zip(rest, l3, strict=True))
        best = min(best, dev)
    seam_pair = [ln.length for ln in d.bend if ln.role in ("seam_start", "seam_end")]
    seam_dev = max(seam_pair) - min(seam_pair) if len(seam_pair) == 2 else 0.0
    ok = best <= TOL_MM and seam_dev <= TOL_MM
    return ok, exp, {"dxf_bend_lines": len(ld), "step_edges": len(l3), "max_dev_mm": _r(best),
                     "seam_start_vs_end_mm": _r(seam_dev)}


def chk_net_area(ctx: _Ctx) -> Outcome:
    s = ctx.solid_ok
    net_area = abs(_signed_area(ctx.net_strict.polygon))
    rel = abs(net_area - s.lateral_area) / s.lateral_area if s.lateral_area > 0 else math.inf
    return rel <= AREA_REL_TOL, {"lateral_area_mm2": _r(s.lateral_area, 1), "rel_tol": AREA_REL_TOL}, {
        "net_area_mm2": _r(net_area, 1), "rel_dev": _r(rel, 6)}


def _perimeter(lines: list[_BendLine]) -> float:
    return sum(ln.length for ln in lines)


def chk_inlet_perimeter(ctx: _Ctx) -> Outcome:
    w, h = ctx.exp.inlet
    exp, act = 2 * (w + h), _perimeter(ctx.net.inlet)
    return abs(act - exp) <= TOL_MM, _r(exp), _r(act)


def chk_outlet_perimeter(ctx: _Ctx) -> Outcome:
    e = ctx.exp
    if e.outlet_rect is not None:
        exp = 2 * (e.outlet_rect[0] + e.outlet_rect[1])
    else:
        assert e.segments is not None and e.diameter is not None
        exp = e.segments * e.diameter * math.sin(math.pi / e.segments)
    act = _perimeter(ctx.net.outlet)
    return abs(act - exp) <= TOL_MM, _r(exp), _r(act)


def _cut_oriented(ctx: _Ctx) -> tuple[list[Pt], float]:
    """CUT vertices wound the same way as the net loop, and the net's outward sign."""
    net_poly = ctx.net_strict.polygon
    sgn = 1.0 if _signed_area(net_poly) > 0 else -1.0
    cut = ctx.dxf.cut_points()
    if len(cut) < 3:
        raise ValueError("CUT outline has fewer than 3 vertices")
    if (1.0 if _signed_area(cut) > 0 else -1.0) != sgn:
        cut = list(reversed(cut))
    return cut, sgn


def _offset_matches(a: Pt, b: Pt, cut: list[Pt], sgn: float, dist: float) -> tuple[bool, list[float]]:
    """Is there a CUT segment parallel (same direction) to a->b, `dist` outward of it, overlapping it?"""
    ab = _sub(b, a)
    ln = _len(ab)
    u = (ab[0] / ln, ab[1] / ln)
    nrm = (sgn * u[1], -sgn * u[0])
    seen: list[float] = []
    n = len(cut)
    for i in range(n):
        c, d = cut[i], cut[(i + 1) % n]
        cd = _sub(d, c)
        lc = _len(cd)
        if lc < 1e-9:
            continue
        if abs(_cross(u, cd)) / lc > PARALLEL_SIN or _dot(u, cd) <= 0:
            continue
        off = _dot(_sub(c, a), nrm)
        s0, s1 = sorted((_dot(_sub(c, a), u), _dot(_sub(d, a), u)))
        overlap = min(ln, s1) - max(0.0, s0)
        if overlap <= 1e-6:
            continue
        seen.append(off)
        if abs(off - dist) <= TOL_MM:
            return True, seen
    return False, seen


def chk_seam_allowance(ctx: _Ctx) -> Outcome:
    allowance = float(ctx.spec["seam"]["allowance_mm"])
    cut, sgn = _cut_oriented(ctx)
    net = ctx.net_strict
    end_ok, seen = _offset_matches(net.seam_end.p, net.seam_end.q, cut, sgn, allowance)
    st = net.seam_start
    worst = 0.0
    n = len(cut)
    for t in (0.25, 0.5, 0.75):
        pt = (st.p[0] + t * (st.q[0] - st.p[0]), st.p[1] + t * (st.q[1] - st.p[1]))
        worst = max(worst, min(_pt_seg(pt, cut[i], cut[(i + 1) % n]) for i in range(n)))
    ok = end_ok and worst <= TOL_MM
    return ok, {"seam_end_offset_mm": allowance, "seam_start_on_outline_within_mm": TOL_MM}, {
        "parallel_offsets_found_mm": _r(seen), "seam_start_distance_to_outline_mm": _r(worst)}


def chk_connection_allowance(ctx: _Ctx) -> Outcome:
    allowance = float(ctx.spec["connection"]["allowance_mm"])
    cut, sgn = _cut_oriented(ctx)
    net = ctx.net_strict
    missing: list[str] = []
    for role, lines in (("inlet", net.inlet), ("outlet", net.outlet)):
        for ln in lines:
            # outlet chain is walked in reverse in the loop, so a->b is q->p there
            a, b = (ln.p, ln.q) if role == "inlet" else (ln.q, ln.p)
            if _len(_sub(b, a)) < 1e-9:
                missing.append(f"{role}[{ln.index}] zero length")
                continue
            ok, _ = _offset_matches(a, b, cut, sgn, allowance)
            if not ok:
                missing.append(f"{role}[{ln.index}]")
    return not missing, {"offset_mm": allowance, "segments": len(net.inlet) + len(net.outlet)}, {
        "segments_without_offset_edge": missing}


def chk_title_block(ctx: _Ctx) -> Outcome:
    spec = ctx.spec
    wanted = {
        "mark": f"MARK: {spec['mark']}",
        "thickness": f"SHEET t={_num(spec['sheet_thickness_mm'])} mm",
        "seam": f"SEAM: {spec['seam']['type']} +{_num(spec['seam']['allowance_mm'])} mm",
        "connection": f"CONN: {spec['connection']['type']} +{_num(spec['connection']['allowance_mm'])} mm",
    }
    texts = ctx.dxf.texts
    missing = {}
    for key, s in wanted.items():
        pat = re.compile(r"(?<!\S)" + re.escape(s) + r"(?!\S)")
        if not any(pat.search(t) for t in texts):
            missing[key] = s
    return not missing, list(wanted.values()), {"missing": missing, "text_lines": len(texts)}


def chk_manifest(ctx: _Ctx, supplied: dict[str, Path]) -> Outcome:
    assert ctx.manifest_path is not None
    m = json.loads(ctx.manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    listed = {}
    for f in m.get("files", []):
        listed[f.get("name")] = f
        p = supplied.get(f.get("name"))
        if p is None:
            problems.append(f"{f.get('name')}: listed but not supplied")
            continue
        if f.get("bytes") != p.stat().st_size:
            problems.append(f"{p.name}: bytes {f.get('bytes')} != {p.stat().st_size}")
        if f.get("sha256") != _sha256(p):
            problems.append(f"{p.name}: sha256 mismatch")
    for name in supplied:
        if name not in listed:
            problems.append(f"{name}: supplied but not in manifest")
    if m.get("inputs") != ctx.spec:
        problems.append("manifest inputs differ from the normalised spec")
    return not problems, "manifest hashes, sizes and inputs match", {"problems": problems}


# ------------------------------------------------------------------ driver
def validate(spec: dict[str, Any], files: list[Path]) -> ValidationResult:
    checks: list[dict[str, Any]] = []

    def run(name: str, tolerance: Any, fn: Callable[[], Outcome]) -> None:
        try:
            passed, expected, actual = fn()
            entry = {"name": name, "passed": bool(passed), "expected": expected, "actual": actual,
                     "tolerance": tolerance}
        except Exception as exc:  # noqa: BLE001 - any failure to measure is a failed check
            entry = {"name": name, "passed": False, "expected": None,
                     "actual": f"{type(exc).__name__}: {str(exc)[:300]}", "tolerance": tolerance}
        checks.append(entry)

    try:
        paths = [Path(f) for f in files]
    except Exception:  # noqa: BLE001
        paths = []
    steps = [p for p in paths if p.suffix.lower() in (".step", ".stp")]
    dxfs = [p for p in paths if p.suffix.lower() == ".dxf"]
    jsons = [p for p in paths if p.suffix.lower() == ".json"]
    others = [p for p in paths if p not in steps + dxfs + jsons]
    ctx = _Ctx(spec if isinstance(spec, dict) else {},
               steps[0] if len(steps) == 1 else None,
               dxfs[0] if len(dxfs) == 1 else None,
               jsons[0] if len(jsons) == 1 else None)

    run("input_files", None, lambda: (
        len(steps) == 1 and len(dxfs) == 1 and len(jsons) <= 1 and not others,
        "exactly one .step, one .dxf, optional manifest.json",
        {"step": len(steps), "dxf": len(dxfs), "json": len(jsons), "other": [p.name for p in others]}))
    run("spec_readable", None, lambda: (bool(ctx.exp), "spec card parses and defaults apply", ctx.exp.kind))

    t = TOL_MM
    run("step_solid", None, lambda: chk_step_solid(ctx))
    run("step_planar_faces", None, lambda: chk_step_planar(ctx))
    run("step_length", t, lambda: chk_step_length(ctx))
    run("step_vertex_count", t, lambda: chk_step_vertex_count(ctx))
    run("step_inlet_cap", t, lambda: chk_step_inlet_cap(ctx))
    run("step_outlet_cap", t, lambda: chk_step_outlet_cap(ctx))
    run("step_outlet_offset", t, lambda: chk_step_outlet_offset(ctx))
    run("step_bbox_xy", t, lambda: chk_step_bbox_xy(ctx))
    run("dxf_loads", None, lambda: chk_dxf_loads(ctx))
    run("dxf_layers", None, lambda: chk_dxf_layers(ctx))
    run("cut_closed_single", None, lambda: chk_cut_closed_single(ctx))
    run("cut_no_zero_length", ZERO_EDGE_MM, lambda: chk_cut_zero_length(ctx))
    run("cut_no_self_intersection", TOUCH_MM, lambda: chk_cut_self_intersection(ctx))
    run("net_connected", CONNECT_TOL, lambda: chk_net_connected(ctx))
    run("net_no_self_intersection", TOUCH_MM, lambda: chk_net_self_intersection(ctx))
    run("edge_lengths_match_3d", t, lambda: chk_edge_lengths(ctx))
    run("net_area_matches_lateral_area", "0.1%", lambda: chk_net_area(ctx))
    run("net_inlet_perimeter", t, lambda: chk_inlet_perimeter(ctx))
    run("net_outlet_perimeter", t, lambda: chk_outlet_perimeter(ctx))
    run("seam_allowance", t, lambda: chk_seam_allowance(ctx))
    run("connection_allowance", t, lambda: chk_connection_allowance(ctx))
    run("title_block", None, lambda: chk_title_block(ctx))
    if jsons:
        supplied = {p.name: p for p in steps + dxfs}
        run("manifest", None, lambda: chk_manifest(ctx, supplied))

    return ValidationResult(passed=all(c["passed"] for c in checks), checks=checks)
