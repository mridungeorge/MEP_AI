# Gate 1: confirm inputs

Gate 1 is where an engineer takes responsibility for the inputs. The rule engine refuses any value that came from a file (provenance "extracted") until a person confirms it.

## Steps

1. Upload the architect model. An IFC (preferred) or a DXF plan, or a PDF drawing, up to 50 MiB. The file type is checked from its content, not its name.
2. Read the "Ingest health" score. It says how well the model was read. A low score means: add the rooms by hand using the manual trace form.
3. Fill in what is missing. Typical items are the storey of each room, the project facts, the building part, and the system schedule (air handlers and their inputs). The system schedule can be typed in or imported from the Excel template for your NCC edition.
4. Assign each system input to a building part.
5. Click "Select all unconfirmed", then "Confirm selected". Only a designer can confirm.
6. Click "Run rules" (see the next page).

## PDF drawings

A PDF is read in the background by a vision model. What is read appears in the evidence table, marked extracted. It is never a space and never an input by itself. You can use "Add as a space" on a reading, declaring the unit (m2, ft2, mm2); the space stays extracted until you confirm it, and no unit is assumed.

## Editing withdraws confirmation

If you edit a confirmed value, its confirmation is withdrawn and you must confirm it again. The run will refuse until you do.

## What the app will refuse

- Confirming a row that has no value ("A selected row has no value").
- Confirming as anyone but a designer.
- Running rules while any row is unconfirmed. The page lists what is unconfirmed.
- Any value with a unit it cannot read: a unit mismatch is treated as NEEDS_JUDGEMENT, never guessed.
- A file whose content does not match its claimed type, or one over the size limit.
