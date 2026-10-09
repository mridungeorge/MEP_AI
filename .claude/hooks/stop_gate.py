#!/usr/bin/env python3
"""Stop hook: don't let a turn end with failing rule tests or golden evals.

The tests run INSIDE the project's devcontainer (Linux, Python 3.12, locked dependencies), never on the host. If the
container cannot be reached the turn is blocked too: a gate that cannot run is not a gate that passed.

Returns {"decision": "block", "reason": ...} so Claude keeps working. Respects stop_hook_active to avoid infinite loops.
Stdlib only.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import devcontainer_exec

TEST_DIRS = ["tests/rules", "tests/engine", "tests/golden"]
UNREACHABLE = devcontainer_exec.UNREACHABLE


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


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

    # `uv run` keeps the container's environment in step with uv.lock; pytest comes from the project's dev group
    proc = devcontainer_exec.run("uv run pytest -q -x --no-header -p no:cacheprovider " + " ".join(targets), root)
    if proc.returncode == UNREACHABLE:
        block("The test gate could not run in the devcontainer: " + (proc.stderr.strip() or "unknown error")
              + "\nStart Docker Desktop and the devcontainer, then finish.")
    if proc.returncode not in (0, 5):  # 5 = no tests collected
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-40:])
        block("Rule tests or golden evals are failing (run in the devcontainer). Fix them before finishing.\n" + tail)
    sys.exit(0)


if __name__ == "__main__":
    main()
