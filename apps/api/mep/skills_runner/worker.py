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


def revalidate(skill: str, outdir: Path) -> dict[str, Any]:
    info = get_skill(skill)
    manifest = json.loads((outdir / "manifest.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location(f"revalidate_{skill.replace('-', '_')}", info.validator)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    files = [outdir / f["name"] for f in manifest["files"]] + [outdir / "manifest.json"]
    result = mod.validate(manifest["inputs"], files)
    return {"passed": bool(result.passed), "checks": [{"name": c["name"], "passed": bool(c["passed"])} for c in result.checks],
            "failed": list(result.failed)}


def main(argv: list[str]) -> int:
    apply_limits()
    if len(argv) >= 4 and argv[0] == "build":
        info = get_skill(argv[1])
        sys.argv = [str(info.build_script), "--spec", argv[2], "--out", argv[3]]
        sys.path.insert(0, str(info.build_script.parent))
        try:
            runpy.run_path(str(info.build_script), run_name="__main__")
        except SystemExit as exc:
            return int(exc.code or 0) if isinstance(exc.code, int | type(None)) else 4
        return 0
    if len(argv) == 3 and argv[0] == "validate":
        print(json.dumps(revalidate(argv[1], Path(argv[2]))))
        return 0
    print("usage: worker build <skill> <spec.json> <outdir> | validate <skill> <outdir>", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
