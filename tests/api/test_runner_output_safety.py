"""What the dispatcher host does with a job's output directory: the job is untrusted, so links, FIFOs, devices, directories and oversized files
are refused BEFORE anything is read, and nothing is released."""
import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest
from mep.skills_runner import runner
from mep.skills_runner.runner import UnsafeOutput, check_output_dir, read_regular, run_skill

SPEC = json.loads((Path(__file__).resolve().parents[2] / "skills/space-envelope/examples/office_floor/spec.json").read_text(encoding="utf-8"))


class Hostile:
    """An executor whose build runs a genuine build, then tampers with the output directory like a compromised job would."""

    def __init__(self, plant):
        self.plant = plant

    def build(self, skill, work, *, wall, cpu, memory):
        runner.LocalExecutor().build(skill, work, wall=wall, cpu=cpu, memory=memory)
        self.plant(work / "out")
        return subprocess.CompletedProcess([], 0, "", "")

    def validate(self, skill, work, *, wall, cpu, memory):
        raise AssertionError("the re-check must not run on refused output")


def plant_fifo(out):
    os.mkfifo(out / "extra.ifc")


def plant_symlink_manifest(out):
    (out / "manifest.json").unlink()
    os.symlink("/dev/zero", out / "manifest.json")


def plant_symlink_file(out):
    name = next(p.name for p in out.iterdir() if p.suffix == ".ifc")
    (out / name).unlink()
    os.symlink("/etc/passwd", out / name)


def plant_dir(out):
    (out / "sub").mkdir()


def plant_many(out):
    for i in range(runner.MAX_OUTPUT_FILES + 2):
        (out / f"f{i}.txt").write_text("x")


@pytest.mark.parametrize("plant", [plant_fifo, plant_symlink_manifest, plant_symlink_file, plant_dir, plant_many])
def test_hostile_output_is_refused_quickly_and_releases_nothing(plant):
    start = time.monotonic()
    result = run_skill("space-envelope", SPEC, executor=Hostile(plant))
    assert time.monotonic() - start < 60                    # nothing blocked on a FIFO or an endless device
    assert result.status == "revalidation_failed" and not result.released and all(f.content is None for f in result.files)


def test_read_regular_refuses_links_fifos_devices_and_big_files(tmp_path):
    ok = tmp_path / "ok"
    ok.write_bytes(b"abc")
    assert read_regular(ok, 10) == b"abc"
    with pytest.raises(UnsafeOutput):
        read_regular(ok, 2)
    link = tmp_path / "link"
    os.symlink(ok, link)
    with pytest.raises(UnsafeOutput):
        read_regular(link, 10)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(UnsafeOutput):
        read_regular(fifo, 10)
    assert stat.S_ISCHR(os.stat("/dev/zero").st_mode)
    with pytest.raises(UnsafeOutput):
        read_regular(Path("/dev/zero"), 10)
    with pytest.raises(UnsafeOutput):
        check_output_dir(tmp_path)                          # holds a link and a FIFO
