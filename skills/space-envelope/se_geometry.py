"""Plan geometry for the space-envelope skill: pure Python, millimetres, no CAD kernel, no network.

A room outline is a simple polygon (no crossing edges). `normalise_outline` returns it counter-clockwise, rounded to 1e-3 mm, starting at
its lowest-then-leftmost vertex, so equal rooms always give equal files.
"""
import math

Pt = tuple[float, float]
EPS = 1e-9


def r3(x: float) -> float:
    return round(x, 3) + 0.0


def signed_area(poly: list[Pt]) -> float:
    """Shoelace; positive for counter-clockwise."""
    return 0.5 * sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly)))


def area_m2(poly: list[Pt]) -> float:
    return abs(signed_area(poly)) / 1e6


def _orient(a: Pt, b: Pt, c: Pt) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Pt, b: Pt, p: Pt) -> bool:
    return (min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS
            and abs(_orient(a, b, p)) <= 1e-6 * max(1.0, math.dist(a, b)))


def segments_properly_cross(a: Pt, b: Pt, c: Pt, d: Pt) -> bool:
    """True when the open segments ab and cd cross at a single interior point of both."""
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    return (o1 * o2 < -EPS) and (o3 * o4 < -EPS)


def segments_touch_or_cross(a: Pt, b: Pt, c: Pt, d: Pt) -> bool:
    if segments_properly_cross(a, b, c, d):
        return True
    return _on_segment(a, b, c) or _on_segment(a, b, d) or _on_segment(c, d, a) or _on_segment(c, d, b)


def is_simple(poly: list[Pt]) -> bool:
    """No two non-adjacent edges touch, and adjacent edges meet only at their shared vertex; at least 3 distinct vertices."""
    n = len(poly)
    if n < 3 or len({(round(x, 6), round(y, 6)) for x, y in poly}) != n:
        return False
    edges = [(poly[i], poly[(i + 1) % n]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = edges[i]
            c, d = edges[j]
            if j == i + 1 or (i == 0 and j == n - 1):         # adjacent: they may share exactly one end, not overlap along a line
                shared = b if j == i + 1 else a
                other_i = a if shared is b else b
                other_j = d if j == i + 1 else c
                if abs(_orient(shared, other_i, other_j)) <= 1e-9 and (other_i[0] - shared[0]) * (other_j[0] - shared[0]) + \
                        (other_i[1] - shared[1]) * (other_j[1] - shared[1]) > 0:
                    return False                              # a spike: the two edges fold back onto each other
                continue
            if segments_touch_or_cross(a, b, c, d):
                return False
    return area_m2(poly) > 0


def normalise_outline(points: list[Pt]) -> list[Pt]:
    poly = [(r3(x), r3(y)) for x, y in points]
    if poly[0] == poly[-1]:
        poly = poly[:-1]
    if signed_area(poly) < 0:
        poly = poly[::-1]
    start = min(range(len(poly)), key=lambda i: (poly[i][1], poly[i][0]))
    return poly[start:] + poly[:start]


def rectangle(x: float, y: float, w: float, d: float) -> list[Pt]:
    return [(x, y), (x + w, y), (x + w, y + d), (x, y + d)]


def point_in_polygon(p: Pt, poly: list[Pt], strict: bool = False) -> bool:
    """Even-odd test; boundary counts as inside unless `strict`."""
    n = len(poly)
    for i in range(n):
        if _on_segment(poly[i], poly[(i + 1) % n], p):
            return not strict
    inside = False
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < (x2 - x1) * (p[1] - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


_TRI_CACHE: dict[tuple[Pt, ...], list[tuple[Pt, Pt, Pt]]] = {}


def triangulate(poly: list[Pt]) -> list[tuple[Pt, Pt, Pt]]:
    """Ear clipping of a simple polygon (any orientation) into triangles. Cached per outline (a build compares every pair of rooms)."""
    key = tuple(poly)
    hit = _TRI_CACHE.get(key)
    if hit is None:
        if len(_TRI_CACHE) > 512:
            _TRI_CACHE.clear()
        hit = _TRI_CACHE[key] = _triangulate(poly)
    return hit


def _triangulate(poly: list[Pt]) -> list[tuple[Pt, Pt, Pt]]:
    pts = list(poly) if signed_area(poly) > 0 else list(poly)[::-1]
    tris: list[tuple[Pt, Pt, Pt]] = []
    guard = 0
    while len(pts) > 3 and guard < 10_000:
        guard += 1
        n = len(pts)
        for i in range(n):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
            if _orient(a, b, c) <= EPS:                       # reflex or collinear: not an ear
                continue
            if any(q not in (a, b, c) and _point_in_triangle(q, a, b, c) for q in pts):
                continue
            tris.append((a, b, c))
            del pts[i]
            break
        else:                                                 # collinear leftovers: drop one collinear vertex and go on
            k = next((i for i in range(n) if abs(_orient(pts[i - 1], pts[i], pts[(i + 1) % n])) <= EPS), None)
            if k is None:
                raise ValueError("polygon could not be triangulated")
            del pts[k]
    if len(pts) == 3 and _orient(*pts) > EPS:
        tris.append((pts[0], pts[1], pts[2]))
    return tris


def _point_in_triangle(p: Pt, a: Pt, b: Pt, c: Pt) -> bool:
    d1, d2, d3 = _orient(a, b, p), _orient(b, c, p), _orient(c, a, p)
    return d1 >= -EPS and d2 >= -EPS and d3 >= -EPS


def _triangles_overlap(t1: tuple[Pt, Pt, Pt], t2: tuple[Pt, Pt, Pt], tol: float = 1e-6) -> bool:
    """Separating-axis test: the INTERIORS overlap unless some edge normal separates them (touching counts as separated)."""
    for tri in (t1, t2):
        for i in range(3):
            ax, ay = tri[i]
            bx, by = tri[(i + 1) % 3]
            nx, ny = ay - by, bx - ax
            norm = math.hypot(nx, ny) or 1.0
            nx, ny = nx / norm, ny / norm
            p1 = [nx * x + ny * y for x, y in t1]
            p2 = [nx * x + ny * y for x, y in t2]
            if max(p1) <= min(p2) + tol or max(p2) <= min(p1) + tol:
                return False
    return True


def interiors_overlap(a: list[Pt], b: list[Pt]) -> bool:
    """True when the two simple polygons share interior area (touching along an edge or at a corner is NOT an overlap)."""
    ax0, ay0, ax1, ay1 = bbox(a)
    bx0, by0, bx1, by1 = bbox(b)
    if ax1 <= bx0 + 1e-6 or bx1 <= ax0 + 1e-6 or ay1 <= by0 + 1e-6 or by1 <= ay0 + 1e-6:
        return False
    ta, tb = triangulate(a), triangulate(b)
    return any(_triangles_overlap(t, u) for t in ta for u in tb)


def label_point(poly: list[Pt]) -> Pt:
    """A point strictly inside the polygon: the middle of the longest interior stretch of a horizontal line through the polygon."""
    ys = sorted({p[1] for p in poly})
    lo, hi = ys[0], ys[-1]
    best: tuple[float, Pt] | None = None
    for frac in (0.5, 0.4, 0.6, 0.3, 0.7, 0.2, 0.8, 0.1, 0.9):
        y = lo + (hi - lo) * frac
        if any(abs(y - v) < 1e-6 for v in ys):
            y += (hi - lo) * 1e-3
        xs: list[float] = []
        n = len(poly)
        for i in range(n):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                xs.append((x2 - x1) * (y - y1) / (y2 - y1) + x1)
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            width = xs[k + 1] - xs[k]
            if best is None or width > best[0]:
                best = (width, (r3((xs[k] + xs[k + 1]) / 2), r3(y)))
        if best is not None and best[0] > 1e-3:
            break
    if best is None:
        raise ValueError("no interior point found")
    return best[1]


def bbox(poly: list[Pt]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)
