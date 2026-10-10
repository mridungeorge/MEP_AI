---
name: ifc-mep
description: Writes ducts, terminals and air-handling equipment, with ports, systems and port connections, into a copy of the architect's IFC4 file in the right storeys; use it to issue the designed duct system as an IFC the architect and other disciplines can federate.
---

# ifc-mep

## When to use
- Add the designed duct system (ducts, terminals, AHU or fan, connections) to the architect's model so it can be coordinated.
- Data and geometry only. This skill never decides compliance, does not size ducts and does not change anything the architect drew.

## Spec card (inputs)
Defined in `spec_card.json` (JSON Schema, lengths in mm, airflow in L/s, coordinates relative to the named storey's origin).

| Field | Unit | Required | Firm default allowed |
| --- | --- | --- | --- |
| mark (file stem) | text | yes | no |
| base_ifc_sha256 (checksum of the architect's file the job is given as `base.ifc`) | hex | yes | no |
| ducts: tag, system, storey, size (rect or round), airflow, start and end | mm, L/s | yes | no |
| terminals: tag, system, storey, airflow, position, optional box | mm, L/s | no | no |
| equipment: tag, system, storey, kind (`ahu` or `fan`), position, box | mm | no | no |
| connections: `TAG.port` to `TAG.port`, flow from the first to the second | label | no | no |

Ports: ducts `start` (SINK) and `end` (SOURCE); terminals `in` (SINK); equipment `in` (SINK) and `out` (SOURCE).

## Conventions
- Metric spec; the writer converts to the file's own length unit. IFC4 and IFC4X3 only.
- The output is a COPY of the architect's file with the new elements added. Architect entities are not edited. New elements are contained in the named storey (unique storey names only), assigned to an `IfcDistributionSystem` per system tag, and carry a `MEP_Services` property set.
- Ducts are extruded rectangle or circle profiles along their run; ports are placed at the ends and joined by `IfcRelConnectsPorts`.
- Deterministic: GlobalIds of everything added are functions of mark, class and tag; same spec and same architect file give identical bytes (on one toolchain).

## How it runs
```bash
python skills/ifc-mep/scripts/build.py --spec in/spec.json --out out/      # in/base.ifc is the architect's file
```
The worker gives the job `in/spec.json` and `in/base.ifc` (read-only). Exit codes: 0 built, 2 spec rejected (including a missing or different architect file, or an unknown storey), 3 validator rejected, 4 build failed; on 3 and 4 `--out` holds no file from this build.

## Outputs
- `<mark>.ifc`, `manifest.json` (inputs, spec hash, files with sha256, measures, validation summary, toolchain).

## Validator checks
`validator.py` re-reads the produced file and the architect's file (tolerance 0.5 mm; connected ports within 1 mm):
- schema is IFC4/IFC4X3 and the file introduces no schema errors beyond those already in the architect's file.
- every architect GlobalId is still present; nothing was added beyond the specified elements, ports and systems.
- each element exists once with the right class and tag, in the right storey and system.
- duct profile, length, start point and direction are measured from the geometry and match the spec.
- ports exist as specified; the connections in the file are exactly the specified ones; connected ports coincide and run SOURCE to SINK.
- airflow balance per system (trunk against terminals, within 1 %); manifest checksums when supplied.
- Known limits: branches need an element at the junction (no tee fitting is generated); duct-to-duct clash is not checked; the schema check is ifcopenshell's, not a full buildingSMART validation.

## Worked example
`examples/ahu_to_terminal/spec.json` (architect's file: `tests/fixtures/ifc/bsi-arch-ifc4.ifc`) → `examples/ahu_to_terminal/expected_manifest.json`.

## Never
- Decide compliance. This skill makes model data only.
- Edit what the architect drew. Call the network.
