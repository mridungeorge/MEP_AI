#!/usr/bin/env python3
"""Stop hook: don't let a turn end with failing rule tests or golden evals.

Returns {"decision": "block", "reason": ...} so Claude keeps working.
Respects stop_hook_active to avoid infinite loops. Stdlib only.
"""
import json
import os
import shutil
import subprocess
import sys

TEST_DIRS = ["tests/rules", "tests/engine", "tests/golden"]


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        data = {}

    # Already continuing because of this hook once: let it stop, avoid loops.
    if data.get("stop_hook_active"):
        sys.exit(0)

    root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    targets = [d for d in TEST_DIRS if os.path.isdir(os.path.join(root, d))]
    if not targets:
        sys.exit(0)  # nothing built yet (early Sprint 0)

    runner = ["uv", "run", "pytest"] if shutil.which("uv") else [sys.executable, "-m", "pytest"]
    try:
        proc = subprocess.run(
            runner + ["-q", "-x", "--no-header", *targets],
            cwd=root, capture_output=True, text=True, timeout=540, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(json.dumps({"decision": "block", "reason": f"Test gate could not run: {exc}. Fix the test setup."}))
        sys.exit(0)

    if proc.returncode not in (0, 5):  # 5 = no tests collected
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-40:])
        print(json.dumps({
            "decision": "block",
            "reason": "Rule tests or golden evals are failing. Fix them before finishing.\n" + tail,
        }))
    sys.exit(0)


if __name__ == "__main__":
    main()
