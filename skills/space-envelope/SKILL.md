---
name: space-envelope
description: Draws the rooms and plant rooms of one storey from scratch as plan outlines with a clear height, and writes an IFC4 model of IfcSpaces plus a plan DXF; use it when an engineer or the designer agent needs room envelopes where no architect model exists yet.
---

# space-envelope

## When to use
- Rooms and plant rooms for ONE storey, drawn from scratch: rectangles or simple polygons (an L-shaped plant room), each with a clear height and an optional ceiling void.
- The IFC and DXF this writes are read back by the app's own ingest (names, areas, storey), so they can start a revision before the architect's model arrives.
- Geometry only. This skill never decides compliance and does not check room sizes, plant clearances or any standard.

## Spec card (inputs)
Defined in `spec_card.json` (JSON Schema, lengths in mm). Required fields block the build.

| Field | Unit | Required | Firm default allowed |
| --- | --- | --- | --- |
| mark (file stem, IFC project name) | text | yes | no |
| storey.name | text | yes | no |
| storey.floor_to_floor_mm | mm | yes (engineer input) | no |
| storey.elevation_mm | mm | no (0) | yes |
| rooms[].name (unique, ignoring case) | text | yes | no |
| rooms[].kind (`room`, `plant_room`) | enum | no (`room`) | no |
| rooms[].outline: `rectangle` (x, y, width, depth) or `polygon` (3 to 64 points) | mm | yes | no |
| rooms[].height_mm (clear height) | mm | yes (engineer input) | no |
| rooms[].ceiling_void_mm | mm | no | yes |
| rooms[].use (free-text label, not interpreted) | text | no | yes |
| notes | text | no | yes |

Refused: an outline that crosses, touches or folds back on itself, a room under 0.25 m2, two rooms that overlap (touching is fine), duplicate
names, a clear height plus ceiling void above the storey height, non-finite numbers, unknown fields, control and direction-override characters.

## Conventions
- Metric only: mm in the spec and in the DXF (`$INSUNITS` 4), IFC length unit millimetre, area unit square metre.
- Outlines are normalised counter-clockwise, starting at the lowest-then-leftmost corner, rounded to 0.001 mm.
- IFC4 (Reference View): project, site, building, one storey, one `IfcSpace` per room aggregated under the storey, an extruded-area body, `Pset_SpaceCommon`
  (`OccupancyType`), `Qto_SpaceBaseQuantities` (`NetFloorArea`, `GrossFloorArea`, `Height`) and `MEP_SpaceEnvelope` (`ClearHeightMm`, `PlantRoom`, `CeilingVoidMm`).
  A plant room is `USERDEFINED` / `PLANT ROOM`. A room's `GlobalId` is derived from the mark and the room name, so a re-issued drawing with more rooms keeps the old ids.
- DXF layers: `A-SPACE` (rooms), `A-SPACE-PLANT` (plant rooms), `A-ANNO-TEXT` (one label per room, inside it). Closed straight polylines only, tagged with XDATA `MEPSPACE` (room name).
- Deterministic: same spec card gives identical bytes (header clock, GlobalIds, DXF GUIDs and section order fixed; tested over several `PYTHONHASHSEED` values).

## How it runs
```bash
uv run python skills/space-envelope/scripts/build.py --spec skills/space-envelope/examples/office_floor/spec.json --out out/
```
Needs `ifcopenshell`, `ezdxf`, `jsonschema` (all project dependencies). Exit codes: 0 built, 2 spec rejected, 3 validator rejected, 4 build failed.
On 3 and 4 `--out` holds no file from this build (files are staged, then moved into place, the manifest last).

## Outputs
`<mark>.ifc`, `<mark>.dxf`, `manifest.json` (inputs, spec hash, files with sha256, per-room measures, validation summary, toolchain).

## Validator checks (`validator.py`, independent re-read of both files; 0.5 mm on coordinates, 0.1 % on areas)
29 checks, all must pass or no file is returned: `input_files`, `spec_readable`; IFC: `ifc_loads`, `ifc_schema`, `ifc_units`, `ifc_project`, `ifc_storey`, `ifc_space_count`,
`ifc_space_names`, `ifc_space_guids`, `ifc_aggregation`, `ifc_footprints`, `ifc_area_quantity`, `ifc_geometry_area` (the geometry kernel's own footprint area),
`ifc_heights`, `ifc_plant_flag`, `ifc_use`, `ifc_ceiling_void`; DXF: `dxf_loads`, `dxf_units`, `dxf_layers`, `dxf_entities` (nothing but closed straight
polylines on the room layers and text on `A-ANNO-TEXT`, at z = 0), `dxf_room_count`, `dxf_outlines`, `dxf_areas`, `dxf_labels` (one per room, inside its own room);
both: `cross_ifc_dxf` (same outline in each), `rooms_no_overlap` (measured from the IFC), `manifest_checksums`.
Known limits: rectangles and straight-sided polygons only (no arcs); one storey per build; the manifest `measures` are the builder's numbers.

## Worked examples
`examples/office_floor` (three rooms) and `examples/plant_room_l_shape` (an L-shaped plant room and a lift motor room on a roof level), each with `expected_manifest.json`.

## Never
- Decide compliance. This skill makes geometry only.
- Encode room-size, clearance or plant-space requirements: dimensions are engineer inputs.
- Call the network, a database or an LLM.
- Return files whose validator failed.
