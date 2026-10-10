"""The reader process: `python -m mep.ingest.worker <ifc|dxf> <path>` prints the result as JSON on stdout.

Run only by ingest/sandbox.py. It applies its own resource limits before it opens the file (the sandbox passes them in the
environment): CPU time, address space, and the size of any file written (stdout and stderr are files, so this caps the output).
Exit codes: 0 read, 3 refused on purpose (the last stderr line is safe to show), 5 the result is larger than the output limit, 4 the file could not be read (only the error's type
is reported).
"""
import json
import os
import resource
import sys

from mep.ingest.records import IngestRefused, result_to_json


def apply_limits() -> None:
    for name, limit in (("MEP_WORKER_CPU", resource.RLIMIT_CPU), ("MEP_WORKER_AS", resource.RLIMIT_AS),
                        ("MEP_WORKER_FSIZE", resource.RLIMIT_FSIZE)):
        value = os.environ.get(name)
        if value and value.isdigit():
            n = int(value)
            resource.setrlimit(limit, (n, n + 5 if limit == resource.RLIMIT_CPU else n))


def render(path: str) -> int:
    """Render a PDF's pages for the vision step: JSON with base64 PNGs on stdout (the file-size limit caps it)."""
    import base64

    from mep.ingest.pdf_render import PdfRenderError, render_pdf
    try:
        out = render_pdf(path)
    except PdfRenderError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 - any failure of the PDF library on a hostile file
        print(type(exc).__name__, file=sys.stderr)
        return 4
    for p in out["pages"]:
        p["png"] = base64.b64encode(p["png"]).decode("ascii")
    try:
        json.dump(out, sys.stdout)
        sys.stdout.flush()
    except OSError:
        print("result too large", file=sys.stderr)
        sys.stderr.flush()
        os._exit(5)
    return 0


def main(argv: list[str]) -> int:
    apply_limits()
    if len(argv) != 2 or argv[0] not in ("ifc", "dxf", "pdf-render"):
        print("usage: python -m mep.ingest.worker <ifc|dxf|pdf-render> <path>", file=sys.stderr)
        return 2
    kind, path = argv
    if kind == "pdf-render":
        return render(path)
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
    try:
        json.dump(result_to_json(result), sys.stdout, default=str)
        sys.stdout.flush()
    except OSError:          # the file-size limit refused the write (Python ignores SIGXFSZ): the result is too large
        print("result too large", file=sys.stderr)
        sys.stderr.flush()
        os._exit(5)          # not a normal return: exit would try to flush the refused output again and change the code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
