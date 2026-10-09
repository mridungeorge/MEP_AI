# Review sheet: NCC2022-J6D7-duct-sealing

| Field | Value |
|---|---|
| Rule ID | `NCC2022-J6D7-duct-sealing` |
| Edition | NCC2022 |
| State | ALL |
| Clause | J6D7 |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2022/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2022/j6/NCC2022-J6D7-duct-sealing.yaml` |
| Status | draft |
| Tests in rule | 6 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| clause | `ac_system` | 3000 L/s | 3000 L/s | yes | |

## Check logic (encoded)

Inputs: `ac_system_airflow` (L/s), `duct_sealed_to_as4254`, `duct_in_only_or_last_room`, `electricity_network_substation`, `system_type`

```
ac_system_airflow < threshold('ac_system') or duct_sealed_to_as4254 == True
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `electricity_network_substation == True`
- `duct_in_only_or_last_room == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- none

Applicability filters: `system_type` air_conditioning

## Open questions

- The sealing class itself comes from AS 4254 (licensed); only an engineer-confirmed yes/no is checked.
- Encoder note: 3000 L/s typed from ABCB clause text; trigger is 3000 L/s or greater (system capacity). Compliance with AS 4254.1/.2 sealing cannot be checked by the engine; it is an engineer-confirmed bool. Unconfirmed sealing returns NEEDS_JUDGEMENT. Applies to air-conditioning ductwork only.
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
