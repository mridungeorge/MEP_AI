# duct-fab skill

Status: **built, reviewed once (round 1 fixed, round 2 below), not integrated.** Geometry only: the skill never decides
compliance. Checked 2026-10-09 in the devcontainer (Linux, Python 3.12, cadquery 2.8.0, cadquery-ocp 7.9.3.1.1,
ezdxf 1.4.4). Source: `skills/duct-fab/`, shared kit `skills/cad/` (`cadkit.py` STEP solids, `develop.py` triangulation
development). Tests: `tests/skills/`.

## Fittings
| Fitting | Inputs (mm) | Notes |
|---|---|---|
| `rect_to_round` | `width_mm`, `height_mm`, `diameter_mm`, `length_mm`; optional `offset_x_mm`, `offset_y_mm`, `circle_segments` (multiple of 8, default 16) | the round end is a regular N-gon in the developed pattern |
| `rect_reducer` | `width_in_mm`, `height_in_mm`, `width_out_mm`, `height_out_mm`, `length_mm`, `alignment` (`concentric`, `flat_bottom`, `flat_top`, `flat_left`, `flat_right`) | |
| `rect_offset` | `width_mm`, `height_mm`, `length_mm`, `offset_x_mm`, `offset_y_mm` (not both zero) | |

Dimensions are at least 10 mm. Required engineer inputs (no gauge or allowance tables are encoded): `sheet_thickness_mm`,
`seam.type` and `seam.allowance_mm`, `connection.type` and `connection.allowance_mm`. Outputs per build: `<mark>.step`,
`<mark>.dxf`, `manifest.json`. Exit codes: 0 built, 2 spec rejected, 3 validator rejected, 4 build failed (kernel error or
the output could not be written). On 3 and 4 the output folder holds no file from the build.

## Validator checks (`skills/duct-fab/validator.py`, independent re-read of both files, 0.5 mm tolerance)
27 checks per build, all must pass or no file is returned: `input_files`, `spec_readable`; STEP: `step_solid`,
`step_planar_faces`, `step_length`, `step_vertex_count`, `step_inlet_cap`, `step_outlet_cap`, `step_outlet_offset`,
`step_bbox_xy`; DXF: `dxf_loads`, `dxf_layers`, `dxf_units` (millimetres), `dxf_entities` (nothing but BEND lines, one CUT
polyline, annotation), `cut_closed_single`, `cut_no_zero_length`, `cut_no_self_intersection`, `net_connected`,
`net_no_self_intersection`, `edge_lengths_match_3d`, `rulings_on_net` (each fold line joins an inlet vertex to an outlet
vertex), `net_area_matches_lateral_area` (0.1 %), `net_inlet_perimeter`, `net_outlet_perimeter`, `seam_allowance`,
`connection_allowance`, `title_block` (sizes, offset, thickness, seam, connection, units and scope lines). A supplied
manifest is also checked against file checksums.

## Results (2026-10-09, after review round 1)
- `tests/skills`: **167 passed, 0 skipped** (14 develop, build and CLI tests, and a validator suite whose mutation tests
  corrupt a good build and expect the named check to fail, including a fold line moved sideways by the same length).
  Without cadquery/ezdxf installed the build and validator tests skip (`uv.lock` does not hold them).
- Examples regenerated from `skills/duct-fab/examples/*/spec.json`; all four validate 27/27. Output is **identical across
  12 `PYTHONHASHSEED` values** for every example (DXF, STEP and manifest hashes), and the committed
  `expected_manifest.json` files were regenerated from that output.
- **Correction of an earlier version of this page:** it said two manifests differed because of platform floating point and
  that a rerun was byte-identical. Both were wrong. The review found the DXF `CLASSES` section followed set order, so the
  DXF bytes changed with `PYTHONHASHSEED` (about one run in two); the earlier "identical rerun" and the hash-seed test
  passed by luck. Fixed in `skills/cad/cadkit.py` (`canonical_dxf` orders `CLASSES` as well as `OBJECTS`).
- Round 1 findings fixed: B1 (above); B2 (validator did not check where fold lines sit); partial output on a failed write and
  tracebacks on schema-valid but degenerate specs (now exit 4 / exit 2 with rollback); DXF units, stray entities and most of
  the title block were unchecked; near-zero offset and near-equal reducer ends; control characters in free text; Windows
  reserved file names.

## Known limits (not fixed)
- The tolerance is absolute (0.5 mm): on a 10 mm part it is 5 %. Dimensions below 10 mm are refused.
- `manifest.json` `measures` are the builder's numbers and are not validated.
- Marks that differ only in case collide on case-insensitive file systems.
- Byte identity has been checked on Linux only; a different OCC/ezdxf version needs the manifests regenerated.

## Integration needed (nothing below is done)
1. `cadquery`, `cadquery-ocp`, `ezdxf` (and `jsonschema`) are only in `skills/duct-fab/requirements.txt`, not in
   `pyproject.toml`/`uv.lock`; add an optional group and lock it.
2. `.devcontainer/Dockerfile` needs `libgl1` (and `libglu1-mesa`) for OCP; it was installed by hand in the running container.
3. `scripts/ci.sh` and `.github/workflows/ci.yml` do not run `tests/skills`; add it once (1) and (2) are done.
4. `apps/api/mep/skills_runner/` is empty: nothing invokes the skill, enforces "validator failed means no file returned"
   at the API (non-negotiable 9), stores outputs, or links a fitting to a duct in a project.
5. No UI, no mapping from schedule/IFC duct data to a spec card; engineer inputs (thickness, allowances) have no place
   in Gate 1 yet.
6. `skills/cad/` adapts the MIT-licensed `earthtojake/text-to-cad` approach (`LICENSE`, `NOTICE` are in place); keep both
   if the code is ever distributed.

## Adversarial review
Before this session the review status was **unknown** (no history survived). Round 1 (this session): 2 blockers, 7
should-fix, 9 nits; the blockers and most should-fixes are fixed as above. Round 2: see the end of this file once run.
