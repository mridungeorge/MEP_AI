"""Read an uploaded file in a separate process with hard limits.

A parser can be made to run for hours, eat all memory, or print gigabytes by a file of a few kilobytes. Reading it in the API
process would stall or kill every other request, so the upload path runs the reader in a child process that

* sets its OWN limits first thing (CPU time, address space, and the size of any file it writes: `ingest/worker.py`), so nothing
  runs between fork and the limits and no `preexec_fn` is needed in this multi-threaded process;
* writes its result and its messages to temporary FILES (the file-size limit caps them; a pipe would be buffered unbounded in the API);
* is given a wall-clock timeout and its own session, and is killed on any of them.

The reader's own complexity budget (ingest/dxf.py) refuses the cheap cases earlier and with a precise reason.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mep.ingest.records import IngestRefused, IngestResult, result_from_json

CPU_SECONDS = 45
MEMORY_BYTES = 4 * 1024 ** 3
WALL_SECONDS = 90
MAX_OUTPUT_BYTES = 8 * 1024 * 1024


def read_isolated(kind: str, path: Path, *, cpu_seconds: int = CPU_SECONDS, memory_bytes: int = MEMORY_BYTES,
                  wall_seconds: int = WALL_SECONDS, max_output: int = MAX_OUTPUT_BYTES) -> IngestResult:
    """The reader's result, or IngestRefused with a message safe to show. Never raises anything else for a bad file."""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
           "PYTHONHASHSEED": "0", "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8",
           "MEP_WORKER_CPU": str(cpu_seconds), "MEP_WORKER_AS": str(memory_bytes), "MEP_WORKER_FSIZE": str(max_output)}
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.run([sys.executable, "-m", "mep.ingest.worker", kind, str(path)], stdout=out, stderr=err, env=env,
                                  timeout=wall_seconds, check=False, start_new_session=True)
        except subprocess.TimeoutExpired:
            raise IngestRefused(f"the file took longer than {wall_seconds} s to read and was stopped; "
                                "it is too complex to read here") from None
        out.seek(0)
        stdout = out.read(max_output + 1)
        err.seek(0)
        stderr = err.read(4096).decode("utf-8", "replace")
    last = next((line for line in reversed(stderr.splitlines()) if line.strip()), "").strip()[:300]
    if proc.returncode == 0 and len(stdout) <= max_output:
        try:
            return result_from_json(json.loads(stdout.decode("utf-8")))
        except (ValueError, KeyError, TypeError):
            raise IngestRefused(f"the file could not be read as {kind.upper()}") from None
    if proc.returncode == 3:
        raise IngestRefused(last or "the file was refused")
    if proc.returncode < 0 or proc.returncode in (5, 137, 152, 153) or len(stdout) > max_output:   # a limit killed it
        raise IngestRefused("the file used more processing time, memory or output than is allowed and was stopped; "
                            "it is too complex to read here")
    raise IngestRefused(f"the file could not be read as {kind.upper()}")
