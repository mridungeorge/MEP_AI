# Review sheet: NCC2022-J6D3-ac-fan-vsd

| Field | Value |
|---|---|
| Rule ID | `NCC2022-J6D3-ac-fan-vsd` |
| Edition | NCC2022 |
| State | ALL |
| Clause | J6D3(1)(e) |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2022/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2022/j6/NCC2022-J6D3-ac-fan-vsd.yaml` |
| Status | draft |
| Tests in rule | 7 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| clause | `ac_system` | 1000 L/s | 1000 L/s | yes | |

## Check logic (encoded)

Inputs: `supply_airflow` (L/s), `supply_air_quantity_variable`, `variable_speed_fan`, `electricity_network_substation`, `system_type`

```
supply_airflow <= threshold('ac_system') or variable_speed_fan == True
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `electricity_network_substation == True`
- `supply_air_quantity_variable == False`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- none

Applicability filters: `system_type` air_conditioning

## Open questions

- The Guide says unitary air-conditioning is exempt; the clause text has no such exemption. Not encoded.
- Encoder note: 1000 L/s typed from ABCB clause text; trigger is strictly more than. The ABCB Guide says a unitary air-conditioning system is exempt from (1)(e), but the clause text has no such exemption, so it is not encoded. Engineer to decide. Overlaps J6D4(1)(c) for ventilation forming part of an AC system.
- Second pass also recorded (not a threshold in the rule): ac_airflow_trigger_comparison = > (more than) n/a

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
