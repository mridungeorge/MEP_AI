# Review sheet: NCC2022-J6D4-exhaust-motor-stop

| Field | Value |
|---|---|
| Rule ID | `NCC2022-J6D4-exhaust-motor-stop` |
| Edition | NCC2022 |
| State | ALL |
| Clause | J6D4(2) |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2022/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2022/j6/NCC2022-J6D4-exhaust-motor-stop.yaml` |
| Status | draft |
| Tests in rule | 7 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| clause | `exhaust_system` | 1000 L/s | 1000 L/s | yes | |

## Check logic (encoded)

Inputs: `exhaust_airflow` (L/s), `can_stop_motor_when_not_needed`, `exhaust_in_sou_class_2_3_9c`, `electricity_network_substation`, `system_type`

```
exhaust_airflow <= threshold('exhaust_system') or can_stop_motor_when_not_needed == True
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `electricity_network_substation == True`
- `exhaust_in_sou_class_2_3_9c == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- none

Applicability filters: `system_type` exhaust

## Open questions

- The Guide mentions an exemption for exhaust that balances required outdoor air; not in the clause text, not encoded.
- Encoder note: 1000 L/s typed from ABCB clause text; trigger is strictly more than. Clause exception is only exhaust in a SOU of Class 2/3/9c; Class 4 parts are not exempt from (2). The Guide mentions exhaust balancing required outdoor air, but that is not in the clause text and is not encoded. Carpark exhaust is J6D4(3), out of scope.
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
