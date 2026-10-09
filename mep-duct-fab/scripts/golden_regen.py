#!/usr/bin/env python3
"""Show how engine output differs from the golden expected files; write them only with --accept.

A difference is a finding, not a chore: do not accept it to make a test pass. New projects start
with `--accept` once, then `derivation.yaml` (written by hand first) is checked against the output.
"""
import difflib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import golden_lib as gl
from mep.engine.loader import load_pack


def dump(obj: object) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    accept = "--accept" in argv
    pack = load_pack(gl.ROOT / "rules")
    differences = 0
    for directory in gl.projects():
        out = gl.run_project(directory, pack)
        files = {"expected_refusal.json": out["refusal"]} if "refusal" in out else {
            "expected_report.json": gl.normalise(out["report"])}
        if "report" in out and out["ledger"]:
            files["expected_ledger.json"] = out["ledger"]
        for name, data in files.items():
            target = directory / name
            new = dump(data)
            old = target.read_text(encoding="utf-8") if target.exists() else ""
            if new != old:
                differences += 1
                print(f"--- {directory.name}/{name}")
                sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True), n=1))
                if accept:
                    target.write_text(new, encoding="utf-8")
    print(f"{differences} file(s) differ" + ("; written" if accept and differences else ""))
    return 0 if accept or differences == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
