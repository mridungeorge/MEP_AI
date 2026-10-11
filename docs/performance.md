# Performance (Phase 10.5)

## Targets

- A rule run finishes in under 10 s for a 200-space, 20-system project.
- Every other page or download (diff, package.pdf, lists) answers in under 3 s.

## What was measured, and where

Pure code only, no web stack and no database: `scripts/perf_project.py` builds a synthetic project (200 spaces, 20 systems, every
selected NCC 2025 / VIC rule fed engineer-confirmed inputs, 200 result lines) and times each part. Run it with
`python scripts/perf_project.py`; the pytest guards are in `tests/perf/test_perf.py` (about 20 s in total, no marker).

Machine: the project devcontainer (Linux, Python 3.12) on a laptop, small RAM, single process, cold interpreter for the fixtures.
Numbers vary run to run by about 2x; the test bounds are the product targets, not these timings.

| Part | Workload | Measured | Test bound |
|---|---|---|---|
| Engine run | 20 systems x 10 rules, 200 results | 0.5 to 0.9 s | 10 s |
| Revision diff + cross-rule rerun | 200 spaces (28 changed or removed, 5 added), 20 systems | 0.3 to 0.8 s | 10 s |
| Package PDF (`to_pdf`) | 400 result lines | 1.2 to 2.2 s | 10 s |
| Duct sizing arithmetic | 2000 runs | 1.0 to 1.7 s | 10 s |
| Clash-lite detect, early stop | 500 ducts x 20000 boxes, limit 2000 | 0.2 s | 10 s |
| Clash-lite detect, no hits (worst case) | 500 ducts x 20000 boxes | 0.2 s (was 28 s) | 10 s |

## Fix made: clash-lite worst case

`clash.detect` compared every duct with every element. When nothing clashes the early stop never fires, and 500 x 20000 = 10 million
pair checks took 28 s. It now indexes each model's elements by low x once and checks only the elements whose x range is within the
clearance of the duct (a binary search window; the same pairs in the same order). The no-hit worst case dropped to 0.2 s, and
`test_indexed_clash_detect_matches_checking_every_box` proves the result equals checking every box. A model made of very long boxes widens
the window and degrades gracefully towards the old cost, never to a wrong answer.

## Not measured here

- The full HTTP load test was NOT run. `tests/perf/locustfile.py` is written (upload, run-rules, diff, package.pdf, with the 10 s / 3 s
  targets marked as failures) but locust is not installed in this project and no API plus database was started for it. Install locust in a
  throwaway environment and run it against a staging API with `MEP_TOKEN` and `MEP_REVISION` set.
- `quantities_of` is database-bound and was skipped.
- The cross-rule rerun in the synthetic revision found no outcome moves, so it is timed but lightly exercised.
- Database time (row inserts for 200 results, `assemble` queries, RLS) is not in these numbers and is the likeliest place for the real
  page times to differ.
