"""Read an uploaded file in a separate process with hard limits.

A parser can be made to run for hours or eat all memory by a file of a few kilobytes. Reading it in the API process would stall
every other request, so the upload path runs the reader in a child process with a CPU-time limit, an address-space limit and a
wall-clock timeout; on any of them the child is killed and the upload is refused with a clear message. The reader's own
complexity budget (ingest/dxf.py) refuses the cheap cases earlier and with a precise reason.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from mep.ingest.records import IngestRefused, IngestResult, result_from_json

CPU_SECONDS = 45
MEMORY_BYTES = 4 * 1024 ** 3
WALL_SECONDS = 90


def _limits(cpu: int, memory: int):  # type: ignore[no-untyped-def]
    def apply() -> None:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        os.setsid()
    return apply


def read_isolated(kind: str, path: Path, *, cpu_seconds: int = CPU_SECONDS, memory_bytes: int = MEMORY_BYTES,
                  wall_seconds: int = WALL_SECONDS) -> IngestResult:
    """The reader's result, or IngestRefused with a message safe to show. Never raises anything else for a bad file."""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
           "PYTHONHASHSEED": "0", "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8"}
    try:
        proc = subprocess.run([sys.executable, "-m", "mep.ingest.worker", kind, str(path)], capture_output=True, env=env,
                              timeout=wall_seconds, check=False,
                              preexec_fn=_limits(cpu_seconds, memory_bytes) if os.name == "posix" else None)
    except subprocess.TimeoutExpired:
        raise IngestRefused(f"the file took longer than {wall_seconds} s to read and was stopped; "
                            "it is too complex to read here") from None
    if proc.returncode == 0:
        try:
            return result_from_json(json.loads(proc.stdout.decode("utf-8")))
        except (ValueError, KeyError, TypeError):
            raise IngestRefused(f"the file could not be read as {kind.upper()}") from None
    if proc.returncode == 3:
        raise IngestRefused(proc.stderr.decode("utf-8", "replace").strip()[:300] or "the file was refused")
    if proc.returncode < 0 or proc.returncode in (137, 152):         # killed by a signal: CPU or memory limit
        raise IngestRefused("the file used more processing time or memory than is allowed and was stopped; "
                            "it is too complex to read here")
    raise IngestRefused(f"the file could not be read as {kind.upper()} ({proc.stderr.decode('utf-8', 'replace').strip()[:60]})")
