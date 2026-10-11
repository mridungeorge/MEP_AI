# Services, clashes and quantities

Open Services on a revision. None of the checks here produce PASS or FAIL: they report CLASH, CLEAR or NO DATA.

## Proposed ducts

Add ducts with a tag, size and airflow, or remove them. Lengths are converted to millimetres with units.

## Ceiling void check

For each space the app compares the void with the deepest duct plus insulation on both faces plus the firm's clearance. The clearance is set by your firm administrator. Only a confirmed void can be CLEAR; an unconfirmed or missing void gives NO DATA with the reason.

## Quantities

Download CSV or XLSX: ducts by size with count, length and surface area, insulation by thickness, fittings, and terminals per space. Spreadsheet cells are neutralised so a formula in a name cannot run when you open the file.

## Clash-lite

1. Choose the discipline (electrical, hydraulic or fire).
2. Upload that discipline's IFC.
3. Read the list of warnings, each with the duct, the other element and the gap in millimetres.
4. "Export BCF 2.1" produces a BCF zip for your coordination tool.

Clash-lite uses axis-aligned boxes, so it is a screen, not a full clash detection. Ducts without coordinates are counted and skipped. The IFC is read in an isolated process with time and memory limits.

## What the app will refuse

- Treating a clash result as compliance: they are warnings only.
- Too many models, elements or clashes beyond the fixed caps, or a model that exceeds the read limits.
- A CLEAR result from an unconfirmed ceiling void.
- Uploading anything that is not an IFC as a clash model.
