# Review sheet: NCC2025-J6D4-exhaust-motor-stop

| Field | Value |
|---|---|
| Rule ID | `NCC2025-J6D4-exhaust-motor-stop` |
| Edition | NCC2025 |
| State | ALL |
| Clause | J6D4(3) |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2025/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2025/j6/NCC2025-J6D4-exhaust-motor-stop.yaml` |
| Status | draft |
| Tests in rule | 6 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| clause | `exhaust_trigger` | 1000 L/s | 1000 L/s | yes | |

## Check logic (encoded)

Inputs: `exhaust_airflow` (L/s), `exhaust_motor_stop_capable`, `exhaust_in_sou_class_3_or_9c`, `system_type`, `is_electricity_substation`, `building_class`

```
exhaust_airflow <= threshold('exhaust_trigger') or exhaust_motor_stop_capable == True
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `exhaust_in_sou_class_3_or_9c == True`
- `is_electricity_substation == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- none

Applicability filters: `building_class` ['2', '3', '4', '5', '6', '7a', '7b', '8', '9a', '9b', '9c'], `system_type` exhaust

## Open questions

- The Guide mentions an exemption for exhaust that balances required outdoor air; not in the clause text, not encoded.
- Encoder note: Encoded from ABCB NCC 2025 J6D4(3). Trigger is strictly greater than 1000 L/s. Only exemption is an exhaust system in a SOU of a Class 3 or 9c building. J6D4(6) and (7) exemptions (incl. carpark exhaust) do not cover (3), so carpark exhaust is in scope. "When not needed" is a design judgement; engineer confirms the stop capability input.
- Second pass also recorded (not a threshold in the rule): exhaust_airflow_trigger_comparison = > (more than) n/a

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
