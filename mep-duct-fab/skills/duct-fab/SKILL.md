---
name: duct-fab
description: Builds a rectangular-to-round transition, rectangular reducer or rectangular offset as a 3D STEP solid plus a flat-pattern DXF for the shop; use it when an engineer or the designer agent needs fabrication geometry for one fitting.
---

# duct-fab

## When to use
- Make a rectangular-to-round transition (optionally offset), a rectangular reducer (concentric or flat side) or a rectangular offset, with a developed flat pattern.
- Geometry only. This skill never decides compliance and does not check taper angles, velocities or any standard.

## Spec card (inputs)
Defined in `spec_card.json` (JSON Schema, all lengths in mm). Required fields block the build.

| Field | Unit | Required | Firm default allowed |
| --- | --- | --- | --- |
| fitting (`rect_to_round`, `rect_reducer`, `rect_offset`) | enum | yes | no |
| mark (file stem and title block) | text | yes | no |
| sheet_thickness_mm | mm | yes (engineer input, no gauge table) | no |
| seam.type / seam.allowance_mm | label / mm | yes (engineer input) | no |
| connection.type / connection.allowance_mm (both ends) | label / mm | yes (engineer input) | no |
| material, notes | text | no | yes |
| geometry: width/height/diameter/length, offsets, alignment, circle_segments | mm / enum / count | per fitting | offsets default 0, circle_segments default 16 |

Fitting geometry fields:
- `rect_to_round`: `width_mm`, `height_mm`, `diameter_mm`, `length_mm`; optional `offset_x_mm`, `offset_y_mm`, `circle_segments` (multiple of 8).
- `rect_reducer`: `width_in_mm`, `height_in_mm`, `width_out_mm`, `height_out_mm`, `length_mm`, `alignment` (`concentric`, `flat_bottom`, `flat_top`, `flat_left`, `flat_right`).
- `rect_offset`: `width_mm`, `height_mm`, `length_mm`, `offset_x_mm`, `offset_y_mm` (not both zero).

## Conventions
- Metric only. Inlet in plane z=0 centred on the origin, flow along +Z, outlet at z=L.
- The STEP solid is the nominal envelope (the net duct size). Sheet thickness is recorded, not modelled, and is not used as a bend deduction in v1.
- Flat pattern: triangulation development (the round end is a regular N-gon). Layers: `CUT` (one closed outline including allowances), `BEND` (every developed edge: fold and ruling lines, tagged with XDATA `MEPFAB` role/index), `ANNOTATION` (dimensions and title block). Pattern is drawn as seen from outside.
- Seam is on corner 0 (+X,+Y) along its middle generator. The seam allowance is one lap tab on the end of the pattern; the connection allowance is added beyond the net inlet and outlet edges.
- Deterministic: same spec card gives identical files (STEP clock and DXF GUIDs fixed; coordinates rounded to 1e-6 mm). Byte identity holds within one toolchain (see `toolchain` in the manifest).

## How it runs
```bash
python skills/duct-fab/scripts/build.py --spec skills/duct-fab/examples/rect_to_round/spec.json --out out/
```
Needs `cadquery`, `ezdxf`, `jsonschema` (`requirements.txt`). Exit codes: 0 built, 2 spec rejected, 3 validator rejected (nothing is written to `--out`).

## Outputs
- `<mark>.step`, `<mark>.dxf`, `manifest.json` (inputs, spec hash, files with sha256, measures, validation summary, toolchain).

## Validator checks
`validator.py` re-reads both files independently (tolerance 0.5 mm unless noted):
- STEP is one valid solid; key dimensions (length, end sizes, round-end radius, offset) match the spec.
- CUT outline is closed, has no zero-length edge and does not self-intersect; the net loop rebuilt from BEND lines is closed.
- Every developed edge length matches the corresponding 3D edge; developed area matches 3D lateral area (0.1 %); inlet and outlet net perimeters match the spec.
- Seam and connection allowances are present at the specified distance.
- Title block carries the spec values; manifest checksums match (when supplied).

## Worked example
`examples/rect_to_round/spec.json` → `examples/rect_to_round/expected_manifest.json` (also `rect_to_round_offset`, `rect_reducer`, `rect_offset`).

## Never
- Decide compliance. This skill makes geometry only.
- Encode AS 4254 gauge or allowance tables: thickness and allowances are engineer inputs.
- Call the network, a database or an LLM.
- Return files whose validator failed.
