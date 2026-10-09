# Review sheet: NCC2025-J6D7-duct-sealing

| Field | Value |
|---|---|
| Rule ID | `NCC2025-J6D7-duct-sealing` |
| Edition | NCC2025 |
| State | ALL |
| Clause | J6D7 |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2025/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2025/j6/NCC2025-J6D7-duct-sealing.yaml` |
| Status | draft |
| Tests in rule | 7 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| clause | `sealing_trigger` | 3000 L/s | 3000 L/s | yes | |

## Check logic (encoded)

Inputs: `ac_system_airflow` (L/s), `duct_sealed_to_as4254_for_static_pressure`, `duct_in_only_or_last_room_served`, `system_type`, `is_electricity_substation`, `building_class`

```
ac_system_airflow < threshold('sealing_trigger') or duct_sealed_to_as4254_for_static_pressure == True
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `duct_in_only_or_last_room_served == True`
- `is_electricity_substation == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- none

Applicability filters: `building_class` ['2', '3', '4', '5', '6', '7a', '7b', '8', '9a', '9b', '9c'], `system_type` air_conditioning

## Open questions

- The sealing class itself comes from AS 4254 (licensed); only an engineer-confirmed yes/no is checked.
- Encoder note: Encoded from ABCB NCC 2025 J6D7. Trigger is >= 3000 L/s. "Capacity" in L/s is read as the system design supply airflow (engineer to confirm). The AS 4254.1/.2 sealing class for the static pressure is not encoded (licensed AS text); the engineer confirms the boolean against the licensed standard. Evaluate per duct section outside the only or last room served.
- Second pass also recorded (not a threshold in the rule): ac_system_capacity_trigger_comparison = >= (3000 L/s or greater) n/a

## Sub-clause letters

The ABCB HTML drops sub-clause letters. They were inferred from item order and cross-references.
Confirm every letter in the clause field against the published NCC.

## First-pass value check (optional, does NOT approve the rule)

Someone other than the approving engineer may type-check the threshold cells against ABCB and record it in the
rule file as `checked_by` and `checked_on`. This changes nothing about status: the rule stays `draft`.

| Field | Entry |
|---|---|
| Checked by (rule file: checked_by) |  |
| Checked on (rule file: checked_on) |  |

## Reviewer sign-off (blank: to be completed by the engineer)

| Field | Entry |
|---|---|
| Reviewer name | |
| Registration no. | |
| Date | |
| Decision (approve / approve with changes / reject) | |
| Changes required | |

Only the engineer sets `status: approved`, together with `reviewed_by`, `reviewed_on` and `reviewer_registration_no`, in the rule file. Approval needs all three.
