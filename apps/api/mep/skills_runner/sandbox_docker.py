"""Run a drafting job in its own container: non-root, no network, read-only root filesystem, the input mounted read-only, the output directory the
only writable place (plus a small private /tmp), no capabilities, and CPU, memory, process and wall-clock limits.

This is the executor a deployed drafting worker uses. It needs a Docker-compatible runtime on the host that runs the dispatcher.
"""
import subprocess
import uuid
from pathlib import Path

JOB_USER = "10001:10001"
PIDS_LIMIT = "256"
TMP_SIZE = "256m"


def docker_run_args(image: str, name: str, indir: Path, outdir: Path, *, out_writable: bool, cpu_seconds: int, memory_bytes: int,
                    file_bytes: int, command: list[str], entrypoint: str | None = None, docker: str = "docker") -> list[str]:
    """The full `docker run` command for one job. One place defines the isolation, so the tests prove exactly what the dispatcher runs."""
    args = [
        docker, "run", "--rm", "--name", name,
        "--network", "none",
        "--read-only",
        "--user", JOB_USER,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", PIDS_LIMIT,
        "--memory", str(memory_bytes), "--memory-swap", str(memory_bytes),
        "--cpus", "2",
        "--ulimit", f"fsize={file_bytes}:{file_bytes}",
        "--ulimit", f"cpu={cpu_seconds}:{cpu_seconds + 5}",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,size={TMP_SIZE}",
        "-e", "HOME=/tmp", "-e", "PYTHONHASHSEED=0", "-e", "LANG=C.UTF-8",
        "-e", f"MEP_WORKER_CPU={cpu_seconds}", "-e", f"MEP_WORKER_AS={memory_bytes}", "-e", f"MEP_WORKER_FSIZE={file_bytes}",
        "-v", f"{indir}:/job/in:ro",
        "-v", f"{outdir}:/job/out:{'rw' if out_writable else 'ro'}",
        "-w", "/job/out" if out_writable else "/job/in",
    ]
    if entrypoint is not None:
        args += ["--entrypoint", entrypoint]
    return [*args, image, *command]


class DockerExecutor:
    """Executor (see runner.Executor) that runs the build and the independent re-check as two separate, fresh, isolated containers."""

    def __init__(self, image: str, file_bytes: int = 50 * 1024 * 1024, docker: str = "docker") -> None:
        self.image, self.file_bytes, self.docker = image, file_bytes, docker

    def _run(self, work: Path, command: list[str], *, out_writable: bool, wall: int, cpu: int, memory: int) -> subprocess.CompletedProcess[str] | None:
        name = f"mep-job-{uuid.uuid4().hex[:12]}"
        args = docker_run_args(self.image, name, work / "in", work / "out", out_writable=out_writable, cpu_seconds=cpu, memory_bytes=memory,
                               file_bytes=self.file_bytes, command=command, docker=self.docker)
        try:
            proc = subprocess.run(args, capture_output=True, timeout=wall + 15, check=False)
        except subprocess.TimeoutExpired:
            subprocess.run([self.docker, "rm", "-f", name], capture_output=True, check=False, timeout=30)
            return None
        return subprocess.CompletedProcess(proc.args, proc.returncode, proc.stdout[:1 << 20].decode("utf-8", "replace"),
                                           proc.stderr[:8192].decode("utf-8", "replace"))

    def build(self, skill: str, work: Path, *, wall: int, cpu: int, memory: int) -> subprocess.CompletedProcess[str] | None:
        return self._run(work, ["build", skill, "/job/in/spec.json", "/job/out"], out_writable=True, wall=wall, cpu=cpu, memory=memory)

    def validate(self, skill: str, work: Path, *, wall: int, cpu: int, memory: int) -> subprocess.CompletedProcess[str] | None:
        return self._run(work, ["validate", skill, "/job/out", "/job/in/spec.json"], out_writable=False, wall=wall, cpu=cpu, memory=memory)
