"""space-envelope-stack: room envelopes of several storeys -> one combined IFC4 + a plan DXF per storey + manifest.json, gated by an independent validator.

    python skills/space-envelope-stack/scripts/build.py --spec spec.json --out out/

Exit codes (same lane rules as duct-fab): 0 built; 2 spec rejected; 3 validator rejected; 4 build failed. On 3 and 4 `--out` holds no file from this build.
Geometry only: no compliance value, no network, no database, no LLM.
"""
import argparse
import copy
import hashlib
import importlib.util
import itertools
import json
import os
import sys
import tempfile
from importlib import metadata
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[1]
SE_DIR = SKILL_DIR.parent / "space-envelope"
REPO_ROOT = SKILL_DIR.parents[1]
for p in (str(REPO_ROOT), str(SE_DIR), str(SKILL_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import jsonschema
import ses_writers as writers

from skills.cad import cadkit

SKILL_VERSION = "1.0.0"
STACK_TOL_MM = 1.0
RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})


class SpecError(ValueError):
    """The spec card is unreadable, off-schema or describes something that cannot be built."""


class ValidationFailed(RuntimeError):
    def __init__(self, result: Any) -> None:
        super().__init__("validator rejected the build: " + ", ".join(result.failed))
        self.result = result


def _load(name: str, path: Path) -> Any:
    mod_spec = importlib.util.spec_from_file_location(name, path)
    assert mod_spec and mod_spec.loader
    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules[name] = mod
    mod_spec.loader.exec_module(mod)
    return mod


def _se_build() -> Any:
    return sys.modules.get("space_envelope_build_for_stack") or _load("space_envelope_build_for_stack", SE_DIR / "scripts" / "build.py")


def load_schema() -> dict[str, Any]:
    return json.loads((SKILL_DIR / "spec_card.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def normalise_spec(spec_in: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(spec_in, dict):
        raise SpecError("the spec card must be a JSON object")
    se = _se_build()
    try:
        se._require_finite(spec_in)
    except se.SpecError as exc:
        raise SpecError(str(exc)) from None
    spec = copy.deepcopy(spec_in)
    errors = sorted(jsonschema.Draft202012Validator(load_schema()).iter_errors(spec), key=lambda e: list(e.absolute_path))
    if errors:
        e = errors[0]
        raise SpecError("/".join(str(p) for p in e.absolute_path) + ": " + e.message[:200])
    if spec["mark"].upper() in RESERVED_NAMES:
        raise SpecError("mark: a reserved file name")
    names = [s["name"].strip().lower() for s in spec["storeys"]]
    if len(set(names)) != len(names):
        raise SpecError("storeys: names must be unique (ignoring case)")
    out = []
    seen: dict[str, str] = {}
    for i, s in enumerate(spec["storeys"]):
        sub = {"spec_version": "1", "mark": writers.storey_mark(spec["mark"], i), "units": "mm",
               "storey": {"name": s["name"], "elevation_mm": s["elevation_mm"], "floor_to_floor_mm": s["floor_to_floor_mm"]}, "rooms": s["rooms"]}
        try:
            norm = se.normalise_spec(sub)
        except se.SpecError as exc:
            raise SpecError(f"storeys[{i}] ({s['name']}): {exc}") from None
        for r in norm["rooms"]:
            key = r["name"].lower()
            if key in seen:
                raise SpecError(f"storeys[{i}] ({s['name']}): room {r['name']!r} is already used on {seen[key]!r} (room names are unique across the drawing)")
            seen[key] = s["name"]
        out.append({"name": s["name"].strip(), "elevation_mm": float(s["elevation_mm"]), "floor_to_floor_mm": float(s["floor_to_floor_mm"]), "rooms": norm["rooms"]})
    for lower, upper in itertools.pairwise(out):
        if abs(upper["elevation_mm"] - (lower["elevation_mm"] + lower["floor_to_floor_mm"])) > STACK_TOL_MM:
            raise SpecError(f"storeys do not stack: {upper['name']!r} should be at {lower['elevation_mm'] + lower['floor_to_floor_mm']:g} mm "
                            f"({lower['name']!r} elevation plus its floor-to-floor height), not {upper['elevation_mm']:g} mm")
    spec["storeys"] = out
    return spec


def _load_validator() -> Any:
    return _load("space_envelope_stack_validator", SKILL_DIR / "validator.py")


def _toolchain() -> dict[str, str]:
    out = {}
    for dist in ("ifcopenshell", "ezdxf"):
        try:
            out[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[dist] = "unknown"
    return out


def spec_sha256(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build(spec_in: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """Build into a scratch folder, validate, and only then publish into `out_dir`."""
    spec = normalise_spec(spec_in)
    mark = spec["mark"]
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        ifc_path = work / f"{mark}.ifc"
        writers.build_ifc(spec, ifc_path)
        dxf_paths = []
        for i in range(len(spec["storeys"])):
            p = work / f"{writers.storey_mark(mark, i)}.dxf"
            writers.build_dxf(spec, i, p)
            dxf_paths.append(p)
        result = _load_validator().validate(spec, [ifc_path, *dxf_paths])
        if not result.passed:
            raise ValidationFailed(result)
        from se_geometry import area_m2
        manifest = {
            "skill": "space-envelope-stack", "skill_version": SKILL_VERSION, "inputs": spec, "spec_sha256": spec_sha256(spec),
            "files": [{"name": p.name, "role": "ifc" if p.suffix == ".ifc" else "dxf", "bytes": p.stat().st_size, "sha256": cadkit.sha256_file(p)} for p in [ifc_path, *dxf_paths]],
            "measures": {"storeys": len(spec["storeys"]), "rooms": sum(len(s["rooms"]) for s in spec["storeys"]),
                         "total_area_m2": round(sum(area_m2([tuple(pt) for pt in r["outline"]]) for s in spec["storeys"] for r in s["rooms"]), 4),
                         "storeys_detail": [{"name": s["name"], "elevation_mm": s["elevation_mm"], "rooms": len(s["rooms"])} for s in spec["storeys"]]},
            "validation": {"passed": True, "checks": [c["name"] for c in result.checks]}, "toolchain": _toolchain(),
        }
        _publish(out_dir, [(p.name, p.read_bytes()) for p in [ifc_path, *dxf_paths]] + [("manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())])
    return manifest


def _publish(out_dir: Path, files: list[tuple[str, bytes]]) -> None:
    """Stage every file next to its destination, then move them into place (the manifest last); on failure put the previous build back."""
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
    ap = argparse.ArgumentParser(description="Build multi-storey room envelopes: one IFC4 + a plan DXF per storey + manifest.json")
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
    except Exception as exc:  # noqa: BLE001 - nothing is released on any build failure
        print(f"build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4
    print(json.dumps({"out": str(args.out), "files": [f["name"] for f in manifest["files"]] + ["manifest.json"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
