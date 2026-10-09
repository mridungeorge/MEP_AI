"""Flat-pattern development of a sheet-metal tube made of planar faces. Pure Python: no CAD kernel, no network.

A `Tube` is an ordered strip of planar faces (triangles or quads) wrapped around an axis. Consecutive faces share
one edge. The strip is cut along one seam edge, so the seam vertices exist twice, as `X` and `X'` (a primed label is
the second copy of the same 3D point). Unrolling places every face with true edge lengths (triangulation
development), so the developed length of every edge equals its 3D length.

Geometry only: nothing here decides compliance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]

EPS = 1e-9


def base(label: str) -> str:
    """The 3D vertex a label stands for (a primed label is the second copy of a seam vertex)."""
    return label.rstrip("'")


@dataclass(frozen=True)
class Tube:
    vertices: dict[str, Vec3]  # unprimed label -> xyz (mm)
    faces: tuple[tuple[str, ...], ...]  # strip order, wound counter-clockwise seen from outside
    inlet: tuple[str, ...]  # inlet boundary labels, strip order, first..last
    outlet: tuple[str, ...]  # outlet boundary labels, strip order, first..last

    def xyz(self, label: str) -> Vec3:
        return self.vertices[base(label)]


@dataclass(frozen=True)
class NetEdge:
    role: str  # inlet | outlet | seam_start | seam_end | ruling
    index: int  # position inside its role (chain order)
    p: Vec2
    q: Vec2
    length_3d: float


# ---------------------------------------------------------------- vector helpers
def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def dist3(a: Vec3, b: Vec3) -> float:
    return _norm(_sub(a, b))


def face_normal(points: list[Vec3]) -> Vec3:
    """Newell normal (not normalised); its length is twice the polygon area."""
    nx = ny = nz = 0.0
    for i, p in enumerate(points):
        q = points[(i + 1) % len(points)]
        nx += (p[1] - q[1]) * (p[2] + q[2])
        ny += (p[2] - q[2]) * (p[0] + q[0])
        nz += (p[0] - q[0]) * (p[1] + q[1])
    return (nx, ny, nz)


def face_area(points: list[Vec3]) -> float:
    return 0.5 * _norm(face_normal(points))


def orient_outward(vertices: dict[str, Vec3], faces: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Wind every face counter-clockwise seen from outside. Valid for a convex body (the hull of two parallel
    convex outlines, which is what a duct fitting is)."""
    pts = list(vertices.values())
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    cz = sum(p[2] for p in pts) / len(pts)
    out = []
    for f in faces:
        ps = [vertices[base(label)] for label in f]
        n = face_normal(ps)
        fc = (sum(p[0] for p in ps) / len(ps), sum(p[1] for p in ps) / len(ps), sum(p[2] for p in ps) / len(ps))
        out.append(f if _dot(n, _sub(fc, (cx, cy, cz))) > 0 else tuple(reversed(f)))
    return out


# ---------------------------------------------------------------- unrolling
def _local_coords(points: list[Vec3]) -> list[Vec2]:
    """Isometric 2D coordinates of a planar polygon, counter-clockwise seen along its normal."""
    n = face_normal(points)
    ln = _norm(n)
    if ln < EPS:
        raise ValueError("degenerate face (zero area)")
    n = (n[0] / ln, n[1] / ln, n[2] / ln)
    e = _sub(points[1], points[0])
    le = _norm(e)
    u = (e[0] / le, e[1] / le, e[2] / le)
    w = _cross(n, u)
    return [(_dot(_sub(p, points[0]), u), _dot(_sub(p, points[0]), w)) for p in points]


def unroll(tube: Tube) -> dict[str, Vec2]:
    """Lay the strip flat. Returns label -> (x, y), seen from outside, rotated so the line from the first to the last
    inlet vertex runs along +X and translated so the minimum corner of the vertices is (0, 0)."""
    pos: dict[str, Vec2] = {}
    for i, face in enumerate(tube.faces):
        local = _local_coords([tube.xyz(label) for label in face])
        loc = dict(zip(face, local, strict=True))
        if i == 0:
            pos.update(loc)
            continue
        shared = [label for label in face if label in tube.faces[i - 1]]
        if len(shared) != 2:
            raise ValueError(f"face {i} shares {len(shared)} vertices with the previous face, expected an edge")
        s0, s1 = shared
        a, b = loc[s0], loc[s1]
        big_a, big_b = pos[s0], pos[s1]
        theta = math.atan2(big_b[1] - big_a[1], big_b[0] - big_a[0]) - math.atan2(b[1] - a[1], b[0] - a[0])
        c, s = math.cos(theta), math.sin(theta)
        for label, p in loc.items():
            if label in pos:
                continue
            dx, dy = p[0] - a[0], p[1] - a[1]
            pos[label] = (big_a[0] + c * dx - s * dy, big_a[1] + s * dx + c * dy)
    first, last = pos[tube.inlet[0]], pos[tube.inlet[-1]]
    ang = -math.atan2(last[1] - first[1], last[0] - first[0])
    c, s = math.cos(ang), math.sin(ang)
    rot = {k: (c * p[0] - s * p[1], s * p[0] + c * p[1]) for k, p in pos.items()}
    minx = min(p[0] for p in rot.values())
    miny = min(p[1] for p in rot.values())
    return {k: (p[0] - minx, p[1] - miny) for k, p in rot.items()}


def net_edges(tube: Tube, pos: dict[str, Vec2]) -> list[NetEdge]:
    """Every distinct edge of the developed strip once (the seam edge appears twice, as start and end copies),
    in a deterministic order: inlet chain, outlet chain, seam start, seam end, then rulings in strip order."""
    out: list[NetEdge] = []

    def add(role: str, index: int, a: str, b: str) -> None:
        out.append(NetEdge(role, index, pos[a], pos[b], dist3(tube.xyz(a), tube.xyz(b))))

    for i in range(len(tube.inlet) - 1):
        add("inlet", i, tube.inlet[i], tube.inlet[i + 1])
    for i in range(len(tube.outlet) - 1):
        add("outlet", i, tube.outlet[i], tube.outlet[i + 1])
    add("seam_start", 0, tube.inlet[0], tube.outlet[0])
    add("seam_end", 0, tube.inlet[-1], tube.outlet[-1])
    known = {frozenset(e) for e in _chain_pairs(tube)}
    seen: set[frozenset[str]] = set(known)
    k = 0
    for face in tube.faces:
        for i, a in enumerate(face):
            b = face[(i + 1) % len(face)]
            key = frozenset((a, b))
            if key in seen:
                continue
            seen.add(key)
            add("ruling", k, a, b)
            k += 1
    return out


def _chain_pairs(tube: Tube) -> list[tuple[str, str]]:
    pairs = [(tube.inlet[i], tube.inlet[i + 1]) for i in range(len(tube.inlet) - 1)]
    pairs += [(tube.outlet[i], tube.outlet[i + 1]) for i in range(len(tube.outlet) - 1)]
    pairs += [(tube.inlet[0], tube.outlet[0]), (tube.inlet[-1], tube.outlet[-1])]
    return pairs


def net_outline(tube: Tube, pos: dict[str, Vec2]) -> tuple[list[Vec2], list[str]]:
    """The net (finished-size) boundary loop and the role of the edge leaving each vertex."""
    labels = list(tube.inlet) + list(reversed(tube.outlet))
    roles = (
        ["inlet"] * (len(tube.inlet) - 1)
        + ["seam_end"]
        + ["outlet"] * (len(tube.outlet) - 1)
        + ["seam_start"]
    )
    return [pos[label] for label in labels], roles


# ---------------------------------------------------------------- 2D geometry
def signed_area(poly: list[Vec2]) -> float:
    s = 0.0
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        s += p[0] * q[1] - q[0] * p[1]
    return 0.5 * s


def offset_outline(poly: list[Vec2], dists: list[float], miter_limit: float = 4.0) -> list[Vec2]:
    """Offset each edge i (poly[i] -> poly[i+1]) outward by dists[i] and join neighbours with mitres (bevel when the
    mitre would exceed `miter_limit` times the larger offset). Outward is away from the enclosed area."""
    n = len(poly)
    sgn = 1.0 if signed_area(poly) > 0 else -1.0
    lines = []
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        dx, dy = q[0] - p[0], q[1] - p[1]
        ln = math.hypot(dx, dy)
        if ln < EPS:
            raise ValueError("zero-length edge in net outline")
        d = (dx / ln, dy / ln)
        nrm = (sgn * d[1], -sgn * d[0])
        lines.append(((p[0] + dists[i] * nrm[0], p[1] + dists[i] * nrm[1]), d, nrm))
    out: list[Vec2] = []
    for i in range(n):
        (p0, d0, n0), (p1, d1, n1) = lines[i - 1], lines[i]
        d_prev, d_cur = dists[i - 1], dists[i]
        v = poly[i]
        a = (v[0] + d_prev * n0[0], v[1] + d_prev * n0[1])
        b = (v[0] + d_cur * n1[0], v[1] + d_cur * n1[1])
        cr = d0[0] * d1[1] - d0[1] * d1[0]
        hit = None
        if abs(cr) > 1e-9:
            t = ((p1[0] - p0[0]) * d1[1] - (p1[1] - p0[1]) * d1[0]) / cr
            hit = (p0[0] + t * d0[0], p0[1] + t * d0[1])
            limit = miter_limit * max(abs(d_prev), abs(d_cur), 1e-12)
            if math.hypot(hit[0] - v[0], hit[1] - v[1]) > limit:
                hit = None
        if hit is not None:
            out.append(hit)
        else:
            out.append(a)
            if math.hypot(a[0] - b[0], a[1] - b[1]) > EPS:
                out.append(b)
    return out


def _orient(a: Vec2, b: Vec2, c: Vec2) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def self_intersections(poly: list[Vec2], tol: float = 1e-6) -> list[tuple[int, int]]:
    """Index pairs of non-adjacent edges of the closed polygon that cross or touch."""
    n = len(poly)
    bad = []
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            c, d = poly[j], poly[(j + 1) % n]
            o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
            if o1 * o2 < -tol and o3 * o4 < -tol:
                bad.append((i, j))
    return bad


def min_edge(poly: list[Vec2]) -> float:
    return min(math.dist(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly)))
