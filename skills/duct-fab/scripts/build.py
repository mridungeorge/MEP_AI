#!/usr/bin/env python3
"""duct-fab builder: spec card -> STEP solid + flat-pattern DXF + manifest.json.

    python skills/duct-fab/scripts/build.py --spec spec.json --out out/

Deterministic: the same spec card writes byte-identical files (in one toolchain). Geometry only: this script never
decides compliance, never touches the network or a database. Nothing is released until validator.py passes.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import os
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import jsonschema

from skills.cad import cadkit, develop
from skills.cad.develop import Tube

SKILL_VERSION = "1.0.0"
DEGENERATE_MM = 0.01  # a difference smaller than this is zero for the purpose of "is this really that fitting"
RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                            *(f"LPT{i}" for i in range(1, 10))})
FITTING_TITLES = {
    "rect_to_round": "RECTANGULAR TO ROUND TRANSITION",
    "rect_reducer": "RECTANGULAR REDUCER",
    "rect_offset": "RECTANGULAR OFFSET",
}


class SpecError(ValueError):
    """The spec card is not buildable."""


class ValidationFailed(RuntimeError):
    def __init__(self, result: Any):
        super().__init__("validator rejected the output; nothing was released")
        self.result = result


def num(v: float) -> str:
    """Number format used in the title block (the validator reads it back with the same rule)."""
    return format(round(float(v), 6) + 0.0, "g")


# ---------------------------------------------------------------- spec
def load_schema() -> dict[str, Any]:
    return json.loads((SKILL_DIR / "spec_card.json").read_text(encoding="utf-8"))


def normalise_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate against the schema, apply defaults, run the semantic checks. Returns a new dict."""
    _require_finite(spec)
    errors = sorted(jsonschema.Draft202012Validator(load_schema()).iter_errors(spec), key=lambda e: list(e.path))
    if errors:
        raise SpecError("; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors))
    out = copy.deepcopy(spec)
    g = out["geometry"]
    kind = out["fitting"]
    if out["mark"].upper() in RESERVED_NAMES:
        raise SpecError(f"mark {out['mark']!r} is a reserved file name on Windows")
    if kind == "rect_to_round":
        g.setdefault("offset_x_mm", 0)
        g.setdefault("offset_y_mm", 0)
        g["circle_segments"] = int(g.get("circle_segments", 16))  # the schema accepts 16.0 as an integer
    elif kind == "rect_reducer":
        if (abs(g["width_in_mm"] - g["width_out_mm"]) < DEGENERATE_MM
                and abs(g["height_in_mm"] - g["height_out_mm"]) < DEGENERATE_MM):
            raise SpecError("inlet and outlet are the same size: this is not a reducer (use rect_offset)")
    elif kind == "rect_offset" and abs(g["offset_x_mm"]) < DEGENERATE_MM and abs(g["offset_y_mm"]) < DEGENERATE_MM:
        raise SpecError("offset is zero in both directions: this is a straight duct, not an offset")
    return out


def _require_finite(node: Any, path: str = "<root>", depth: int = 0) -> None:
    """NaN and Infinity pass the JSON Schema range checks and would reach the title block and the manifest."""
    if depth > 20:
        raise SpecError(f"{path}: nested too deeply")
    if isinstance(node, float) and not math.isfinite(node):
        raise SpecError(f"{path}: {node} is not a number a spec card may hold")
    if isinstance(node, dict):
        for k, v in node.items():
            _require_finite(v, f"{path}/{k}", depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _require_finite(v, f"{path}/{i}", depth + 1)


def alignment_offset(g: dict[str, Any]) -> tuple[float, float]:
    """Outlet centre relative to inlet centre for a reducer."""
    dw = g["width_out_mm"] - g["width_in_mm"]
    dh = g["height_out_mm"] - g["height_in_mm"]
    return {
        "concentric": (0.0, 0.0),
        "flat_bottom": (0.0, dh / 2),
        "flat_top": (0.0, -dh / 2),
        "flat_left": (dw / 2, 0.0),
        "flat_right": (-dw / 2, 0.0),
    }[g["alignment"]]


# ---------------------------------------------------------------- tubes
def _round(v: tuple[float, float, float]) -> tuple[float, float, float]:
    return (cadkit.r6(v[0]), cadkit.r6(v[1]), cadkit.r6(v[2]))


def rect_to_round_tube(g: dict[str, Any]) -> Tube:
    w, h, d, length = g["width_mm"], g["height_mm"], g["diameter_mm"], g["length_mm"]
    ox, oy, n = g["offset_x_mm"], g["offset_y_mm"], g["circle_segments"]
    q = n // 4  # chords per quadrant
    half = q // 2  # the seam runs along the middle generator of corner 0
    verts: dict[str, tuple[float, float, float]] = {}
    for k, (x, y) in enumerate([(w / 2, h / 2), (-w / 2, h / 2), (-w / 2, -h / 2), (w / 2, -h / 2)]):
        verts[f"C{k}"] = _round((x, y, 0.0))
    for j in range(n):
        a = 2 * math.pi * j / n
        verts[f"P{j}"] = _round((ox + d / 2 * math.cos(a), oy + d / 2 * math.sin(a), length))

    def p(j: int) -> str:
        return f"P{j % n}"

    faces: list[tuple[str, ...]] = []
    for k in range(4):
        for j in range(half if k == 0 else k * q, (k + 1) * q):
            faces.append((f"C{k}", p(j), p(j + 1)))
        faces.append((f"C{k}", "C0'" if k == 3 else f"C{k + 1}", p((k + 1) * q)))
    for j in range(half):
        faces.append(("C0'", p(j), p(j + 1) + ("'" if j + 1 == half else "")))
    inlet = ("C0", "C1", "C2", "C3", "C0'")
    outlet = tuple([p(half)] + [p(j) for j in range(half + 1, n)] + [p(j) for j in range(half)] + [p(half) + "'"])
    return Tube(verts, tuple(develop.orient_outward(verts, faces)), inlet, outlet)


def frustum_tube(w1: float, h1: float, w2: float, h2: float, length: float, ox: float, oy: float) -> Tube:
    """Rectangle to rectangle with parallel sides; the outlet centre is (ox, oy) from the inlet centre."""
    verts: dict[str, tuple[float, float, float]] = {}
    for k, (sx, sy) in enumerate([(1, 1), (-1, 1), (-1, -1), (1, -1)]):
        verts[f"C{k}"] = _round((sx * w1 / 2, sy * h1 / 2, 0.0))
        verts[f"O{k}"] = _round((ox + sx * w2 / 2, oy + sy * h2 / 2, length))
    faces: list[tuple[str, ...]] = [
        ("C0", "C1", "O1", "O0"),
        ("C1", "C2", "O2", "O1"),
        ("C2", "C3", "O3", "O2"),
        ("C3", "C0'", "O0'", "O3"),
    ]
    return Tube(
        verts,
        tuple(develop.orient_outward(verts, faces)),
        ("C0", "C1", "C2", "C3", "C0'"),
        ("O0", "O1", "O2", "O3", "O0'"),
    )


def make_tube(spec: dict[str, Any]) -> Tube:
    g, kind = spec["geometry"], spec["fitting"]
    if kind == "rect_to_round":
        return rect_to_round_tube(g)
    if kind == "rect_reducer":
        ox, oy = alignment_offset(g)
        return frustum_tube(g["width_in_mm"], g["height_in_mm"], g["width_out_mm"], g["height_out_mm"],
                            g["length_mm"], ox, oy)
    return frustum_tube(g["width_mm"], g["height_mm"], g["width_mm"], g["height_mm"], g["length_mm"],
                        g["offset_x_mm"], g["offset_y_mm"])


# ---------------------------------------------------------------- DXF
def _text(msp: Any, s: str, at: tuple[float, float], h: float, rotation: float = 0.0, centre: bool = False) -> None:
    from ezdxf.enums import TextEntityAlignment

    t = msp.add_text(s, height=h, rotation=rotation, dxfattribs={"layer": "ANNOTATION"})
    t.set_placement((cadkit.r6(at[0]), cadkit.r6(at[1])),
                    align=TextEntityAlignment.BOTTOM_CENTER if centre else TextEntityAlignment.LEFT)


def _line(msp: Any, a: tuple[float, float], b: tuple[float, float], layer: str) -> Any:
    return msp.add_line((cadkit.r6(a[0]), cadkit.r6(a[1])), (cadkit.r6(b[0]), cadkit.r6(b[1])),
                        dxfattribs={"layer": layer})


def _dimension(msp: Any, p: tuple[float, float], q: tuple[float, float], off: float, h: float) -> None:
    """Aligned dimension drawn as plain ANNOTATION geometry: extension lines, dimension line, ticks and text."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux
    sgn = 1.0 if off >= 0 else -1.0
    p2 = (p[0] + off * nx, p[1] + off * ny)
    q2 = (q[0] + off * nx, q[1] + off * ny)
    over = 0.4 * h * sgn
    _line(msp, p, (p2[0] + over * nx, p2[1] + over * ny), "ANNOTATION")
    _line(msp, q, (q2[0] + over * nx, q2[1] + over * ny), "ANNOTATION")
    _line(msp, p2, q2, "ANNOTATION")
    for c in (p2, q2):
        k = 0.4 * h
        _line(msp, (c[0] - k * (ux + nx), c[1] - k * (uy + ny)), (c[0] + k * (ux + nx), c[1] + k * (uy + ny)),
              "ANNOTATION")
    ang = math.degrees(math.atan2(uy, ux))
    if ang > 90 or ang <= -90:
        ang += 180 if ang <= -90 else -180
    mid = ((p2[0] + q2[0]) / 2 + 0.5 * h * nx * sgn, (p2[1] + q2[1]) / 2 + 0.5 * h * ny * sgn)
    _text(msp, f"{length:.1f}", mid, h, rotation=ang, centre=True)


def title_lines(spec: dict[str, Any]) -> list[str]:
    g, kind = spec["geometry"], spec["fitting"]
    if kind == "rect_to_round":
        size = (f"INLET: {num(g['width_mm'])} x {num(g['height_mm'])}  OUTLET: DIA {num(g['diameter_mm'])}  "
                f"LENGTH: {num(g['length_mm'])}")
        extra = f"OFFSET: X {num(g['offset_x_mm'])} Y {num(g['offset_y_mm'])}  CHORDS: {g['circle_segments']}"
    elif kind == "rect_reducer":
        size = (f"INLET: {num(g['width_in_mm'])} x {num(g['height_in_mm'])}  "
                f"OUTLET: {num(g['width_out_mm'])} x {num(g['height_out_mm'])}  LENGTH: {num(g['length_mm'])}")
        extra = f"ALIGNMENT: {g['alignment']}"
    else:
        size = f"SIZE: {num(g['width_mm'])} x {num(g['height_mm'])}  LENGTH: {num(g['length_mm'])}"
        extra = f"OFFSET: X {num(g['offset_x_mm'])} Y {num(g['offset_y_mm'])}"
    lines = [
        f"{FITTING_TITLES[kind]}   MARK: {spec['mark']}",
        size,
        extra,
        f"SHEET t={num(spec['sheet_thickness_mm'])} mm" + (f"  MATERIAL: {spec['material']}" if spec.get("material") else ""),
        f"SEAM: {spec['seam']['type']} +{num(spec['seam']['allowance_mm'])} mm",
        f"CONN: {spec['connection']['type']} +{num(spec['connection']['allowance_mm'])} mm (inlet and outlet)",
        "UNITS: mm   VIEW: OUTSIDE   DEVELOPMENT: TRIANGULATION",
        "GEOMETRY ONLY - NOT A COMPLIANCE CHECK",
    ]
    if spec.get("notes"):
        lines.append(f"NOTES: {spec['notes']}")
    return lines


def build_pattern(spec: dict[str, Any], tube: Tube) -> tuple[Any, dict[str, Any]]:
    """Returns (ezdxf document, pattern measures)."""
    pos = develop.unroll(tube)
    edges = develop.net_edges(tube, pos)
    net, roles = develop.net_outline(tube, pos)
    if develop.self_intersections(net):
        raise SpecError("the developed net overlaps itself; the fitting is too short or too offset to develop")
    sa = spec["seam"]["allowance_mm"]
    ca = spec["connection"]["allowance_mm"]
    dist = {"inlet": ca, "outlet": ca, "seam_end": sa, "seam_start": 0.0}
    outline = develop.offset_outline(net, [dist[r] for r in roles])
    if develop.self_intersections(outline) or develop.min_edge(outline) < 0.1:
        raise SpecError("the allowances are too large for this geometry (the cut outline folds over itself)")
    minx = min(x for x, _ in outline)
    miny = min(y for _, y in outline)
    shift = (-minx, -miny)

    def mv(pt: tuple[float, float]) -> tuple[float, float]:
        return (pt[0] + shift[0], pt[1] + shift[1])

    outline = [mv(pt) for pt in outline]
    doc = cadkit.new_dxf()
    msp = doc.modelspace()
    for e in edges:
        ent = _line(msp, mv(e.p), mv(e.q), "BEND")
        ent.set_xdata(cadkit.DXF_APPID, [(1000, e.role), (1070, e.index)])
    msp.add_lwpolyline([(cadkit.r6(x), cadkit.r6(y)) for x, y in outline], close=True, dxfattribs={"layer": "CUT"})

    width = max(x for x, _ in outline)
    height = max(y for _, y in outline)
    th = round(max(3.5, min(max(width, height) / 120, 12.0)), 1)
    _dimension(msp, (0.0, 0.0), (width, 0.0), -3.0 * th, th)
    _dimension(msp, (width, 0.0), (width, height), 3.0 * th, th)
    seam = next(e for e in edges if e.role == "seam_start")
    _dimension(msp, mv(seam.p), mv(seam.q), 3.0 * th, th)
    lines = title_lines(spec)
    box_w = max(len(s) for s in lines) * th * 0.75 + 2 * th
    box_h = (len(lines) + 1) * th * 1.6
    top = -8.0 * th
    box = [(0.0, top), (box_w, top), (box_w, top - box_h), (0.0, top - box_h)]
    msp.add_lwpolyline([(cadkit.r6(x), cadkit.r6(y)) for x, y in box], close=True, dxfattribs={"layer": "ANNOTATION"})
    for i, s in enumerate(lines):
        _text(msp, s, (th, top - (i + 1) * th * 1.6), th)

    net_area = abs(develop.signed_area(net))
    measures = {
        "net_area_mm2": round(net_area, 2),
        "outline_area_mm2": round(abs(develop.signed_area(outline)), 2),
        "outline_bbox_mm": [round(width, 2), round(height, 2)],
        "net_edge_count": len(edges),
        "seam_edge_length_mm": round(seam.length_3d, 2),
    }
    return doc, measures


# ---------------------------------------------------------------- build + gate
def _load_validator() -> Any:
    path = SKILL_DIR / "validator.py"
    mod_spec = importlib.util.spec_from_file_location("duct_fab_validator", path)
    assert mod_spec and mod_spec.loader
    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules["duct_fab_validator"] = mod
    mod_spec.loader.exec_module(mod)
    return mod


def _toolchain() -> dict[str, str]:
    out = {}
    for dist in ("cadquery", "cadquery-ocp", "ezdxf"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = "unknown"
    return out


def build(spec_in: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """Build into a scratch folder, run the validator, and only then publish the files into `out_dir`.
    Raises SpecError for a bad card and ValidationFailed (with nothing written to `out_dir`) for a bad output."""
    spec = normalise_spec(spec_in)
    tube = make_tube(spec)
    solid = cadkit.solid_from_tube(tube)
    doc, pattern = build_pattern(spec, tube)
    mark = spec["mark"]
    out_dir = Path(out_dir)
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        step_path, dxf_path = work / f"{mark}.step", work / f"{mark}.dxf"
        cadkit.write_step(solid, step_path, mark)
        cadkit.save_dxf(doc, dxf_path)
        result = _load_validator().validate(spec, [step_path, dxf_path])
        if not result.passed:
            raise ValidationFailed(result)
        bb = solid.BoundingBox()
        manifest = {
            "skill": "duct-fab",
            "skill_version": SKILL_VERSION,
            "inputs": spec,
            "spec_sha256": cadkit.sha256_bytes(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()),
            "files": [
                {"name": p.name, "role": role, "bytes": p.stat().st_size, "sha256": cadkit.sha256_file(p)}
                for role, p in (("step", step_path), ("dxf", dxf_path))
            ],
            "measures": {
                "solid_bbox_mm": [round(bb.xlen, 2), round(bb.ylen, 2), round(bb.zlen, 2)],
                "solid_volume_mm3": round(solid.Volume(), 0),
                "pattern": pattern,
            },
            "validation": {"passed": True, "checks": [c["name"] for c in result.checks]},
            "toolchain": _toolchain(),
        }
        _publish(out_dir, [(step_path.name, step_path.read_bytes()), (dxf_path.name, dxf_path.read_bytes()),
                           ("manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())])
    return manifest


def _publish(out_dir: Path, files: list[tuple[str, bytes]]) -> None:
    """Stage every file next to its destination, then move them into place (the manifest last). If anything fails, the files
    this call placed are removed and the previous build's files are put back: the folder never holds half of a build, and a
    failed rebuild never destroys the last good one."""
    out_dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    backups: list[tuple[Path, Path]] = []
    placed: list[Path] = []
    try:
        for name, data in files:
            final = out_dir / name
            if final.exists() and not final.is_file():
                raise OSError(f"{final} exists and is not a file")
            fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=name + ".stage.")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            staged.append((final, Path(tmp)))
        for final, tmp in staged:
            if final.exists():
                backup = final.with_name(final.name + ".previous")
                os.replace(final, backup)
                backups.append((final, backup))
            os.replace(tmp, final)
            placed.append(final)
    except BaseException:
        for final in placed:
            final.unlink(missing_ok=True)
        for final, backup in backups:
            os.replace(backup, final)
        for _, tmp in staged:
            tmp.unlink(missing_ok=True)
        raise
    for _, backup in backups:
        backup.unlink(missing_ok=True)


def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not allowed in a spec card")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build a duct fitting: STEP + flat-pattern DXF + manifest.json")
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, ValueError, RecursionError) as exc:        # includes JSONDecodeError, bad UTF-8, NaN, deep nesting
        print(f"spec rejected: {exc}", file=sys.stderr)
        return 2
    try:
        manifest = build(spec, args.out)
    except SpecError as exc:
        print(f"spec rejected: {exc}", file=sys.stderr)
        return 2
    except ValidationFailed as exc:
        for c in exc.result.checks:
            if not c["passed"]:
                print(f"FAILED {c['name']}: expected {c['expected']} actual {c['actual']} (tol {c['tolerance']})",
                      file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - a kernel error or an output that cannot be written: nothing is released
        print(f"build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4
    print(json.dumps({"out": str(args.out), "files": [f["name"] for f in manifest["files"]] + ["manifest.json"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
