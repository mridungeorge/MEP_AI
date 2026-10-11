# Release notes: sprint-4b-gate (2026-10-10)

ALL RULES ARE DRAFT AND NOT ENGINEER-APPROVED. Nothing an agent produces is a compliance result. Not a compliance certification.

## What this release delivers
- The space-envelope drafting skill: rooms and plant rooms to an IFC4 model and a plan DXF, released only after an independent validator re-checks the files.
- A sandboxed skill runner and a drafting wizard (card as a form, confirmation before Build).
- Runtime agents (designer, adversarial checker, compliance risk) that may only add notes through a permission-checked tool layer; every call is logged and ledgered. Agents never produce PASS or FAIL.
- PDF pages rendered in a sandbox and read by a vision model into an evidence table only; evidence is never an input.

## What it does not do yet
- Needs ANTHROPIC_API_KEY for agents and vision. The worker sandbox is limits only at this tag. Validator blind spots are listed in docs/STATUS.md.

## Verification
- Local gate; two review rounds (4 blockers fixed). The round-2 fixes had no third review.

## Data and migrations
- Migrations 0018 to 0021.
