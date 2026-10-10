"""The drafting worker's container isolation. The command line is built in ONE place (sandbox_docker.docker_run_args); the flag tests check it, and the
container tests run real probes with exactly that command: no network, no writes outside the output directory, not root.

Container tests need a Docker runtime and an image with Python: MEP_WORKER_IMAGE (the real worker image, built in CI) or MEP_ISOLATION_IMAGE
(any image with python, e.g. python:3.12-slim). Without one they are skipped, and the skip is reported.
"""
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest
from mep.skills_runner.runner import run_skill
from mep.skills_runner.sandbox_docker import DockerExecutor, docker_run_args

ROOT = Path(__file__).resolve().parents[2]
IMAGE = os.environ.get("MEP_WORKER_IMAGE") or os.environ.get("MEP_ISOLATION_IMAGE")
needs_container = pytest.mark.skipif(not (IMAGE and shutil.which("docker")), reason="no MEP_WORKER_IMAGE / MEP_ISOLATION_IMAGE or no docker")


def _args(out_writable=True):
    return docker_run_args("img", "n", Path("/a/in"), Path("/a/out"), out_writable=out_writable, cpu_seconds=60, memory_bytes=1 << 30,
                           file_bytes=1 << 20, command=["build", "x"])


def test_the_command_line_isolates_the_job():
    a = _args()
    pairs = {a[i]: a[i + 1] for i in range(len(a) - 1)}
    assert pairs["--network"] == "none" and "--read-only" in a and pairs["--user"] == "10001:10001" and pairs["--cap-drop"] == "ALL"
    assert pairs["--security-opt"] == "no-new-privileges" and pairs["--memory"] == str(1 << 30) and pairs["--memory-swap"] == str(1 << 30)
    assert "/a/in:/job/in:ro" in a and "/a/out:/job/out:rw" in a and "-v" in a
    assert "--privileged" not in a and not any(x.startswith(("--cap-add", "--device")) for x in a)
    assert "/a/out:/job/out:ro" in _args(out_writable=False)                                   # the re-check cannot write the output either
    assert "--rm" in a and a[-3:] == ["img", "build", "x"]


def _probe(code: str, *, out_writable=True) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / "in").mkdir()
        (work / "out").mkdir()
        os.chmod(work, 0o755)
        os.chmod(work / "in", 0o755)
        os.chmod(work / "out", 0o777)
        args = docker_run_args(IMAGE or "", f"mep-probe-{uuid.uuid4().hex[:8]}", work / "in", work / "out", out_writable=out_writable, cpu_seconds=30,
                               memory_bytes=1 << 30, file_bytes=1 << 20, command=["-c", code], entrypoint="python")
        return subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)


@needs_container
def test_the_job_runs_as_a_non_root_user():
    r = _probe("import os; print(os.getuid())")
    assert r.returncode == 0 and r.stdout.strip() == "10001", r.stderr


@needs_container
def test_network_access_fails():
    code = ("import socket\n"
            "for host in ('1.1.1.1', '8.8.8.8', 'example.com'):\n"
            "    try:\n"
            "        socket.create_connection((host, 53), timeout=3)\n"
            "        print('CONNECTED', host)\n"
            "    except OSError:\n"
            "        pass\n"
            "print('done')\n")
    r = _probe(code)
    assert r.returncode == 0 and "CONNECTED" not in r.stdout and "done" in r.stdout, r.stdout + r.stderr


@needs_container
def test_writes_outside_the_output_directory_fail_and_the_output_directory_works():
    code = ("import os\n"
            "def try_write(path):\n"
            "    try:\n"
            "        with open(path, 'w') as f:\n"
            "            f.write('x')\n"
            "        return 'WROTE'\n"
            "    except OSError as e:\n"
            "        return 'DENIED'\n"
            "for p in ('/escape.txt', '/srv/escape.txt', '/usr/escape.txt', '/etc/escape.txt', '/job/in/escape.txt', '/job/escape.txt'):\n"
            "    print(p, try_write(p))\n"
            "print('/job/out/ok.txt', try_write('/job/out/ok.txt'))\n")
    r = _probe(code)
    lines = dict(line.rsplit(" ", 1) for line in r.stdout.strip().splitlines())
    assert r.returncode == 0, r.stderr
    assert {k: v for k, v in lines.items() if k != "/job/out/ok.txt"} == {k: "DENIED" for k in lines if k != "/job/out/ok.txt"} and len(lines) == 7
    assert lines["/job/out/ok.txt"] == "WROTE"


@needs_container
def test_the_recheck_container_cannot_write_the_output_directory():
    r = _probe("open('/job/out/x', 'w')", out_writable=False)
    assert r.returncode != 0 and ("Read-only" in r.stderr or "Permission denied" in r.stderr)


@needs_container
def test_a_hanging_job_is_killed_by_the_wall_clock():
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / "in").mkdir()
        (work / "out").mkdir()
        ex = DockerExecutor(IMAGE or "")
        # drive the executor's own runner with a command that never ends
        got = ex._run(work, ["sleep", "600"], out_writable=True, wall=3, cpu=5, memory=1 << 30)
    assert got is None or got.returncode != 0


EXAMPLES = ROOT / "skills" / "space-envelope" / "examples"


@pytest.mark.skipif(not (os.environ.get("MEP_WORKER_IMAGE") and shutil.which("docker")), reason="needs the built worker image (MEP_WORKER_IMAGE)")
@pytest.mark.parametrize("name", ["office_floor", "plant_room_l_shape"])
def test_the_real_worker_image_builds_and_validates_space_envelope_twice(name):
    spec = json.loads((EXAMPLES / name / "spec.json").read_text(encoding="utf-8"))
    result = run_skill("space-envelope", spec, executor=DockerExecutor(os.environ["MEP_WORKER_IMAGE"]))
    assert result.status == "ok" and result.released and {f.role for f in result.files} >= {"ifc", "dxf", "manifest"}
    expected = json.loads((EXAMPLES / name / "expected_manifest.json").read_text(encoding="utf-8"))
    assert [(f["name"], f["sha256"]) for f in result.manifest["files"]] == [(f["name"], f["sha256"]) for f in expected["files"]]


@pytest.mark.skipif(not (os.environ.get("MEP_WORKER_IMAGE") and shutil.which("docker")), reason="needs the built worker image (MEP_WORKER_IMAGE)")
def test_the_real_worker_image_carries_the_cad_kernel_for_duct_fab():
    spec = json.loads((ROOT / "skills" / "duct-fab" / "examples" / "rect_reducer" / "spec.json").read_text(encoding="utf-8"))
    result = run_skill("duct-fab", spec, executor=DockerExecutor(os.environ["MEP_WORKER_IMAGE"]))
    assert result.status == "ok" and result.released
