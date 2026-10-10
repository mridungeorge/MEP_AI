"""Independent validator for hvac-dxf output: re-reads the DXF from disk and compares it with the spec and the sizing schedule.

`validate(spec, files)` takes the NORMALISED spec (the `inputs` of manifest.json) and the produced files (.dxf, optionally manifest.json) and returns a
ValidationResult. Every number is measured from the file; the expected tag texts are rebuilt here from the SIZING SCHEDULE, not from the duct entries.
Tolerance: 0.5 mm on coordinates and widths.
"""
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOL_MM = 0.5
APPID = "MEPHVAC"
DEFAULT_LAYERS = {"duct_rect": "M-DUCT-RECT", "duct_round": "M-DUCT-ROUND", "terminal": "M-TERMINAL", "tag": "M-ANNO-TAG", "frame": "M-ANNO-FRAME", "title": "M-ANNO-TITB"}
ALLOWED_EXTRA_LAYERS = {"0", "DEFPOINTS"}
TITLE_LABELS = {"project": "PROJECT", "drawing_title": "TITLE", "drawing_no": "DRAWING NO", "revision": "REVISION", "date": "DATE", "drawn_by": "DRAWN BY", "firm": "FIRM"}


@dataclass
class ValidationResult:
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def failed(self) -> list[str]:
        return [c["name"] for c in self.checks if not c["passed"]]


def _size_text(size: dict[str, Any]) -> str:
    return f"{size['width_mm']:g}x{size['depth_mm']:g}" if size["shape"] == "rect" else f"dia{size['diameter_mm']:g}"


def _xdata(entity: Any) -> tuple[str, str] | None:
    try:
        data = entity.get_xdata(APPID)
    except Exception:  # noqa: BLE001 - no xdata of ours
        return None
    vals = [v for code, v in data if code == 1000]
    return (str(vals[0]), str(vals[1])) if len(vals) >= 2 else None


def _poly_pts(entity: Any) -> list[tuple[float, float]]:
    return [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]


def balance(spec: dict[str, Any]) -> list[tuple[str, float, float, float]]:
    systems: dict[str, dict[str, float]] = {}
    for d in spec["ducts"]:
        s = systems.setdefault(d["system"], {"trunk": 0.0, "terminals": 0.0, "n": 0})
        s["trunk"] = max(s["trunk"], float(d["airflow_ls"]))
    for t in spec.get("terminals", []):
        s = systems.setdefault(t["system"], {"trunk": 0.0, "terminals": 0.0, "n": 0})
        s["terminals"] += float(t["airflow_ls"])
        s["n"] += 1
    out = []
    for name, s in sorted(systems.items()):
        if s["n"] == 0:
            continue                                     # no terminals drawn for this system: nothing to balance against
        big = max(s["trunk"], s["terminals"])
        out.append((name, s["trunk"], s["terminals"], abs(s["trunk"] - s["terminals"]) / big * 100.0 if big else 0.0))
    return out


def validate(spec: dict[str, Any], files: list[Path]) -> ValidationResult:
    import ezdxf

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, expected: Any, actual: Any, tol: Any = None) -> None:
        checks.append({"name": name, "passed": bool(ok), "expected": expected, "actual": actual, "tolerance": tol})

    dxf = next((Path(f) for f in files if str(f).lower().endswith(".dxf")), None)
    manifest = next((Path(f) for f in files if Path(f).name == "manifest.json"), None)
    if dxf is None:
        check("dxf_present", False, "a .dxf file", None)
        return ValidationResult(False, checks)
    try:
        doc = ezdxf.readfile(str(dxf))
    except Exception as exc:  # noqa: BLE001 - unreadable file
        check("dxf_readable", False, "a readable DXF", f"{type(exc).__name__}")
        return ValidationResult(False, checks)
    msp = doc.modelspace()
    names = {**DEFAULT_LAYERS, **(spec.get("layers") or {})}
    check("units_mm", doc.header.get("$INSUNITS") == 4, 4, doc.header.get("$INSUNITS"))
    present = {layer.dxf.name for layer in doc.layers}
    check("layers_present", set(names.values()) <= present, sorted(names.values()), sorted(present & set(names.values())))
    stray = {e.dxf.layer for e in msp} - set(names.values()) - ALLOWED_EXTRA_LAYERS
    check("no_foreign_layers", not stray, [], sorted(stray))

    # --- the drawing's ducts against the sizing schedule
    sched = {s["tag"]: s for s in spec["sizing_schedule"]}
    ducts = {d["tag"]: d for d in spec["ducts"]}
    mismatches = []
    if set(sched) != set(ducts):
        mismatches.append(f"tags differ: only in drawing {sorted(set(ducts) - set(sched))}, only in schedule {sorted(set(sched) - set(ducts))}")
    for tag in sorted(set(sched) & set(ducts)):
        if sched[tag]["size"] != ducts[tag]["size"] or not math.isclose(float(sched[tag]["airflow_ls"]), float(ducts[tag]["airflow_ls"]), abs_tol=1e-9):
            mismatches.append(f"{tag}: drawn {_size_text(ducts[tag]['size'])} {ducts[tag]['airflow_ls']:g} L/s, "
                              f"scheduled {_size_text(sched[tag]['size'])} {sched[tag]['airflow_ls']:g} L/s")
    check("schedule_matches_drawing", not mismatches, "every duct matches its sizing schedule entry", mismatches[:5])

    # --- ducts in the file
    found: dict[str, list[Any]] = {}
    texts: dict[str, list[str]] = {}
    term_ins: dict[str, list[Any]] = {}
    term_txt: dict[str, list[str]] = {}
    title_txt: list[str] = []
    for e in msp:
        x = _xdata(e)
        if x is None:
            continue
        role, tag = x
        if role == "duct" and e.dxftype() in ("LWPOLYLINE", "LINE"):
            found.setdefault(tag, []).append(e)
        elif role == "duct_tag" and e.dxftype() == "TEXT":
            texts.setdefault(tag, []).append(e.dxf.text)
        elif role == "terminal" and e.dxftype() == "INSERT":
            term_ins.setdefault(tag, []).append(e)
        elif role == "terminal_tag" and e.dxftype() == "TEXT":
            term_txt.setdefault(tag, []).append(e.dxf.text)
        elif role == "title" and e.dxftype() == "TEXT":
            title_txt.append(e.dxf.text)
    check("ducts_drawn_once", set(found) == set(ducts) and all(len(v) == 1 for v in found.values()), sorted(ducts), {k: len(v) for k, v in sorted(found.items())})
    bad_geo = []
    for tag, d in ducts.items():
        ent = (found.get(tag) or [None])[0]
        if ent is None:
            continue
        s, e_ = d["start_mm"], d["end_mm"]
        want_len = math.dist(s, e_)
        w = float(d["size"]["width_mm"]) if d["size"]["shape"] == "rect" else float(d["size"]["diameter_mm"])
        want_layer = names["duct_rect"] if d["size"]["shape"] == "rect" else names["duct_round"]
        if ent.dxf.layer != want_layer:
            bad_geo.append(f"{tag}: layer {ent.dxf.layer}")
        if ent.dxftype() == "LINE":
            if (spec["mode"] != "schematic" or math.dist((ent.dxf.start.x, ent.dxf.start.y), tuple(s)) > TOL_MM
                    or math.dist((ent.dxf.end.x, ent.dxf.end.y), tuple(e_)) > TOL_MM):
                bad_geo.append(f"{tag}: centreline")
        else:
            pts = _poly_pts(ent)
            if spec["mode"] != "layout" or len(pts) != 4 or not ent.closed:
                bad_geo.append(f"{tag}: outline")
                continue
            if abs(math.dist(pts[0], pts[1]) - want_len) > TOL_MM or abs(math.dist(pts[1], pts[2]) - w) > TOL_MM:
                bad_geo.append(f"{tag}: length {math.dist(pts[0], pts[1]):.1f} width {math.dist(pts[1], pts[2]):.1f}, expected {want_len:.1f} x {w:g}")
            mid = ((pts[0][0] + pts[2][0]) / 2, (pts[0][1] + pts[2][1]) / 2)
            if math.dist(mid, ((s[0] + e_[0]) / 2, (s[1] + e_[1]) / 2)) > TOL_MM:
                bad_geo.append(f"{tag}: position")
    check("duct_geometry", not bad_geo, [], bad_geo[:5], TOL_MM)

    # --- tags rebuilt from the SCHEDULE
    bad_tags = []
    for tag, s in sched.items():
        want = f"{tag} {_size_text(s['size'])} {float(s['airflow_ls']):g} L/s"
        got = texts.get(tag, [])
        if got != [want]:
            bad_tags.append(f"{tag}: expected {want!r}, found {got}")
    extra = sorted(set(texts) - set(sched))
    if extra:
        bad_tags.append(f"tags with no schedule entry: {extra}")
    check("tags_match_sizing_schedule", not bad_tags, "one tag per duct, size and airflow as scheduled", bad_tags[:5])

    # --- terminals
    want_t = {t["tag"]: t for t in spec.get("terminals", [])}
    bad_t = []
    if set(term_ins) != set(want_t) or any(len(v) != 1 for v in term_ins.values()):
        bad_t.append(f"symbols: {sorted(term_ins)} vs {sorted(want_t)}")
    for tag, t in want_t.items():
        ins = (term_ins.get(tag) or [None])[0]
        if ins is not None and (ins.dxf.name != "TERMINAL" or math.dist((ins.dxf.insert.x, ins.dxf.insert.y), tuple(t["at_mm"])) > TOL_MM):
            bad_t.append(f"{tag}: symbol or position")
        if term_txt.get(tag) != [f"{tag} {float(t['airflow_ls']):g} L/s"]:
            bad_t.append(f"{tag}: text {term_txt.get(tag)}")
    check("terminals", not bad_t, [], bad_t[:5], TOL_MM)

    # --- airflow balance per system (a drawing whose airflows do not add up is not released)
    tol = float(spec.get("balance_tolerance_pct", 1))
    off = [f"{n}: trunk {tr:g} L/s vs terminals {te:g} L/s ({d:.1f} %)" for n, tr, te, d in balance(spec) if d > tol]
    check("airflow_balance", not off, f"within {tol:g} %", off[:5], tol)

    # --- title block
    missing = []
    for key, label in TITLE_LABELS.items():
        if spec["title_block"].get(key) and f"{label}: {spec['title_block'][key]}" not in title_txt:
            missing.append(label)
    check("title_block", not missing, "every supplied title field", missing)

    if manifest is not None:
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            ok = all(hashlib.sha256(Path(dxf.parent / f["name"]).read_bytes()).hexdigest() == f["sha256"] for f in m["files"])
        except (OSError, ValueError, KeyError):
            ok = False
        check("manifest_checksums", ok, "files match the manifest", ok)
    return ValidationResult(all(c["passed"] for c in checks), checks)
