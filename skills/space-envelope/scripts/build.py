"""space-envelope: room and plant-room envelopes for one storey -> IFC4 + plan DXF + manifest.json, gated by an independent validator.

    python skills/space-envelope/scripts/build.py --spec spec.json --out out/

Exit codes (same lane rules as duct-fab): 0 built; 2 spec rejected (unreadable, off-schema, degenerate); 3 validator rejected;
4 build failed (kernel error or the output could not be written). On 3 and 4 `--out` holds no file from this build.
Geometry only: no compliance value is computed, no network, no database, no LLM.
"""
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
for p in (str(REPO_ROOT), str(SKILL_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import jsonschema
import se_geometry as geo
import se_writers as writers

from skills.cad import cadkit

SKILL_VERSION = "1.0.0"
MIN_ROOM_M2 = 0.25
RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})


class SpecError(ValueError):
    """The spec card is unreadable, off-schema or describes something that cannot be built."""


class ValidationFailed(RuntimeError):
    def __init__(self, result: Any) -> None:
        super().__init__("validator rejected the build: " + ", ".join(result.failed))
        self.result = result


def load_schema() -> dict[str, Any]:
    return json.loads((SKILL_DIR / "spec_card.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _require_finite(node: Any, path: str = "<root>", depth: int = 0) -> None:
    if depth > 12:
        raise SpecError(f"{path}: nested too deeply")
    if isinstance(node, float) and not math.isfinite(node):
        raise SpecError(f"{path}: not a finite number")
    if isinstance(node, dict):
        for k, v in node.items():
            _require_finite(v, f"{path}.{k}", depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _require_finite(v, f"{path}[{i}]", depth + 1)


def normalise_spec(spec_in: dict[str, Any]) -> dict[str, Any]:
    """Validate against the card and return the spec the writers and the validator share: defaults applied, outlines as
    counter-clockwise point lists, rooms in the order given."""
    if not isinstance(spec_in, dict):
        raise SpecError("the spec card must be a JSON object")
    _require_finite(spec_in)
    spec = copy.deepcopy(spec_in)
    errors = sorted(jsonschema.Draft202012Validator(load_schema()).iter_errors(spec), key=lambda e: list(e.absolute_path))
    if errors:
        e = errors[0]
        raise SpecError("/".join(str(p) for p in e.absolute_path) + ": " + e.message[:200])
    if spec["mark"].upper() in RESERVED_NAMES:
        raise SpecError("mark: a reserved file name")
    storey = spec["storey"]
    storey.setdefault("elevation_mm", 0)
    seen: dict[str, str] = {}
    rooms = []
    for i, room in enumerate(spec["rooms"]):
        where = f"rooms[{i}] ({room['name']})"
        key = room["name"].strip().lower()
        if key in seen:
            raise SpecError(f"{where}: the name is already used by {seen[key]!r} (names are unique, ignoring case)")
        seen[key] = room["name"]
        o = room["outline"]
        pts = geo.rectangle(o["x_mm"], o["y_mm"], o["width_mm"], o["depth_mm"]) if o["type"] == "rectangle" else [tuple(p) for p in o["points_mm"]]
        pts = geo.normalise_outline([(float(x), float(y)) for x, y in pts])
        if not geo.is_simple(pts):
            raise SpecError(f"{where}: the outline is not a simple polygon (edges cross, touch or fold back)")
        if geo.area_m2(pts) < MIN_ROOM_M2:
            raise SpecError(f"{where}: the outline is smaller than {MIN_ROOM_M2} m2")
        void = float(room.get("ceiling_void_mm", 0) or 0)
        if float(room["height_mm"]) + void > float(storey["floor_to_floor_mm"]) + 1e-9:
            raise SpecError(f"{where}: clear height plus ceiling void ({float(room['height_mm']) + void:g} mm) is more than the storey height")
        rooms.append({"name": room["name"].strip(), "kind": room.get("kind", "room"), "use": room.get("use"),
                      "outline": [list(p) for p in pts], "height_mm": float(room["height_mm"]),
                      **({"ceiling_void_mm": float(room["ceiling_void_mm"])} if room.get("ceiling_void_mm") is not None else {})})
    for i in range(len(rooms)):
        for j in range(i + 1, len(rooms)):
            if geo.interiors_overlap([tuple(p) for p in rooms[i]["outline"]], [tuple(p) for p in rooms[j]["outline"]]):
                raise SpecError(f"rooms {rooms[i]['name']!r} and {rooms[j]['name']!r} overlap")
    spec["rooms"] = rooms
    return spec


def _load_validator() -> Any:
    path = SKILL_DIR / "validator.py"
    mod_spec = importlib.util.spec_from_file_location("space_envelope_validator", path)
    assert mod_spec and mod_spec.loader
    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules["space_envelope_validator"] = mod
    mod_spec.loader.exec_module(mod)
    return mod


def _toolchain() -> dict[str, str]:
    out = {}
    for dist in ("ifcopenshell", "ezdxf"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = "unknown"
    return out


def build(spec_in: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """Build into a scratch folder, validate, and only then publish into `out_dir`."""
    spec = normalise_spec(spec_in)
    mark = spec["mark"]
    out_dir = Path(out_dir)
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        ifc_path, dxf_path = work / f"{mark}.ifc", work / f"{mark}.dxf"
        writers.build_ifc(spec, ifc_path)
        writers.build_dxf(spec, dxf_path)
        result = _load_validator().validate(spec, [ifc_path, dxf_path])
        if not result.passed:
            raise ValidationFailed(result)
        manifest = {
            "skill": "space-envelope", "skill_version": SKILL_VERSION, "inputs": spec, "spec_sha256": writers.spec_sha256(spec),
            "files": [{"name": p.name, "role": role, "bytes": p.stat().st_size, "sha256": cadkit.sha256_file(p)}
                      for role, p in (("ifc", ifc_path), ("dxf", dxf_path))],
            "measures": {
                "rooms": len(spec["rooms"]), "plant_rooms": sum(1 for r in spec["rooms"] if r["kind"] == "plant_room"),
                "total_area_m2": round(sum(geo.area_m2([tuple(p) for p in r["outline"]]) for r in spec["rooms"]), 4),
                "rooms_detail": [{"name": r["name"], "kind": r["kind"], "area_m2": round(geo.area_m2([tuple(p) for p in r["outline"]]), 4),
                                  "height_mm": r["height_mm"]} for r in spec["rooms"]],
            },
            "validation": {"passed": True, "checks": [c["name"] for c in result.checks]},
            "toolchain": _toolchain(),
        }
        _publish(out_dir, [(ifc_path.name, ifc_path.read_bytes()), (dxf_path.name, dxf_path.read_bytes()),
                           ("manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())])
    return manifest


def _publish(out_dir: Path, files: list[tuple[str, bytes]]) -> None:
    """Stage every file next to its destination, then move them into place (the manifest last); on any failure put the previous
    build back: the folder never holds half of a build, and a failed rebuild never destroys the last good one."""
    out_dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    backups: list[tuple[Path, Path]] = []
    placed: list[Path] = []
    try:
        for name, data in files:
            final = out_dir / name
            fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=name + ".")
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
    ap = argparse.ArgumentParser(description="Build room envelopes: IFC4 + plan DXF + manifest.json")
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, ValueError, RecursionError) as exc:
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
                print(f"FAILED {c['name']}: expected {c['expected']} actual {c['actual']} (tol {c['tolerance']})", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - a kernel error or an output that cannot be written: nothing is released
        print(f"build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4
    print(json.dumps({"out": str(args.out), "files": [f["name"] for f in manifest["files"]] + ["manifest.json"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
