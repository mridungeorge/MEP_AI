# duct-fab skill

Status: **built, not integrated.** Geometry only: the skill never decides compliance. Checked 2026-10-09 in the devcontainer
(Linux, Python 3.12, cadquery 2.8.0, cadquery-ocp 7.9.3.1.1, ezdxf 1.4.4). Source: `skills/duct-fab/`, shared kit
`skills/cad/` (`cadkit.py` STEP solids, `develop.py` triangulation development). Tests: `tests/skills/`.

## Fittings
| Fitting | Inputs (mm) | Notes |
|---|---|---|
| `rect_to_round` | `width_mm`, `height_mm`, `diameter_mm`, `length_mm`; optional `offset_x_mm`, `offset_y_mm`, `circle_segments` (multiple of 8, default 16) | the round end is a regular N-gon in the developed pattern |
| `rect_reducer` | `width_in_mm`, `height_in_mm`, `width_out_mm`, `height_out_mm`, `length_mm`, `alignment` (`concentric`, `flat_bottom`, `flat_top`, `flat_left`, `flat_right`) | |
| `rect_offset` | `width_mm`, `height_mm`, `length_mm`, `offset_x_mm`, `offset_y_mm` (not both zero) | |

Required engineer inputs (no gauge or allowance tables are encoded): `sheet_thickness_mm`, `seam.type` and
`seam.allowance_mm`, `connection.type` and `connection.allowance_mm`. Outputs per build: `<mark>.step`, `<mark>.dxf`,
`manifest.json` (inputs, spec hash, file hashes, measures, validation summary, toolchain). Exit codes: 0 built, 2 spec
rejected, 3 validator rejected (nothing is written to `--out`).

## Validator checks (`skills/duct-fab/validator.py`, independent re-read of both files, 0.5 mm tolerance)
24 checks per build, all must pass or no file is returned:
`input_files`, `spec_readable`; STEP: `step_solid`, `step_planar_faces`, `step_length`, `step_vertex_count`,
`step_inlet_cap`, `step_outlet_cap`, `step_outlet_offset`, `step_bbox_xy`; DXF: `dxf_loads`, `dxf_layers`,
`cut_closed_single`, `cut_no_zero_length`, `cut_no_self_intersection`, `net_connected`, `net_no_self_intersection`,
`edge_lengths_match_3d`, `net_area_matches_lateral_area` (0.1 %), `net_inlet_perimeter`, `net_outlet_perimeter`,
`seam_allowance`, `connection_allowance`, `title_block`. A supplied manifest is also checked against file checksums.

## Results (2026-10-09)
- `tests/skills`: **132 passed, 0 skipped** (14 develop, 19 build, 99 validator including mutation tests that corrupt a
  spec/file and expect the matching check to fail). Without cadquery/ezdxf installed, the build and validator tests skip
  (the root `uv.lock` does not include them), and the OCP wheel also needs the system library `libGL`.
- Examples regenerated (`skills/duct-fab/examples/*/spec.json`), all four validate 24/24:
  | Example | Manifest vs committed `expected_manifest.json` |
  |---|---|
  | `rect_reducer` | byte-identical |
  | `rect_offset` | byte-identical |
  | `rect_to_round` | **differs**: only the DXF `sha256` (same byte count, STEP hash identical) |
  | `rect_to_round_offset` | **differs**: only the DXF `sha256` (same byte count, STEP hash identical) |
  A second build on this machine is byte-identical to the first, so the build is deterministic here. The difference
  appears only for the two round-end fittings; the committed hashes were generated on an earlier machine (likely Windows).
  Not determined: whether the cause is platform floating point (sin/cos in the N-gon) or a change in the DXF writer.
  The committed manifests were NOT changed. Decision needed: regenerate them on the Linux gate toolchain, or find the cause.

## Integration needed (nothing below is done)
1. `cadquery`, `cadquery-ocp`, `ezdxf` (and `jsonschema`) are only in `skills/duct-fab/requirements.txt`, not in
   `pyproject.toml`/`uv.lock`; add an optional group and lock it.
2. `.devcontainer/Dockerfile` needs `libgl1` (and `libglu1-mesa`) for OCP; it was installed by hand in the running container.
3. `scripts/ci.sh` and `.github/workflows/ci.yml` do not run `tests/skills`; add it once (1) and (2) are done.
4. The two round-end example manifests above (hash decision).
5. `apps/api/mep/skills_runner/` is empty: nothing invokes the skill, enforces "validator failed means no file returned"
   at the API (non-negotiable 9), stores outputs, or links a fitting to a duct in a project.
6. No UI, no mapping from schedule/IFC duct data to a spec card; engineer inputs (thickness, allowances) have no place
   in Gate 1 yet.
7. `skills/cad/` adapts the MIT-licensed `earthtojake/text-to-cad` approach (`LICENSE`, `NOTICE` are in place); keep both
   if the code is ever distributed.
8. Adversarial review: see below.

## Adversarial review
Before this session the review status was **unknown** (no history survived). One review is run in this session (max two
rounds); the outcome is recorded here.
