"""Run a drafting skill safely and hand back files ONLY when the validator has passed twice.

1. the skill's own build (a child process with CPU, memory, file-size and wall-clock limits, a minimal environment, its own session);
2. the runner's own checks on what came out: the manifest is readable and lists files, every listed file is there with the sha256 and size the
   manifest claims, nothing is oversized;
3. the skill's validator run AGAIN in a separate child process on the finished folder.

Any other outcome returns a result with `files` empty of content: the caller stores nothing and releases nothing. No LLM is involved and no
compliance value is computed here.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from mep.skills_runner.registry import MEDIA_TYPES, availability, get_skill

CPU_SECONDS = 120
MEMORY_BYTES = 6 * 1024 ** 3
WALL_SECONDS = 240
FILE_BYTES = 50 * 1024 * 1024
MAX_SPEC_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
_SLOTS = threading.BoundedSemaphore(3)


class SkillUnavailable(Exception):
    """The skill cannot run on this server (a package is not installed) or the server is busy."""


@dataclass
class RunFile:
    name: str
    role: str
    media_type: str
    bytes: int
    sha256: str
    content: bytes | None = None          # present only when the run is released


@dataclass
class SkillRunResult:
    status: str                            # ok | spec_rejected | validator_rejected | build_failed | timeout | revalidation_failed
    message: str = ""
    validation: dict[str, Any] = field(default_factory=dict)
    files: list[RunFile] = field(default_factory=list)
    manifest: dict[str, Any] | None = None

    @property
    def released(self) -> bool:
        return self.status == "ok"


def _env(cpu: int, memory: int) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": os.pathsep.join(p for p in sys.path if p), "PYTHONHASHSEED": "0",
            "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8", "MEP_WORKER_CPU": str(cpu), "MEP_WORKER_AS": str(memory),
            "MEP_WORKER_FSIZE": str(FILE_BYTES)}


class Executor(Protocol):
    """Where a build and its independent re-check run. Both get the same layout: `work/in/spec.json` (read-only to the job) and `work/out/`
    (the only place a build may write). Each call is a fresh, separate process or container. None means it ran out of time."""

    def build(self, skill: str, work: Path, *, wall: int, cpu: int, memory: int) -> subprocess.CompletedProcess[str] | None: ...
    def validate(self, skill: str, work: Path, *, wall: int, cpu: int, memory: int) -> subprocess.CompletedProcess[str] | None: ...


class LocalExecutor:
    """Child processes of this server with CPU, memory and file-size limits: no isolation of the network or the filesystem. For development,
    tests and a server where no container runtime exists."""

    def build(self, skill: str, work: Path, *, wall: int, cpu: int, memory: int) -> subprocess.CompletedProcess[str] | None:
        return _child(["build", skill, str(work / "in" / "spec.json"), str(work / "out")], cwd=work, wall=wall, cpu=cpu, memory=memory)

    def validate(self, skill: str, work: Path, *, wall: int, cpu: int, memory: int) -> subprocess.CompletedProcess[str] | None:
        return _child(["validate", skill, str(work / "out"), str(work / "in" / "spec.json")], cwd=work, wall=wall, cpu=cpu, memory=memory)


def _child(args: list[str], *, cwd: Path, wall: int, cpu: int, memory: int) -> subprocess.CompletedProcess[str] | None:
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.run([sys.executable, "-m", "mep.skills_runner.worker", *args], stdout=out, stderr=err, cwd=cwd,
                                  env=_env(cpu, memory), timeout=wall, check=False, start_new_session=True)
        except subprocess.TimeoutExpired:
            return None
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out.read(1 << 20).decode("utf-8", "replace"),
                                           err.read(8192).decode("utf-8", "replace"))


def run_skill(name: str, spec: dict[str, Any], *, wall_seconds: int = WALL_SECONDS, cpu_seconds: int = CPU_SECONDS,
              memory_bytes: int = MEMORY_BYTES, executor: Executor | None = None) -> SkillRunResult:
    info = get_skill(name)                                    # raises UnknownSkill for anything not enabled
    executor = executor or LocalExecutor()
    if isinstance(executor, LocalExecutor):                   # a container executor brings its own packages
        ok, why = availability(name)
        if not ok:
            raise SkillUnavailable(f"{name} {why}")
    text = json.dumps(spec, sort_keys=True)
    if len(text.encode()) > MAX_SPEC_BYTES:
        return SkillRunResult("spec_rejected", "the spec card is larger than 1 MB")
    if not _SLOTS.acquire(timeout=5):
        raise SkillUnavailable("several drafting jobs are running; try again in a minute")
    try:
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            (work / "in").mkdir()
            (work / "out").mkdir()
            os.chmod(work, 0o755)
            os.chmod(work / "in", 0o755)
            os.chmod(work / "out", 0o777)                    # the job may run as another user and must be able to write here, and only here
            (work / "in" / "spec.json").write_text(text, encoding="utf-8")
            os.chmod(work / "in" / "spec.json", 0o644)
            built = executor.build(info.name, work, wall=wall_seconds, cpu=cpu_seconds, memory=memory_bytes)
            if built is None:
                return SkillRunResult("timeout", f"the build took longer than {wall_seconds} s and was stopped")
            message = (built.stderr.strip().splitlines() or [""])[-1][:300] if built.stderr else ""
            if built.returncode == 2:
                return SkillRunResult("spec_rejected", message.removeprefix("spec rejected: ") or "the spec card was rejected")
            if built.returncode == 3:
                failed = [ln.split(":")[0].removeprefix("FAILED ") for ln in built.stderr.splitlines() if ln.startswith("FAILED ")]
                return SkillRunResult("validator_rejected", "the validator rejected the build: " + ", ".join(failed or ["see checks"]),
                                      validation={"passed": False, "failed": failed})
            if built.returncode != 0:
                return SkillRunResult("build_failed", message or "the build failed")
            return _release(info.name, work, executor, wall_seconds, cpu_seconds, memory_bytes)
    finally:
        _SLOTS.release()


def _release(skill: str, work: Path, executor: Executor, wall: int, cpu: int, memory: int) -> SkillRunResult:
    out = work / "out"
    try:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        listed = manifest["files"]
        assert isinstance(listed, list) and listed
    except (OSError, ValueError, KeyError, AssertionError):
        return SkillRunResult("revalidation_failed", "the build left no readable manifest")
    files: list[RunFile] = []
    total = 0
    for entry in listed:
        name = str(entry.get("name", ""))
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return SkillRunResult("revalidation_failed", f"the manifest names an unsafe file: {name!r}")
        path = out / name
        try:
            data = path.read_bytes()
        except OSError:
            return SkillRunResult("revalidation_failed", f"{name} is listed in the manifest but missing")
        total += len(data)
        if not data or len(data) > FILE_BYTES or total > MAX_TOTAL_BYTES:
            return SkillRunResult("revalidation_failed", f"{name} is empty or too large")
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.get("sha256") or len(data) != entry.get("bytes"):
            return SkillRunResult("revalidation_failed", f"{name} does not match the manifest's checksum")
        role = str(entry.get("role", name.rsplit(".", 1)[-1]))
        files.append(RunFile(name, role, MEDIA_TYPES.get(name.rsplit(".", 1)[-1].lower(), "application/octet-stream"), len(data), digest, data))
    manifest_bytes = (out / "manifest.json").read_bytes()
    files.append(RunFile("manifest.json", "manifest", "application/json", len(manifest_bytes), hashlib.sha256(manifest_bytes).hexdigest(),
                         manifest_bytes))
    checked = executor.validate(skill, work, wall=wall, cpu=cpu, memory=memory)
    if checked is None or checked.returncode != 0:
        return SkillRunResult("revalidation_failed", "the independent re-check did not finish", manifest=manifest)
    try:
        verdict = json.loads(checked.stdout)
    except ValueError:
        return SkillRunResult("revalidation_failed", "the independent re-check gave no verdict", manifest=manifest)
    if verdict.get("passed") is not True or verdict.get("failed"):
        for f in files:
            f.content = None
        return SkillRunResult("revalidation_failed", "the independent re-check failed: " + ", ".join(verdict.get("failed", [])),
                              validation=verdict, files=files, manifest=manifest)
    return SkillRunResult("ok", "", validation=verdict, files=files, manifest=manifest)
