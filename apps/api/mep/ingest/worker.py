"""The reader process: `python -m mep.ingest.worker <ifc|dxf> <path>` prints the result as JSON on stdout.

Run only by ingest/sandbox.py, which applies the CPU, memory and wall-clock limits. Exit codes: 0 read, 3 refused on purpose (the
message on stderr is safe to show), 4 the file could not be read (only the error's type is reported).
"""
import json
import sys

from mep.ingest.records import IngestRefused, result_to_json


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("ifc", "dxf"):
        print("usage: python -m mep.ingest.worker <ifc|dxf> <path>", file=sys.stderr)
        return 2
    kind, path = argv
    try:
        if kind == "ifc":
            from mep.ingest.ifc import read_ifc
            result = read_ifc(path)
        else:
            from mep.ingest.dxf import read_dxf
            result = read_dxf(path)
    except IngestRefused as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - any failure to read a damaged file
        print(type(exc).__name__, file=sys.stderr)
        return 4
    json.dump(result_to_json(result), sys.stdout, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
