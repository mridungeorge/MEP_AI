"""IFC reader: IfcSpace -> SpaceRecord (GUID, name, area, use, storey, ceiling void), with the exporter profile.

The header `originating_system` selects a profile in ingest/profiles/*.yaml (revit, archicad, sketchup, unknown). The
profile says where this exporter puts areas and uses. Areas come from the quantity sets first; only if none exists the
footprint of the space geometry is used (and the record says so). Everything returned is provenance 'extracted': a
designer confirms it at Gate 1. Nothing here decides compliance.

IngestResult.metadata keys (read by ingest/health.py):
  schema, schema_supported, originating_system, preprocessor_version, exporter_profile, profile_status, area_source
  ('quantities' | 'geometry' | 'mixed' | 'none'), spaces_total, spaces_with_area, spaces_without_storey,
  unnamed_spaces, duplicate_spaces, storeys, length_unit_scale_m
"""
import hashlib
import math
import re
from pathlib import Path
from typing import Any

import yaml

from mep.ingest.records import IngestResult, SpaceRecord

PROFILE_DIR = Path(__file__).parent / "profiles"
PROFILE_ORDER = ("revit", "archicad", "sketchup")        # first match wins; 'unknown' is the fallback
SUPPORTED_SCHEMAS = ("IFC2X3", "IFC4", "IFC4X3")
MAX_BYTES = 500 * 1024 * 1024


class IfcReadError(Exception):
    """The file is not a readable IFC model."""


def load_profile(profile_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9_]+", profile_id):
        raise ValueError(f"bad profile id {profile_id!r}")
    data = yaml.safe_load((PROFILE_DIR / f"{profile_id}.yaml").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("id") != profile_id or data.get("status") not in ("verified", "unverified"):
        raise ValueError(f"profile {profile_id} is malformed")
    return data


def match_profile(originating_system: str | None) -> dict[str, Any]:
    text = originating_system or ""
    for pid in PROFILE_ORDER:
        profile = load_profile(pid)
        if any(re.search(pattern, text) for pattern in profile["match"]["originating_system"]):
            return profile
    return load_profile("unknown")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _lookup(psets: dict[str, Any], pairs: list[list[str]]) -> Any:
    for pset, prop in pairs:
        value = (psets.get(pset) or {}).get(prop)
        if value is not None and value != "":
            return value
    return None


def _storey(element: Any) -> str | None:
    import ifcopenshell.util.element as util

    parent = util.get_aggregate(element)
    while parent is not None and not parent.is_a("IfcBuildingStorey"):
        parent = util.get_aggregate(parent)
    if parent is None:
        parent = util.get_container(element)
        if parent is not None and not parent.is_a("IfcBuildingStorey"):
            parent = None
    return None if parent is None else (parent.Name or parent.LongName)


def _geometry_area(settings: Any, element: Any) -> float | None:
    import ifcopenshell.geom
    import ifcopenshell.util.shape

    if element.Representation is None:
        return None
    try:
        shape = ifcopenshell.geom.create_shape(settings, element)
        area = float(ifcopenshell.util.shape.get_footprint_area(shape.geometry))
    except Exception:  # noqa: BLE001 - geometry kernels raise many types; a failed space is reported, not fatal
        return None
    return area if math.isfinite(area) and area > 0 else None


def _geometry_centroid(element: Any) -> tuple[float, float] | None:
    """Plan position (metres, model coordinates) of the middle of the space's bounding box, or None if it cannot be computed."""
    import ifcopenshell.geom

    if element.Representation is None:
        return None
    try:
        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        verts = ifcopenshell.geom.create_shape(settings, element).geometry.verts
        xs, ys = list(verts[0::3]), list(verts[1::3])
        x, y = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    except Exception:  # noqa: BLE001 - a space without a computable position is matched by GUID or name only
        return None
    return (round(x, 3), round(y, 3)) if math.isfinite(x) and math.isfinite(y) else None


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def read_ifc(path: str | Path) -> IngestResult:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element as util
    import ifcopenshell.util.unit as units

    p = Path(path)
    if p.suffix.lower() != ".ifc":
        raise IfcReadError("only .ifc files are read (DWG and other formats are not supported)")
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise IfcReadError(f"cannot read {p.name}: {exc}") from exc
    if size == 0 or size > MAX_BYTES:
        raise IfcReadError(f"{p.name}: empty or too large (max {MAX_BYTES} bytes)")
    try:
        model = ifcopenshell.open(str(p))
    except Exception as exc:
        raise IfcReadError(f"{p.name} is not a readable IFC file: {exc}") from exc

    header = model.header.file_name
    system = str(header.originating_system or "")
    profile = match_profile(system)
    schema = str(model.schema)
    meta: dict[str, Any] = {
        "schema": schema, "schema_supported": schema.split("_")[0] in SUPPORTED_SCHEMAS,
        "originating_system": system, "preprocessor_version": str(header.preprocessor_version or ""),
        "exporter_profile": profile["id"], "profile_status": profile["status"],
    }
    result = IngestResult("ifc", p.name, _sha256(p), metadata=meta)
    try:
        length_scale = float(units.calculate_unit_scale(model))
        area_scale = float(units.calculate_unit_scale(model, "AREAUNIT"))
    except Exception:  # noqa: BLE001
        length_scale, area_scale = 1.0, 1.0
        result.problems.append("the file's units could not be read; areas from quantities are unscaled")
    meta["length_unit_scale_m"] = length_scale

    settings = ifcopenshell.geom.settings()
    sources: set[str] = set()
    seen: dict[tuple[str, str | None, float | None], int] = {}
    storeys: set[str] = set()
    for element in model.by_type("IfcSpace"):
        psets = util.get_psets(element)
        notes: list[str] = []
        raw_area = _lookup(psets, profile["area"]["quantities"])
        area = _positive(raw_area)
        area = None if area is None else area * area_scale
        if area is not None:
            sources.add("quantities")
        elif profile["area"].get("geometry_fallback"):
            area = _geometry_area(settings, element)
            if area is not None:
                sources.add("geometry")
                notes.append("area computed from the space geometry (footprint), not read from a quantity set")
        if area is None:
            notes.append("no usable area")
        use = _lookup(psets, profile["use"]["properties"])
        if use is None and profile["use"].get("object_type_fallback"):
            use = element.ObjectType
        void = _positive(_lookup(psets, profile["ceiling_void"]["properties"]))
        name = element.Name or getattr(element, "LongName", None) or None
        storey = _storey(element)
        if storey:
            storeys.add(storey)
        key = (str(name or "").strip().lower(), storey, None if area is None else round(area, 2))
        seen[key] = seen.get(key, 0) + 1
        result.spaces.append(SpaceRecord(
            key=str(element.GlobalId), name=None if name is None else str(name),
            area_m2=None if area is None else round(area, 6), use=None if use is None else str(use), storey=storey,
            ceiling_void_mm=None if void is None else void * length_scale * 1000.0,
            source_kind="ifc", notes=tuple(notes), centroid_m=_geometry_centroid(element)))
    total = len(result.spaces)
    meta.update({
        "spaces_total": total,
        "spaces_with_area": sum(1 for s in result.spaces if s.area_m2 is not None),
        "spaces_without_storey": sum(1 for s in result.spaces if s.storey is None),
        "unnamed_spaces": sum(1 for s in result.spaces if not (s.name or "").strip()),
        "duplicate_spaces": sum(n - 1 for k, n in seen.items() if n > 1 and k[0]),
        "storeys": sorted(storeys),
        "area_source": "none" if not sources else (next(iter(sources)) if len(sources) == 1 else "mixed"),
    })
    if total == 0:
        result.problems.append("the model has no IfcSpace entities: nothing to read as spaces")
    return result
