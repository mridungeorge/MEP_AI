---
name: hvac-dxf
description: Draws a duct layout (true width) or schematic (centreline) as a DXF with firm layers, terminal symbols, a title block and a size and airflow tag on every duct; use it to issue the sized duct system as a drawing.
---

# hvac-dxf

## When to use
- Issue the duct system from the sizing schedule as a plan DXF: ducts, terminals, tags, frame and title block.
- Geometry and text only. This skill never decides compliance and does not size ducts (sizing is `mep/sizing.py`; the result arrives as the `sizing_schedule`).

## Spec card (inputs)
Defined in `spec_card.json` (JSON Schema, lengths in mm, airflow in L/s).

| Field | Unit | Required | Firm default allowed |
| --- | --- | --- | --- |
| mark (file stem) | text | yes | no |
| mode (`layout` or `schematic`) | enum | yes | no |
| title_block: project, drawing_title, drawing_no, revision; optional date, drawn_by, firm | text | yes / no | firm and drawn_by yes |
| layers: duct_rect, duct_round, terminal, tag, frame, title | layer names | no | yes (firm layer standard) |
| ducts: tag, system, size (rect width/depth or round diameter), airflow, start and end points | mm, L/s | yes | no |
| terminals: tag, system, airflow, position | mm, L/s | no | no |
| sizing_schedule: tag, size, airflow (the reference every tag is checked against) | mm, L/s | yes | no |
| balance_tolerance_pct | % | no (1) | yes |

## Conventions
- Metric only; model units are millimetres in the plan's own coordinates ($INSUNITS = 4).
- Layout: each duct is a closed 4-point polyline at its true width on `duct_rect` or `duct_round`. Schematic: one centreline.
- Every duct carries a tag text `TAG SIZE AIRFLOW L/s` (for example `D1 600x300 600 L/s`, `D3 dia300 200 L/s`) and XDATA `MEPHVAC` role/tag. Terminals are a `TERMINAL` block with tag text `TAG AIRFLOW L/s`.
- Frame around the extents and a title block at its lower right, on their own layers.
- Deterministic: same spec card gives identical bytes (fixed DXF meta data, ordered sections, coordinates rounded to 1e-6 mm).

## How it runs
```bash
python skills/hvac-dxf/scripts/build.py --spec skills/hvac-dxf/examples/office_layout/spec.json --out out/
```
Needs `ezdxf`, `jsonschema`. Exit codes: 0 built, 2 spec rejected, 3 validator rejected, 4 build failed; on 3 and 4 `--out` holds no file from this build.

## Outputs
- `<mark>.dxf`, `manifest.json` (inputs, spec hash, files with sha256, measures, validation summary, toolchain).

## Validator checks
`validator.py` re-reads the DXF and compares it with the spec (tolerance 0.5 mm):
- units are millimetres; every layer exists; no entity sits on a foreign layer.
- the drawn ducts match the sizing schedule (same tags, sizes, airflows).
- every duct is drawn once, on the right layer, with the right length, width and position (or centreline in schematic mode).
- every tag text equals the text rebuilt FROM THE SCHEDULE (so a wrong size or airflow in the drawing is caught).
- terminals: one symbol and one text each, at their position.
- airflow balance per system: the trunk (largest duct airflow) equals the sum of terminal airflows within the tolerance, otherwise the build is refused.
- the title block carries every supplied field; manifest checksums match when supplied.
- Known limits: tags are not checked for overlap with other geometry; a duct can cross another; fittings are not drawn.

## Worked example
`examples/office_layout/spec.json` → `examples/office_layout/expected_manifest.json`.

## Never
- Decide compliance. This skill makes a drawing only.
- Call the network.
