# space-envelope skill

Status: **built and integrated** (Phase 4b). Source: `skills/space-envelope/` (read `SKILL.md` there for the spec card, conventions and the 29 validator checks).
Tests: `tests/skills/test_space_envelope.py` (52), `tests/rls/test_pg_skills.py`.

What it does: draws the rooms and plant rooms of one storey from scratch (rectangles or simple polygons, a clear height, an optional ceiling void) and writes an
IFC4 model of `IfcSpace`s plus a plan DXF, under the same lane rules as duct-fab: deterministic bytes, an independent validator that re-reads both files and compares
them with the spec and with each other, files staged and published only when it passes, exit codes 0 / 2 (spec rejected) / 3 (validator rejected) / 4 (failed).

How the app runs it (both skills, `apps/api/mep/skills_runner/`): the build runs in a child process with CPU, memory, file-size and wall-clock limits; the runner
then checks the manifest and every file's checksum itself and re-runs the skill's validator in a SEPARATE process; only if both pass is any artifact `released`
and its bytes stored (`artifact_blob`, which the database refuses for an unreleased artifact). A build the validator rejects leaves a run record and nothing
to download. A built IFC or DXF can be fed to the revision through the normal upload path ("use as this revision's model"): its spaces arrive as
`extracted` and need Gate 1 like any architect model. Nothing here decides compliance.

Known limits: rectangles and straight-sided polygons only (no arcs); one storey per build; the skills need `ifcopenshell`/`ezdxf` (space-envelope) and
`cadquery` (duct-fab) on the server: the API image does not carry cadquery, so duct-fab answers "not installed on this server" there (503) until the image is
built with it; the manifest `measures` are the builder's numbers.
