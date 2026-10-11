"""ifc-mep: ducts, terminals and equipment written into a COPY of the architect's IFC -> <mark>.ifc + manifest.json, gated by an independent validator.

    python skills/ifc-mep/scripts/build.py --spec in/spec.json --out out/        (the architect's file is read from base.ifc next to the spec)

Exit codes (same lane rules as duct-fab): 0 built; 2 spec rejected (unreadable, off-schema, no or wrong base file, unknown storey); 3 validator rejected;
4 build failed. On 3 and 4 `--out` holds no file from this build. Geometry and data only: no compliance value, no network, no database, no LLM.
"""
import argparse
import copy
import hashlib
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

import im_writers as writers
import jsonschema

SKILL_VERSION = "1.0.0"
MAX_BASE_BYTES = 100 * 1024 * 1024
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
    for g in ("terminals", "equipment", "connections"):
        spec.setdefault(g, [])
    spec.setdefault("balance_tolerance_pct", 1)
    tags = [e["tag"] for g in ("ducts", "terminals", "equipment") for e in spec[g]]
    if len(set(tags)) != len(tags):
        raise SpecError("tags must be unique across ducts, terminals and equipment")
    for i, d in enumerate(spec["ducts"]):
        s = d["size"]
        if s["shape"] == "rect" and ("width_mm" not in s or "depth_mm" not in s or "diameter_mm" in s):
            raise SpecError(f"ducts[{i}] ({d['tag']}): a rectangular size has width_mm and depth_mm only")
        if s["shape"] == "round" and ("diameter_mm" not in s or "width_mm" in s or "depth_mm" in s):
            raise SpecError(f"ducts[{i}] ({d['tag']}): a round size has diameter_mm only")
        if math.dist(d["start_mm"], d["end_mm"]) < 1.0:
            raise SpecError(f"ducts[{i}] ({d['tag']}): shorter than 1 mm")
    ports = {f"{d['tag']}.{p}" for d in spec["ducts"] for p in ("start", "end")} | {f"{t['tag']}.in" for t in spec["terminals"]} \
        | {f"{q['tag']}.{p}" for q in spec["equipment"] for p in ("in", "out")}
    seen = set()
    for i, c in enumerate(spec["connections"]):
        for end in (c["from"], c["to"]):
            if end not in ports:
                raise SpecError(f"connections[{i}]: no such port {end!r}")
            if end in seen:
                raise SpecError(f"connections[{i}]: port {end!r} is already connected")
            seen.add(end)
        if c["from"].split(".")[0] == c["to"].split(".")[0]:
            raise SpecError(f"connections[{i}]: an element cannot connect to itself")
    return spec


def _load_validator() -> Any:
    path = SKILL_DIR / "validator.py"
    mod_spec = importlib.util.spec_from_file_location("ifc_mep_validator", path)
    assert mod_spec and mod_spec.loader
    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules["ifc_mep_validator"] = mod
    mod_spec.loader.exec_module(mod)
    return mod


def _toolchain() -> dict[str, str]:
    try:
        return {"ifcopenshell": metadata.version("ifcopenshell")}
    except metadata.PackageNotFoundError:
        return {"ifcopenshell": "unknown"}


def spec_sha256(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check_base(spec: dict[str, Any], base: Path) -> None:
    try:
        size = base.stat().st_size
    except OSError:
        raise SpecError("the architect's file (base.ifc) was not supplied to the job") from None
    if size > MAX_BASE_BYTES:
        raise SpecError("the architect's file is larger than 100 MiB")
    if writers.sha256_file(base) != spec["base_ifc_sha256"]:
        raise SpecError("the architect's file does not match base_ifc_sha256")


def build(spec_in: dict[str, Any], out_dir: Path, base: Path) -> dict[str, Any]:
    """Build into a scratch folder, validate, and only then publish into `out_dir`."""
    spec = normalise_spec(spec_in)
    check_base(spec, base)
    mark = spec["mark"]
    with tempfile.TemporaryDirectory() as td:
        ifc_path = Path(td) / f"{mark}.ifc"
        try:
            measures = writers.build_ifc(spec, base, ifc_path)
        except ValueError as exc:
            raise SpecError(str(exc)) from None
        result = _load_validator().validate(spec, [ifc_path, base])
        if not result.passed:
            raise ValidationFailed(result)
        from skills.cad import cadkit
        manifest = {
            "skill": "ifc-mep", "skill_version": SKILL_VERSION, "inputs": spec, "spec_sha256": spec_sha256(spec),
            "files": [{"name": ifc_path.name, "role": "ifc", "bytes": ifc_path.stat().st_size, "sha256": cadkit.sha256_file(ifc_path)}],
            "measures": measures, "validation": {"passed": True, "checks": [c["name"] for c in result.checks]}, "toolchain": _toolchain(),
        }
        _publish(out_dir, [(ifc_path.name, ifc_path.read_bytes()), ("manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())])
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
    ap = argparse.ArgumentParser(description="Write ducts, terminals and equipment into a copy of the architect's IFC")
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--base", type=Path, help="the architect's IFC (default: base.ifc next to the spec)")
    args = ap.parse_args(argv)
    try:
        spec = json.loads(args.spec.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, ValueError, RecursionError) as exc:
        print(f"spec rejected: {exc}", file=sys.stderr)
        return 2
    try:
        manifest = build(spec, args.out, args.base or args.spec.parent / "base.ifc")
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
