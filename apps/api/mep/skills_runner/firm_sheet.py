"""The firm's drawing standard applied to a drafting skill's DXF: layer names and styles from the firm's layer standard, and the firm's title block drawing stamped
on the sheet with the card's values filled in.

The skill's own output is NEVER changed: this makes an additional file `<name>.firm.dxf` from a released DXF, and only keeps it when an independent re-read confirms
that every original entity is still there (on its mapped layer), the styles are the standard's and the title block is present with its placeholders filled.
Geometry and text only: no compliance value, no network, no database.

Layer standard JSON: {"layers": {"NAME": {"color": 3, "linetype": "CONTINUOUS"}}, "map": {"SKILL-LAYER": "FIRM-LAYER"}} (`map` optional).
Title block: a DXF drawing; TEXT and MTEXT containing {{MARK}} {{SKILL}} {{PROJECT}} {{TITLE}} {{DRAWING_NO}} {{REVISION}} {{DATE}} {{DRAWN_BY}} {{FIRM}} {{NOTES}} are filled in.
"""
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from mep.skills_runner.runner import RunFile, SkillRunResult

PLACEHOLDER = re.compile(r"\{\{([A-Z_]{2,20})\}\}")
CONTROL = re.compile("[\x00-\x1f\x7f\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")
KEYS = ("MARK", "SKILL", "PROJECT", "TITLE", "DRAWING_NO", "REVISION", "DATE", "DRAWN_BY", "FIRM", "NOTES")
MAX_ENTITIES = 5000
GAP_MM = 500.0
SIZE_LIMIT_MM = 200000.0


@dataclass
class FirmTemplates:
    title_block: bytes | None = None
    layer_standard: dict[str, Any] | None = None
    title_block_name: str | None = None
    layer_standard_name: str | None = None

    @property
    def any(self) -> bool:
        return self.title_block is not None or self.layer_standard is not None


def values_for(skill: str, spec: dict[str, Any]) -> dict[str, str]:
    tb = spec.get("title_block") if isinstance(spec.get("title_block"), dict) else {}

    def clean(v: Any) -> str:
        text = CONTROL.sub("", str(v)).replace(chr(92), "/").replace("{", "(").replace("}", ")").replace("%%", "%")      # no MTEXT or %% format codes from card text
        return text.strip()[:80] or "-"

    return {"MARK": clean(spec.get("mark", "-")), "SKILL": clean(skill), "PROJECT": clean(tb.get("project", spec.get("mark", "-"))),
            "TITLE": clean(tb.get("drawing_title", skill)), "DRAWING_NO": clean(tb.get("drawing_no", "-")), "REVISION": clean(tb.get("revision", "-")),
            "DATE": clean(tb.get("date", "-")), "DRAWN_BY": clean(tb.get("drawn_by", "-")), "FIRM": clean(tb.get("firm", "-")), "NOTES": clean(spec.get("notes", "-"))}


def _read(data: bytes) -> Any:
    import ezdxf

    return ezdxf.read(io.StringIO(data.decode("utf-8", "replace")))


def _write(doc: Any) -> bytes:
    """Written the way the skills write their drawings (fixed meta data, ordered sections), so the same stamp always gives the same bytes."""
    import sys
    import tempfile
    from pathlib import Path

    from mep.skills_runner.registry import REPO_ROOT

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from skills.cad import cadkit

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.dxf"
        cadkit.save_dxf(doc, path)
        return path.read_bytes()


def _round(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 5) + 0.0
    if hasattr(v, "__iter__") and not isinstance(v, str | bytes):
        return tuple(_round(x) for x in v)
    return v


def signature(e: Any, mapping: dict[str, str]) -> str:
    """What an entity IS apart from its handle: type, mapped layer, every attribute, and the points of a polyline."""
    attrs = {k: _round(v) for k, v in e.dxfattribs().items() if k not in ("handle", "owner", "layer")}
    extra = tuple(_round(tuple(p)) for p in e.get_points("xyseb")) if e.dxftype() == "LWPOLYLINE" else ()
    layer = e.dxf.layer
    return repr((e.dxftype(), mapping.get(layer, layer), sorted(attrs.items()), extra))


def _texts(layout: Any) -> list[tuple[Any, str]]:
    out = []
    for e in layout:
        if e.dxftype() == "TEXT":
            out.append((e, e.dxf.text))
        elif e.dxftype() == "MTEXT":
            out.append((e, e.text))
    return out


def _fill(text: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), "-"), text)


def stamp(dxf: bytes, templates: FirmTemplates, values: dict[str, str]) -> bytes:
    """The drawing with the firm's layer standard and title block applied. Raises ValueError for anything it cannot place safely."""
    import ezdxf.bbox
    from ezdxf.addons.importer import Importer

    doc = _read(dxf)
    msp = doc.modelspace()
    box = ezdxf.bbox.extents(msp)
    if templates.layer_standard:
        std = templates.layer_standard
        for old, new in (std.get("map") or {}).items():
            if old not in doc.layers or new.upper() in ("0", "DEFPOINTS"):
                continue
            src = doc.layers.get(old)
            if new not in doc.layers:
                doc.layers.add(new, color=src.dxf.color, linetype=src.dxf.linetype)
            for layout in (msp, *[b for b in doc.blocks if not b.name.startswith("*")]):
                for e in layout:
                    if e.dxf.layer == old:
                        e.dxf.layer = new
            doc.layers.remove(old)
        for name, style in std.get("layers", {}).items():
            if name in doc.layers:
                lay = doc.layers.get(name)
                lay.dxf.color = int(style["color"])
                lay.dxf.linetype = style.get("linetype", "CONTINUOUS")
    if templates.title_block:
        tdoc = _read(templates.title_block)
        tmsp = tdoc.modelspace()
        if len(tmsp) == 0 or len(tmsp) > MAX_ENTITIES:
            raise ValueError("the title block drawing is empty or too large")
        tbox = ezdxf.bbox.extents(tmsp)
        if not tbox.has_data or tbox.size.x > SIZE_LIMIT_MM or tbox.size.y > SIZE_LIMIT_MM:
            raise ValueError("the title block drawing has no usable extent")
        obox = box if box.has_data else ezdxf.bbox.Extents([(0, 0, 0)])
        dx = obox.extmax.x - tbox.extmax.x
        dy = (obox.extmin.y - GAP_MM) - tbox.extmax.y
        for e in list(tmsp):
            e.translate(dx, dy, 0)
        for e, text in _texts(tmsp):
            filled = _fill(text, values)
            if e.dxftype() == "TEXT":
                e.dxf.text = filled
            else:
                e.text = filled
        importer = Importer(tdoc, doc)
        importer.import_entities(list(tmsp), msp)
        importer.finalize()
    return _write(doc)


def verify(original: bytes, stamped: bytes, templates: FirmTemplates, values: dict[str, str]) -> list[dict[str, Any]]:
    """An independent re-read: every check is measured from the two files."""
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, expected: Any, actual: Any) -> None:
        checks.append({"name": name, "passed": bool(ok), "expected": expected, "actual": actual})

    a, b = _read(original), _read(stamped)
    mapping = dict((templates.layer_standard or {}).get("map") or {})
    count_a = Counter(signature(e, mapping) for e in a.modelspace())
    count_b = Counter(signature(e, {}) for e in b.modelspace())
    missing = {k: v - count_b.get(k, 0) for k, v in count_a.items() if count_b.get(k, 0) < v}
    check("firm_sheet_original_entities_kept", not missing, "every original entity unchanged (geometry, text, attributes), on its mapped layer", [k[:120] for k in list(missing)[:3]])
    if templates.layer_standard:
        std = templates.layer_standard
        left = [old for old in mapping if old in b.layers and old not in mapping.values()]
        check("firm_sheet_layers_renamed", not left, "no skill layer name left where the standard maps it", left)
        bad = []
        for name, style in std.get("layers", {}).items():
            if name in b.layers:
                lay = b.layers.get(name)
                if lay.dxf.color != int(style["color"]) or str(lay.dxf.linetype).upper() != style.get("linetype", "CONTINUOUS"):
                    bad.append(f"{name}: {lay.dxf.color}/{lay.dxf.linetype}")
        check("firm_sheet_layer_styles", not bad, "colour and linetype as in the standard", bad[:5])
    if templates.title_block:
        tdoc = _read(templates.title_block)
        want_extra = len(tdoc.modelspace())
        extra = len(b.modelspace()) - len(a.modelspace())
        check("firm_sheet_title_block_present", extra >= want_extra, f"at least {want_extra} added entities", extra)
        texts = [t for _, t in _texts(b.modelspace())]
        check("firm_sheet_placeholders_filled", not any(PLACEHOLDER.search(t) for t in texts), "no {{...}} left", [t for t in texts if PLACEHOLDER.search(t)][:3])
        keys = {m.group(1) for _, t in _texts(tdoc.modelspace()) for m in PLACEHOLDER.finditer(t)}
        absent = [k for k in sorted(keys) if k in values and values[k] not in " ".join(texts)]
        check("firm_sheet_values_shown", not absent, "every filled value appears on the sheet", absent)
    return checks


def apply_to_result(result: SkillRunResult, skill: str, spec: dict[str, Any], templates: FirmTemplates | None) -> SkillRunResult:
    """Add `<stem>.firm.dxf` for each released DXF when the firm has templates and the re-read confirms the result. Never changes the original files."""
    if templates is None or not templates.any or not result.released:
        return result
    values = values_for(skill, spec)
    report: list[dict[str, Any]] = []
    extra: list[RunFile] = []
    for f in list(result.files):
        if f.role != "dxf" or f.content is None or not f.name.lower().endswith(".dxf"):
            continue
        try:
            stamped = stamp(f.content, templates, values)
            checks = verify(f.content, stamped, templates, values)
        except Exception as exc:  # noqa: BLE001 - a template that cannot be applied costs the firm sheet, never the release
            report.append({"file": f.name, "applied": False, "reason": f"{type(exc).__name__}: {str(exc)[:120]}"})
            continue
        ok = all(c["passed"] for c in checks)
        report.append({"file": f.name, "applied": ok, "checks": checks})
        if ok:
            name = f.name[:-4] + ".firm.dxf"
            extra.append(RunFile(name, "dxf_firm", f.media_type, len(stamped), hashlib.sha256(stamped).hexdigest(), stamped))
    validation = {**result.validation, "firm_sheet": report}
    return SkillRunResult(result.status, result.message, validation=validation, files=[*result.files, *extra], manifest=result.manifest)
