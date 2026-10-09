"""Known-bad fixture generators for the duct-fab validator tests.

Each mutation takes a good build (or builds from a modified spec) and returns the files to hand to the validator,
together with the check names that must fail. The validator is always given the ORIGINAL spec.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "duct-fab"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses (postponed annotations) look the module up here
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ specs
def _base(fitting: str, geometry: dict[str, Any], mark: str) -> dict[str, Any]:
    return {
        "spec_version": "1",
        "fitting": fitting,
        "mark": mark,
        "units": "mm",
        "sheet_thickness_mm": 1.0,
        "seam": {"type": "pittsburgh", "allowance_mm": 25},
        "connection": {"type": "tdc", "allowance_mm": 30},
        "geometry": geometry,
    }


GOOD_SPECS: dict[str, dict[str, Any]] = {
    "rect_to_round": _base(
        "rect_to_round", {"width_mm": 600, "height_mm": 400, "diameter_mm": 300, "length_mm": 450}, "RR-01"),
    "rect_to_round_offset": _base(
        "rect_to_round",
        {"width_mm": 600, "height_mm": 400, "diameter_mm": 300, "length_mm": 450, "offset_x_mm": 60,
         "offset_y_mm": -40, "circle_segments": 24},
        "RR-02"),
    "rect_reducer": _base(
        "rect_reducer",
        {"width_in_mm": 600, "height_in_mm": 400, "width_out_mm": 400, "height_out_mm": 300, "length_mm": 500,
         "alignment": "flat_bottom"},
        "RD-01"),
    "rect_offset": _base(
        "rect_offset",
        {"width_mm": 500, "height_mm": 300, "length_mm": 600, "offset_x_mm": 150, "offset_y_mm": 100}, "OF-01"),
}

# Extra good cases: other alignments and zero allowances.
EXTRA_GOOD_SPECS: dict[str, dict[str, Any]] = {}
for _al in ("concentric", "flat_top", "flat_left", "flat_right"):
    _s = copy.deepcopy(GOOD_SPECS["rect_reducer"])
    _s["geometry"]["alignment"] = _al
    _s["mark"] = f"RD-{_al}"
    EXTRA_GOOD_SPECS[f"rect_reducer_{_al}"] = _s
_z = copy.deepcopy(GOOD_SPECS["rect_to_round"])
_z["mark"] = "RR-ZERO"
_z["seam"]["allowance_mm"] = 0
_z["connection"]["allowance_mm"] = 0
EXTRA_GOOD_SPECS["rect_to_round_zero_allowances"] = _z
_z2 = copy.deepcopy(GOOD_SPECS["rect_offset"])
_z2["mark"] = "OF-ZERO"
_z2["seam"]["allowance_mm"] = 0
_z2["connection"]["allowance_mm"] = 0
EXTRA_GOOD_SPECS["rect_offset_zero_allowances"] = _z2

# the geometry key whose change must show up in a specific STEP check
DIMENSION_CHANGE: dict[str, tuple[str, float, str]] = {
    "rect_to_round": ("width_mm", 30.0, "step_inlet_cap"),
    "rect_to_round_offset": ("diameter_mm", 40.0, "step_outlet_cap"),
    "rect_reducer": ("width_out_mm", 40.0, "step_outlet_cap"),
    "rect_offset": ("offset_x_mm", 30.0, "step_outlet_offset"),
}


# ------------------------------------------------------------------ environment
@dataclass
class Env:
    spec: dict[str, Any]
    key: str
    good: dict[str, Path]  # step / dxf / manifest from a good build of `spec`
    work: Path
    build_fn: Callable[[dict[str, Any], Path], Any]
    _n: int = field(default=0)

    def build(self, spec: dict[str, Any]) -> dict[str, Path]:
        self._n += 1
        out = self.work / f"variant{self._n}"
        self.build_fn(spec, out)
        return find_outputs(out)

    def scratch(self, name: str) -> Path:
        self.work.mkdir(parents=True, exist_ok=True)
        return self.work / name


def find_outputs(out: Path) -> dict[str, Path]:
    return {
        "step": next(out.glob("*.step")),
        "dxf": next(out.glob("*.dxf")),
        "manifest": out / "manifest.json",
    }


@dataclass(frozen=True)
class Mutation:
    fn: Callable[[Env], list[Path]]
    must_fail: tuple[str, ...]


def _edit_dxf(env: Env, name: str, editor: Callable[[Any], None]) -> Path:
    import ezdxf

    doc = ezdxf.readfile(str(env.good["dxf"]))
    editor(doc)
    out = env.scratch(name + ".dxf")
    doc.saveas(str(out))
    return out


def _cut(doc: Any) -> Any:
    return next(e for e in doc.modelspace().query("LWPOLYLINE") if e.dxf.layer == "CUT")


def _bend(doc: Any, role: str, index: int) -> Any:
    for e in doc.modelspace().query("LINE"):
        if e.dxf.layer == "BEND" and e.has_xdata("MEPFAB"):
            tags = [(t.code, t.value) for t in e.get_xdata("MEPFAB")]
            if tags[0][1] == role and tags[1][1] == index:
                return e
    raise LookupError((role, index))


def _extend_end(line: Any, mm: float) -> None:
    """Lengthen a LINE by `mm` along its own direction (so its length changes by exactly `mm`)."""
    (x0, y0, _), (x1, y1, _) = line.dxf.start, line.dxf.end
    ln = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    line.dxf.end = (x1 + mm * (x1 - x0) / ln, y1 + mm * (y1 - y0) / ln, 0.0)


def _step_dxf(env: Env, dxf: Path) -> list[Path]:
    return [env.good["step"], dxf]


# ---- DXF mutations
def cut_open(env: Env) -> list[Path]:
    def edit(doc: Any) -> None:
        _cut(doc).closed = False

    return _step_dxf(env, _edit_dxf(env, "cut_open", edit))


def cut_duplicate_vertex(env: Env) -> list[Path]:
    def edit(doc: Any) -> None:
        pl = _cut(doc)
        pts = [tuple(p) for p in pl.get_points("xy")]
        pts.insert(3, pts[3])
        pl.set_points(pts, format="xy")

    return _step_dxf(env, _edit_dxf(env, "cut_dup", edit))


def _crosses(pts: list[tuple[float, float]]) -> bool:
    """True when two non-adjacent segments of the closed polygon properly cross (own check, for picking a swap)."""

    def o(a: Any, b: Any, c: Any) -> float:
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    n = len(pts)
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            a, b, c, d = pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n]
            if o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0:
                return True
    return False


def cut_swap_vertices(env: Env) -> list[Path]:
    def edit(doc: Any) -> None:
        pl = _cut(doc)
        pts = [tuple(p) for p in pl.get_points("xy")]
        for k in range(len(pts) - 2):
            trial = list(pts)
            trial[k], trial[k + 1] = trial[k + 1], trial[k]
            if _crosses(trial):
                pl.set_points(trial, format="xy")
                return
        raise AssertionError("no vertex swap produces a crossing")

    return _step_dxf(env, _edit_dxf(env, "cut_swap", edit))


def cut_two_outlines(env: Env) -> list[Path]:
    def edit(doc: Any) -> None:
        pts = [tuple(p) for p in _cut(doc).get_points("xy")]
        doc.modelspace().add_lwpolyline(pts, close=True, dxfattribs={"layer": "CUT"})

    return _step_dxf(env, _edit_dxf(env, "cut_two", edit))


def bend_ruling_moved(env: Env) -> list[Path]:
    def edit(doc: Any) -> None:
        _extend_end(_bend(doc, "ruling", 0), 2.0)

    return _step_dxf(env, _edit_dxf(env, "bend_ruling", edit))


def bend_inlet_gap(env: Env) -> list[Path]:
    def edit(doc: Any) -> None:
        _extend_end(_bend(doc, "inlet", 1), 2.0)

    return _step_dxf(env, _edit_dxf(env, "bend_gap", edit))


def bend_untagged(env: Env) -> list[Path]:
    def edit(doc: Any) -> None:
        _bend(doc, "inlet", 0).discard_xdata("MEPFAB")

    return _step_dxf(env, _edit_dxf(env, "bend_untagged", edit))


def bend_line_dropped(env: Env) -> list[Path]:
    def edit(doc: Any) -> None:
        doc.modelspace().delete_entity(_bend(doc, "ruling", 0))

    return _step_dxf(env, _edit_dxf(env, "bend_dropped", edit))


def title_wrong(env: Env) -> list[Path]:
    def edit(doc: Any) -> None:
        for t in doc.modelspace().query("TEXT"):
            t.dxf.text = re.sub(r"SHEET t=\S+", "SHEET t=9", t.dxf.text)

    return _step_dxf(env, _edit_dxf(env, "title", edit))


def dxf_garbage(env: Env) -> list[Path]:
    p = env.scratch("garbage.dxf")
    p.write_bytes(b"this is not a dxf file\n\x00\x01\x02")
    return [env.good["step"], p]


# ---- spec-differing builds (validated against the original spec)
def seam_dropped(env: Env) -> list[Path]:
    s = copy.deepcopy(env.spec)
    s["seam"]["allowance_mm"] = 0
    b = env.build(s)
    return [b["step"], b["dxf"]]


def connection_wrong(env: Env) -> list[Path]:
    s = copy.deepcopy(env.spec)
    s["connection"]["allowance_mm"] = env.spec["connection"]["allowance_mm"] - 20
    b = env.build(s)
    return [b["step"], b["dxf"]]


def step_other_length(env: Env) -> list[Path]:
    s = copy.deepcopy(env.spec)
    s["geometry"]["length_mm"] += 20
    return [env.build(s)["step"], env.good["dxf"]]


def step_other_dimension(env: Env) -> list[Path]:
    key, delta, _ = DIMENSION_CHANGE[env.key]
    s = copy.deepcopy(env.spec)
    s["geometry"][key] += delta
    return [env.build(s)["step"], env.good["dxf"]]


# ---- STEP file damage
def step_garbage(env: Env) -> list[Path]:
    p = env.scratch("garbage.step")
    p.write_text("this is not a STEP file\n", encoding="utf-8")
    return [p, env.good["dxf"]]


def step_truncated(env: Env) -> list[Path]:
    data = env.good["step"].read_bytes()
    p = env.scratch("truncated.step")
    p.write_bytes(data[: len(data) // 2])
    return [p, env.good["dxf"]]


def step_shell_not_solid(env: Env) -> list[Path]:
    import cadquery as cq

    wp = cq.importers.importStep(str(env.good["step"]))
    shell = wp.solids().vals()[0].Shells()[0]
    p = env.scratch("shell.step")
    cq.exporters.export(cq.Workplane(obj=shell), str(p), exportType="STEP")
    return [p, env.good["dxf"]]


def step_empty_file(env: Env) -> list[Path]:
    p = env.scratch("empty.step")
    p.write_bytes(b"")
    return [p, env.good["dxf"]]


# ---- manifest
def manifest_bad_hash(env: Env) -> list[Path]:
    m = json.loads(env.good["manifest"].read_text(encoding="utf-8"))
    m["files"][1]["sha256"] = "0" * 64
    p = env.scratch("manifest.json")
    p.write_text(json.dumps(m), encoding="utf-8")
    return [env.good["step"], env.good["dxf"], p]


def manifest_bad_inputs(env: Env) -> list[Path]:
    m = json.loads(env.good["manifest"].read_text(encoding="utf-8"))
    m["inputs"]["sheet_thickness_mm"] = 2.0
    p = env.scratch("manifest.json")
    p.write_text(json.dumps(m), encoding="utf-8")
    return [env.good["step"], env.good["dxf"], p]


def manifest_stale_dxf(env: Env) -> list[Path]:
    def edit(doc: Any) -> None:
        doc.modelspace().add_text("EXTRA", dxfattribs={"layer": "ANNOTATION", "height": 3})

    dxf = _edit_dxf(env, "stale", edit)
    # same file name as the manifest lists, different bytes
    target = env.scratch("stale_dir") / env.good["dxf"].name
    target.parent.mkdir(exist_ok=True)
    shutil.copyfile(dxf, target)
    return [env.good["step"], target, env.good["manifest"]]


MUTATIONS: dict[str, Mutation] = {
    "cut_open": Mutation(cut_open, ("cut_closed_single",)),
    "cut_duplicate_vertex": Mutation(cut_duplicate_vertex, ("cut_no_zero_length",)),
    "cut_swap_vertices": Mutation(cut_swap_vertices, ("cut_no_self_intersection",)),
    "cut_two_outlines": Mutation(cut_two_outlines, ("cut_closed_single",)),
    "bend_ruling_moved": Mutation(bend_ruling_moved, ("edge_lengths_match_3d",)),
    "bend_inlet_gap": Mutation(bend_inlet_gap, ("net_connected", "edge_lengths_match_3d")),
    "bend_untagged": Mutation(bend_untagged, ("net_connected",)),
    "bend_line_dropped": Mutation(bend_line_dropped, ("edge_lengths_match_3d",)),
    "title_wrong": Mutation(title_wrong, ("title_block",)),
    "dxf_garbage": Mutation(dxf_garbage, ("dxf_loads", "dxf_layers", "cut_closed_single", "net_connected")),
    "seam_dropped": Mutation(seam_dropped, ("seam_allowance", "title_block")),
    "connection_wrong": Mutation(connection_wrong, ("connection_allowance", "title_block")),
    "step_other_length": Mutation(step_other_length, ("step_length", "edge_lengths_match_3d")),
    "step_other_dimension": Mutation(step_other_dimension, ()),  # per-fitting check added by the test
    "step_garbage": Mutation(step_garbage, ("step_solid", "step_length", "edge_lengths_match_3d")),
    "step_truncated": Mutation(step_truncated, ("step_solid",)),
    "step_shell_not_solid": Mutation(step_shell_not_solid, ("step_solid",)),
    "step_empty_file": Mutation(step_empty_file, ("step_solid",)),
    "manifest_bad_hash": Mutation(manifest_bad_hash, ("manifest",)),
    "manifest_bad_inputs": Mutation(manifest_bad_inputs, ("manifest",)),
    "manifest_stale_dxf": Mutation(manifest_stale_dxf, ("manifest",)),
}
