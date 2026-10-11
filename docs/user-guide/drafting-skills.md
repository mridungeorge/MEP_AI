# Drafting skills

Open Drafting on a revision. Pick a skill, fill in its spec card, and the app builds files in a sandbox. Skills produce geometry and data only; they never decide compliance.

## The skills

- space-envelope: rooms and plant rooms of one storey, as an IFC4 model of spaces plus a plan DXF. Start a revision before the architect's model arrives.
- space-envelope-stack: the same for several stacked storeys, one IFC with a storey per level and a plan DXF per storey. Storeys must stack on their floor-to-floor heights.
- duct-fab: a rectangular-to-round transition, rectangular reducer or rectangular offset as a 3D STEP solid plus a flat-pattern DXF. Sheet thickness, seam and connection allowances are engineer inputs: there are no defaults.
- hvac-dxf: a duct layout (true width) or schematic (centreline) as a DXF with firm layers, terminal symbols, title block and a size and airflow tag on every duct.
- ifc-mep: ducts, terminals and air-handling equipment with ports and connections written into a copy of the architect's IFC. The architect's elements are never edited.

## Steps

1. Choose the skill. Optionally describe it in a sentence and click "Read it": the app shows what it understood, what is still needed and what text it did not read. Click "Put this into the card".
2. Complete the card as a form. "What is missing?" lists required fields.
3. Review the card, tick the confirmation, and click "Build".
4. The build runs in an isolated container, then a separate independent re-check validates the files against the spec. Only if both pass are the files released for download. Use "Use as this revision's model" to start from a released IFC or DXF.

A card drafted by an assistant must be reviewed and confirmed by a designer, on the exact version of the card, before it can be built.

## Firm templates

Your administrator can upload a title block (DXF) and a layer standard (JSON). When present, a second file `<name>.firm.dxf` is produced with the firm's title block and layers, only if every original entity is kept. The validated original is always kept.

## 3D preview

Open the 3D preview on a revision. Upload the architect's IFC, tick the model parts to show, click "Show", and orbit the view. It reads the IFC in your browser. Models above 3,000,000 triangles are refused. It has had no automated browser test: check it by eye.

## Sizing

Duct sizing is on the Services page (equal friction). Friction rate, roughness, size increment, minimum size and aspect ratios are firm-changeable design-practice defaults, not from a standard. Velocity limits have no default: the page says "NO LIMIT SET" until your firm sets one. Recommended sizes feed hvac-dxf and ifc-mep as a schedule.

## What the app will refuse

- A build with a required field missing, unit-less or in the wrong unit.
- A build of an unconfirmed assistant card, or a card that changed after confirmation.
- Releasing any file whose independent re-check failed.
- Overlapping rooms, self-crossing outlines, storeys that do not stack, or an ifc-mep base file that does not match the checksum in the card.
- Building when no drafting worker is running (the page says so; the rest of the app works).
