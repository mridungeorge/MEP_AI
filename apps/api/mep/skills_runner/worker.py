"""The skill process: `python -m mep.skills_runner.worker build|validate <skill> ...`, run only by skills_runner.runner.

It sets its own resource limits first (CPU, memory, file size; from the environment), then either runs the skill's build script exactly as the
command line would (`build <skill> <spec.json> <outdir>`; the exit code is the skill's: 0 built, 2 spec rejected, 3 validator rejected, 4 failed)
or re-runs the skill's validator on a finished output folder (`validate <skill> <outdir>`; prints JSON, exit 0). The validator run is a
SEPARATE process from the build so a build cannot influence its own re-check.
"""
import importlib.util
import json
import os
import resource
import runpy
import sys
from pathlib import Path
from typing import Any

from mep.skills_runner.registry import get_skill


def apply_limits() -> None:
    for name, limit in (("MEP_WORKER_CPU", resource.RLIMIT_CPU), ("MEP_WORKER_AS", resource.RLIMIT_AS),
                        ("MEP_WORKER_FSIZE", resource.RLIMIT_FSIZE)):
        value = os.environ.get(name)
        if value and value.isdigit():
            n = int(value)
            resource.setrlimit(limit, (n, n + 5 if limit == resource.RLIMIT_CPU else n))


def _same_inputs(info: Any, manifest: dict[str, Any], submitted: Path) -> str | None:
    """A skill that normalises its spec must have built from the spec that was SUBMITTED: normalise it again here and compare with the
    manifest's `inputs` (what the validator is given). Returns a problem, or None."""
    spec = importlib.util.spec_from_file_location("resubmit_build", info.build_script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    sys.path.insert(0, str(info.build_script.parent))
    spec.loader.exec_module(mod)
    normalise = getattr(mod, "normalise_spec", None)
    if normalise is None:
        return None
    try:
        again = normalise(json.loads(submitted.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 - the submitted spec no longer normalises: the build cannot have been of it
        return f"the submitted spec does not normalise on re-check ({type(exc).__name__})"
    if json.dumps(again, sort_keys=True) != json.dumps(manifest.get("inputs"), sort_keys=True):
        return "the manifest's inputs are not the spec that was submitted"
    return None


def revalidate(skill: str, outdir: Path, submitted: Path | None = None) -> dict[str, Any]:
    info = get_skill(skill)
    manifest = json.loads((outdir / "manifest.json").read_text(encoding="utf-8"))
    mismatch = _same_inputs(info, manifest, submitted) if submitted is not None else None
    if mismatch:
        return {"passed": False, "checks": [{"name": "inputs_match_submitted_spec", "passed": False}], "failed": ["inputs_match_submitted_spec"]}
    spec = importlib.util.spec_from_file_location(f"revalidate_{skill.replace('-', '_')}", info.validator)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    files = [outdir / f["name"] for f in manifest["files"]] + [outdir / "manifest.json"]
    result = mod.validate(manifest["inputs"], files)
    return {"passed": bool(result.passed), "checks": [{"name": c["name"], "passed": bool(c["passed"])} for c in result.checks],
            "failed": list(result.failed)}


def _share_output(out: str) -> None:
    """The job may run as another user than the one that collects its output: make every plain file in the output directory readable by it
    (a build that writes through a temporary file leaves mode 0600)."""
    try:
        for entry in os.scandir(out):
            if entry.is_file(follow_symlinks=False):
                os.chmod(entry.path, 0o644)
    except OSError:
        pass


def main(argv: list[str]) -> int:
    apply_limits()
    if len(argv) >= 4 and argv[0] == "build":
        info = get_skill(argv[1])
        sys.argv = [str(info.build_script), "--spec", argv[2], "--out", argv[3]]
        sys.path.insert(0, str(info.build_script.parent))
        try:
            runpy.run_path(str(info.build_script), run_name="__main__")
        except SystemExit as exc:
            _share_output(argv[3])
            return int(exc.code or 0) if isinstance(exc.code, int | type(None)) else 4
        _share_output(argv[3])
        return 0
    if len(argv) in (3, 4) and argv[0] == "validate":
        print(json.dumps(revalidate(argv[1], Path(argv[2]), Path(argv[3]) if len(argv) == 4 else None)))
        return 0
    print("usage: worker build <skill> <spec.json> <outdir> | validate <skill> <outdir>", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
