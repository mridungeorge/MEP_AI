"""hvac-dxf: duct layout or schematic -> DXF + manifest.json, gated by an independent validator.

    python skills/hvac-dxf/scripts/build.py --spec spec.json --out out/

Exit codes (same lane rules as duct-fab): 0 built; 2 spec rejected (unreadable, off-schema, degenerate); 3 validator rejected;
4 build failed. On 3 and 4 `--out` holds no file from this build. Geometry and text only: no compliance value, no network, no database, no LLM.
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

import hd_writers as writers
import jsonschema

from skills.cad import cadkit

SKILL_VERSION = "1.0.0"
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


def _check_size(size: dict[str, Any], where: str) -> None:
    if size["shape"] == "rect":
        if "width_mm" not in size or "depth_mm" not in size or "diameter_mm" in size:
            raise SpecError(f"{where}: a rectangular size has width_mm and depth_mm only")
    elif "diameter_mm" not in size or "width_mm" in size or "depth_mm" in size:
        raise SpecError(f"{where}: a round size has diameter_mm only")


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
    spec.setdefault("terminals", [])
    spec.setdefault("balance_tolerance_pct", 1)
    names = writers.layer_names(spec)
    if len({n.upper() for n in names.values()}) != len(names):
        raise SpecError("layers: each role needs its own layer name")
    for group in ("ducts", "terminals", "sizing_schedule"):
        tags = [i["tag"] for i in spec[group]]
        if len(set(tags)) != len(tags):
            raise SpecError(f"{group}: tags must be unique")
    for i, d in enumerate(spec["ducts"]):
        _check_size(d["size"], f"ducts[{i}] ({d['tag']})")
        if math.dist(d["start_mm"], d["end_mm"]) < 1.0:
            raise SpecError(f"ducts[{i}] ({d['tag']}): shorter than 1 mm")
    for i, s in enumerate(spec["sizing_schedule"]):
        _check_size(s["size"], f"sizing_schedule[{i}] ({s['tag']})")
    return spec


def _load_validator() -> Any:
    path = SKILL_DIR / "validator.py"
    mod_spec = importlib.util.spec_from_file_location("hvac_dxf_validator", path)
    assert mod_spec and mod_spec.loader
    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules["hvac_dxf_validator"] = mod
    mod_spec.loader.exec_module(mod)
    return mod


def _toolchain() -> dict[str, str]:
    try:
        return {"ezdxf": metadata.version("ezdxf")}
    except metadata.PackageNotFoundError:
        return {"ezdxf": "unknown"}


def spec_sha256(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build(spec_in: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """Build into a scratch folder, validate, and only then publish into `out_dir`."""
    spec = normalise_spec(spec_in)
    mark = spec["mark"]
    with tempfile.TemporaryDirectory() as td:
        dxf_path = Path(td) / f"{mark}.dxf"
        writers.build_dxf(spec, dxf_path)
        result = _load_validator().validate(spec, [dxf_path])
        if not result.passed:
            raise ValidationFailed(result)
        manifest = {
            "skill": "hvac-dxf", "skill_version": SKILL_VERSION, "inputs": spec, "spec_sha256": spec_sha256(spec),
            "files": [{"name": dxf_path.name, "role": "dxf", "bytes": dxf_path.stat().st_size, "sha256": cadkit.sha256_file(dxf_path)}],
            "measures": {"mode": spec["mode"], "ducts": len(spec["ducts"]), "terminals": len(spec["terminals"]),
                         "duct_length_m": round(sum(math.dist(d["start_mm"], d["end_mm"]) for d in spec["ducts"]) / 1000.0, 3)},
            "validation": {"passed": True, "checks": [c["name"] for c in result.checks]},
            "toolchain": _toolchain(),
        }
        _publish(out_dir, [(dxf_path.name, dxf_path.read_bytes()), ("manifest.json", (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())])
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
    ap = argparse.ArgumentParser(description="Build a duct layout or schematic DXF + manifest.json")
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
