---
name: space-envelope-stack
description: Draws the room and plant-room envelopes of several storeys as one IFC4 file with a storey per level plus a plan DXF per storey; use it for a multi-storey building where space-envelope's single storey is not enough.
---

# space-envelope-stack

## When to use
- Make the room envelopes of two or more stacked storeys in one IFC4 file, with a plan DXF for each storey.
- Geometry only. This skill never decides compliance. Same room rules as `space-envelope`; for one storey use that skill.

## Spec card (inputs)
Defined in `spec_card.json` (JSON Schema, lengths in mm).

| Field | Unit | Required | Firm default allowed |
| --- | --- | --- | --- |
| mark (at most 24 characters) | text | yes | no |
| storeys[].name | text | yes | no |
| storeys[].elevation_mm | mm | yes | no |
| storeys[].floor_to_floor_mm | mm | yes (engineer input) | no |
| storeys[].rooms[]: name, outline, height_mm, kind, use, ceiling_void_mm | as space-envelope | per room | use and ceiling void yes |

## Conventions
- Lowest storey first. Storeys must stack: each elevation is the one below plus that storey's floor-to-floor height (1 mm tolerance). Storey names are unique and room names are unique across the whole drawing.
- Output: `<mark>.ifc` (one project, site, building, one `IfcBuildingStorey` per level with its rooms as `IfcSpace`), `<mark>-S01.dxf`, `<mark>-S02.dxf` ... (one plan per storey, in that storey's own coordinates) and `manifest.json`.
- Deterministic: GlobalIds are functions of mark, storey and room name; same card gives identical files on one toolchain.

## How it runs
```bash
python skills/space-envelope-stack/scripts/build.py --spec skills/space-envelope-stack/examples/two_storeys/spec.json --out out/
```
Exit codes: 0 built, 2 spec rejected, 3 validator rejected, 4 build failed; on 3 and 4 `--out` holds no file from this build.

## Validator checks
`validator.py` re-reads the files (tolerance 0.5 mm; areas 0.1 %):
- the IFC is IFC4 in millimetres with one project and exactly the specified storeys (names and elevations).
- every room is aggregated under its own storey with the right GlobalId, footprint, height, area quantity and plant flag; no other spaces exist.
- each storey's DXF passes space-envelope's own DXF checks against that storey's rooms (units, layers, entities, outlines, areas, labels).
- manifest checksums match, and the manifest lists exactly the files present.

## Worked example
`examples/two_storeys/spec.json` → `examples/two_storeys/expected_manifest.json`.

## Never
- Decide compliance. Call the network.
