"""Clash-lite: axis-aligned boxes only. Other disciplines' IFC elements become bounding boxes; proposed ducts become boxes from their end points, half the section
either side, plus insulation. A pair is reported as OVERLAP (the boxes intersect) or CLEARANCE (the gap is less than the firm's clearance). These are WARNINGS for a
person to look at, never findings of compliance or non-compliance, and a box is bigger than a bent or diagonal element, so false alarms are expected.

Units: boxes are in millimetres in the model's own coordinates. A project's duct coordinates must be entered in the same frame as the other models.
"""
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

MAX_ELEMENTS = 20000
MAX_ATTEMPTS = 60000
READ_SECONDS = 120
READ_MEMORY = 3 * 1024 * 1024 * 1024
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(text: str | None) -> str | None:
    """Text without control characters (invalid in XML and in a spreadsheet cell)."""
    return None if text is None else _CONTROL.sub("", text)
MAX_BYTES = 100 * 1024 * 1024
SKIP_CLASSES = ("IfcSpace", "IfcOpeningElement", "IfcAnnotation", "IfcGrid", "IfcVirtualElement", "IfcSite", "IfcBuilding", "IfcBuildingStorey", "IfcProject")


@dataclass(frozen=True)
class Box:
    guid: str
    ifc_class: str
    name: str | None
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]


class ClashIfcError(Exception):
    """The file is not a readable IFC model."""


def read_boxes(path: Path) -> tuple[list[Box], int, list[str]]:
    """(boxes in mm, elements skipped for lack of geometry, problems). Never decides anything about the content."""
    import ifcopenshell
    import ifcopenshell.geom

    if path.stat().st_size > MAX_BYTES:
        raise ClashIfcError(f"the file is larger than {MAX_BYTES // (1024 * 1024)} MiB")
    try:
        model = ifcopenshell.open(str(path))
    except Exception as exc:
        raise ClashIfcError("not a readable IFC file") from exc
    settings = ifcopenshell.geom.settings()
    settings.set("use-world-coords", True)
    boxes: list[Box] = []
    skipped = 0
    problems: list[str] = []
    attempts = 0
    for element in model.by_type("IfcProduct"):
        if any(element.is_a(c) for c in SKIP_CLASSES) or getattr(element, "Representation", None) is None:
            continue
        attempts += 1
        if len(boxes) >= MAX_ELEMENTS or attempts > MAX_ATTEMPTS:
            problems.append(f"only the first {len(boxes)} elements were read")
            break
        try:
            verts = ifcopenshell.geom.create_shape(settings, element).geometry.verts
            xs, ys, zs = verts[0::3], verts[1::3], verts[2::3]
            lo = (min(xs) * 1000.0, min(ys) * 1000.0, min(zs) * 1000.0)
            hi = (max(xs) * 1000.0, max(ys) * 1000.0, max(zs) * 1000.0)
            if not all(math.isfinite(v) for v in (*lo, *hi)):
                raise ValueError("not finite")
        except Exception:  # noqa: BLE001 - a failed element is counted, not fatal
            skipped += 1
            continue
        boxes.append(Box(str(element.GlobalId), element.is_a(), clean(element.Name), lo, hi))
    if skipped:
        problems.append(f"{skipped} elements had no usable geometry and were left out")
    return boxes, skipped, problems


def duct_box(run: dict[str, Any]) -> Box | None:
    """The box of a duct run with coordinates, else None. Width is taken horizontal and depth vertical (a round duct uses its diameter both ways)."""
    if run.get("x0") is None:
        return None
    if run["shape"] == "rect":
        half_w, half_d = float(run["width_mm"]) / 2, float(run["depth_mm"]) / 2
    else:
        half_w = half_d = float(run["diameter_mm"]) / 2
    ins = float(run.get("insulation_mm") or 0)
    p, q = (float(run["x0"]), float(run["y0"]), float(run["z0"])), (float(run["x1"]), float(run["y1"]), float(run["z1"]))
    lo = (min(p[0], q[0]) - half_w - ins, min(p[1], q[1]) - half_w - ins, min(p[2], q[2]) - half_d - ins)
    hi = (max(p[0], q[0]) + half_w + ins, max(p[1], q[1]) + half_w + ins, max(p[2], q[2]) + half_d + ins)
    return Box(str(run["id"]), "DUCT", run["tag"], lo, hi)


def gap(a: Box, b: Box) -> float:
    """Smallest separation between two boxes in mm; negative is how far they interpenetrate (the least overlap over the three axes)."""
    per_axis = [max(a.lo[i] - b.hi[i], b.lo[i] - a.hi[i]) for i in range(3)]
    return max(per_axis)


def detect(ducts: list[dict[str, Any]], models: list[dict[str, Any]], clearance_mm: float) -> list[dict[str, Any]]:
    """models: [{"discipline", "file_name", "elements": [Box...]}]. Sorted for a stable output."""
    found = []
    for d in ducts:
        db = duct_box(d)
        if db is None:
            continue
        for m in models:
            for e in m["elements"]:
                g = gap(db, e)
                if g < clearance_mm:
                    found.append({"duct_id": str(d["id"]), "duct_tag": d["tag"], "discipline": m["discipline"], "model": m["file_name"], "element_guid": e.guid,
                                  "element_class": e.ifc_class, "element_name": e.name, "kind": "OVERLAP" if g < 0 else "CLEARANCE", "gap_mm": round(g, 1),
                                  "clearance_mm": clearance_mm, "point_mm": [round((max(db.lo[i], e.lo[i]) + min(db.hi[i], e.hi[i])) / 2, 1) for i in range(3)],
                                  "level": "WARNING"})
    found.sort(key=lambda r: (r["duct_tag"], r["discipline"], r["element_guid"]))
    return found


def _guid(seed: str) -> str:
    h = hashlib.sha256(seed.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def bcf_zip(clashes: list[dict[str, Any]], project_name: str, author: str) -> bytes:
    """BCF 2.1: bcf.version, project.bcfp, and one topic folder per clash with a markup.bcf. Topics are warnings; the status is always 'Open'."""
    out = io.BytesIO()
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bcf.version", '<?xml version="1.0" encoding="UTF-8"?>\n<Version VersionId="2.1" xsi:noNamespaceSchemaLocation="version.xsd" '
                                  'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><DetailedVersion>2.1</DetailedVersion></Version>')
        z.writestr("project.bcfp", f'<?xml version="1.0" encoding="UTF-8"?>\n<ProjectExtension><Project ProjectId="{_guid("project:" + project_name)}">'
                                   f"<Name>{escape(clean(project_name) or '')}</Name></Project></ProjectExtension>")
        for c in clashes:
            tid = _guid(f"{c['duct_id']}|{c['element_guid']}")
            title = clean(f"{c['kind']}: duct {c['duct_tag']} / {c['discipline']} {c['element_class']}") or "clash"
            desc = (f"Warning only. Gap {c['gap_mm']} mm against a clearance of {c['clearance_mm']} mm. Axis-aligned boxes; check the model. "
                    f"Point (mm) {c['point_mm']}. Element GUID {c['element_guid']}.")
            z.writestr(f"{tid}/markup.bcf",
                       '<?xml version="1.0" encoding="UTF-8"?>\n<Markup xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
                       f'<Topic Guid="{tid}" TopicType="Clash" TopicStatus="Open"><Title>{escape(title)}</Title><Priority>Normal</Priority>'
                       f"<CreationDate>{stamp}</CreationDate><CreationAuthor>{escape(author)}</CreationAuthor>"
                       f"<Description>{escape(desc)}</Description></Topic></Markup>")
    return out.getvalue()


def read_boxes_isolated(path: Path) -> tuple[list[Box], int, list[str]]:
    """read_boxes in a child process with a wall-clock limit and (on Linux) CPU and address-space limits, so a hostile or huge model cannot hang or take down the API."""
    def limits() -> None:  # pragma: no cover - runs in the child
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (READ_SECONDS, READ_SECONDS))
        resource.setrlimit(resource.RLIMIT_AS, (READ_MEMORY, READ_MEMORY))
    try:
        done = subprocess.run([sys.executable, "-I", "-m", "mep.clash", str(path)], capture_output=True, timeout=READ_SECONDS + 10, check=False,
                              preexec_fn=limits if os.name == "posix" else None, cwd=str(path.parent))
    except subprocess.TimeoutExpired:
        raise ClashIfcError("reading the model took too long") from None
    if done.returncode != 0:
        raise ClashIfcError(done.stderr.decode(errors="replace").strip().splitlines()[-1][:200] if done.stderr.strip() else "the model could not be read")
    data = json.loads(done.stdout)
    return [Box(b[0], b[1], b[2], tuple(b[3]), tuple(b[4])) for b in data["boxes"]], data["skipped"], data["problems"]


if __name__ == "__main__":  # pragma: no cover - the child process
    try:
        got = read_boxes(Path(sys.argv[1]))
    except ClashIfcError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"boxes": [[b.guid, b.ifc_class, b.name, list(b.lo), list(b.hi)] for b in got[0]], "skipped": got[1], "problems": got[2]}))
