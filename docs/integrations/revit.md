# Revit connector: options, effort and licensing

Status: **documentation only. No connector code exists.** Nothing here has been checked with Autodesk; every licensing and cost statement below is a question to confirm
with Autodesk's current developer terms and the firm's own Revit agreement before anyone builds.

## What already works without a connector

The app reads and writes IFC. A Revit user can already:

1. Export the architectural model from Revit as IFC (IFC4 reference view is the safest choice; the exporter profile for Revit is in `apps/api/mep/ingest/profiles/revit.yaml`).
2. Upload it on the revision (spaces arrive as `extracted` and go through Gate 1) and, for services drawing, upload it again as the **architect model** (`3D preview` page,
   `POST /revisions/{id}/base-model`) so the `ifc-mep` skill can write the designed services into a copy of it.
3. Link the produced IFC (`<mark>.ifc`) back into Revit (Insert > Link IFC) to coordinate.

This path needs no Autodesk developer agreement, only the firm's own Revit licence. Its limits: it is manual, every round trip is a file, and Revit does not
treat linked IFC geometry as editable Revit families.

## Connector options

| Option | What it would do | Effort (one developer, rough) | Licensing and cost questions | Main risks |
| --- | --- | --- | --- | --- |
| A. Keep IFC round trip, add a small export checklist and a "model health" panel | Make the manual path faster and safer (already half there: ingest health, base model upload) | 2 to 4 days | None beyond the firm's Revit licence | Still manual |
| B. Revit add-in (C#, Revit API) | Ribbon button: export selected spaces and levels straight to the app (REST, user's token); optionally place mechanical families from a returned schedule | 4 to 8 weeks for export + status; 3 to 6 months if it must create native MEP elements and keep them in sync | Revit API is used inside a licensed Revit. Distribution outside the firm (App Store or customers) has Autodesk terms and possibly a signed add-in manifest requirement; confirm. A .NET build per Revit release (the API changes yearly) | Yearly API breakage; support burden; native element creation is the hard part (types, connectors, systems) |
| C. Autodesk Platform Services (cloud: Model Derivative, Data Management, Design Automation for Revit) | Read the cloud model (BIM 360 or ACC) and run headless Revit jobs without a desktop session | 6 to 12 weeks | Needs an Autodesk developer account, app credentials and metered cloud usage (cost model to confirm); client data leaves the firm's tenancy into ours, which needs a privacy review | A paid account and a secret the project does not have today; data residency (Sydney region requirement) must be checked |
| D. pyRevit or Dynamo scripts handed out as a "starter kit" | Scripts the firm runs inside Revit to export spaces and push to the API | 1 to 3 weeks | pyRevit is open source; Dynamo ships with Revit; confirm Revit licence terms for scripting | Fragile across Revit versions; not a supported product |

## Recommendation (for a person to decide; not decided here)

Do **A** now (it is cheap and removes the sharpest edges), then **B limited to export + status** if customers ask for a one-click path. Hold **C** until there is a
paying customer on ACC and the privacy review is done. Treat native element creation inside Revit as a separate product decision.

## What a connector must keep true (non-negotiable rules carry over)

- Everything it sends in is provenance `extracted`; Gate 1 confirms it. The connector never writes `engineer_confirmed` values.
- It sends units with every number (rule 8) and one NCC edition per project (rule 7).
- It holds no compliance logic and shows no PASS/FAIL it computed itself; it displays what the API returned, with the draft-rules banner.
- Credentials: a per-user token from the app's own sign-in; no shared service key inside an add-in.
- Anything it writes back into Revit is geometry and data from a **released, validated** skill output only.

## Open questions for the owner

1. Which Revit versions must be supported (each major version is a separate build for option B)?
2. Does any customer need cloud models (ACC), which would force option C?
3. Is distribution outside the firm planned (App Store, customers), which changes the Autodesk terms that apply?
4. Who owns support when Autodesk changes the API in a new release?
