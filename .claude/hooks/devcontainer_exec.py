#!/usr/bin/env python3
"""Run a command inside this project's devcontainer (Linux, Python 3.12, uv, the locked dependencies).

Everything that checks the project runs there, never on the host: the host Python is not the pinned one and has none of the
dependencies. The container bind-mounts the working tree, so the command sees the files as they are now, uncommitted edits
included.

    python3 .claude/hooks/devcontainer_exec.py [--timeout SECONDS] -- <shell command run in the project folder>

Exit code: the command's, or 125 when the container cannot be reached (Docker down, no devcontainer for this folder).
Stdlib only.
"""
import os
import re
import subprocess
import sys

UNREACHABLE = 125
USER = "vscode"


def norm(path: str) -> str:
    """A path comparable across the host's spellings (drive case, slashes, trailing slash)."""
    return re.sub(r"[\\/]+", "/", path).rstrip("/").lower()


def _docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)


def find_container(root: str) -> tuple[str, str] | None:
    """(container id, workspace folder inside the container) for the devcontainer built from `root`, or None."""
    out = _docker("ps", "-a", "--filter", "label=devcontainer.local_folder", "--format",
                  '{{.ID}}\t{{.State}}\t{{.Label "devcontainer.local_folder"}}')
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        cid, state, folder = (line.split("\t") + ["", "", ""])[:3]
        if norm(folder) == norm(root):
            if state != "running" and _docker("start", cid, timeout=120).returncode != 0:
                return None
            return cid, "/workspaces/" + re.split(r"[\\/]", folder.rstrip("\\/"))[-1]
    return None


def to_container_path(root: str, host_path: str, workspace: str) -> str:
    """The container's path for a host file under `root` (None-safe: a path outside root is returned unchanged)."""
    h, r = re.sub(r"[\\/]+", "/", host_path), re.sub(r"[\\/]+", "/", root).rstrip("/")
    return f"{workspace}/{h[len(r) + 1:]}" if norm(h).startswith(norm(r) + "/") else host_path


def run(command: str, root: str, timeout: int = 540) -> subprocess.CompletedProcess[str]:
    """Run `command` (a shell string) in the project folder of the devcontainer."""
    try:
        found = find_container(root)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess([], UNREACHABLE, "", f"docker is not reachable: {exc}")
    if found is None:
        return subprocess.CompletedProcess([], UNREACHABLE, "", (
            "no running devcontainer for this folder (Docker Desktop must be up). Build it once with: "
            "npx @devcontainers/cli up --workspace-folder ."))
    cid, workspace = found
    try:
        return subprocess.run(["docker", "exec", "-u", USER, "-w", workspace, cid, "bash", "-lc", command],
                              capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess([], UNREACHABLE, "", f"the command did not finish within {timeout} s")


def main(argv: list[str]) -> int:
    timeout = 540
    if argv[:1] == ["--timeout"] and len(argv) > 2:
        timeout, argv = int(argv[1]), argv[2:]
    if argv[:1] != ["--"] or len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    result = run(" ".join(argv[1:]), os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(), timeout)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
