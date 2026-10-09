---
name: <skill-name>
description: <One sentence: what it builds and when the designer agent should use it.>
---

# <Skill name>

## When to use
- <job this skill does, e.g. "make a rectangular-to-round duct transition with a flat pattern">

## Spec card (inputs)
Defined in `spec_card.json`. Every field has a unit; required fields block the build if blank.

| Field | Unit | Required | Firm default allowed |
| --- | --- | --- | --- |
| <inlet_width> | mm | yes | no |
| <material> | enum | yes | yes |

## Conventions
- Metric only: mm, L/s, Pa, kW.
- Layers and title block from the firm profile (`templates/`), fallback to project defaults.
- Deterministic: same spec card → identical files.

## How it runs
```bash
uv run python skills/<skill-name>/scripts/build.py --spec spec.json --out out/
uv run python -c "from skills.<skill_name>.validator import validate"  # called by skills_runner
```

## Outputs
- `<files>` plus `manifest.json` (inputs, files, checksums).

## Validator checks
- <check 1, with tolerance>
- <check 2>

## Worked example
`examples/basic/spec.json` → `examples/basic/expected_manifest.json`

## Never
- Decide compliance. This skill makes geometry only.
- Call the network.
