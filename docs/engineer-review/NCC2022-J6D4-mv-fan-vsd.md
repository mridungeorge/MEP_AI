# Review sheet: NCC2022-J6D4-mv-fan-vsd

| Field | Value |
|---|---|
| Rule ID | `NCC2022-J6D4-mv-fan-vsd` |
| Edition | NCC2022 |
| State | ALL |
| Clause | J6D4(1)(c) |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2022/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2022/j6/NCC2022-J6D4-mv-fan-vsd.yaml` |
| Status | draft |
| Tests in rule | 9 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| clause | `mv_system` | 1000 L/s | 1000 L/s | yes | |

## Check logic (encoded)

Inputs: `mv_airflow` (L/s), `variable_speed_fan`, `f6_requires_constant_downstream_airflow`, `serves_single_sou_class_2`, `serves_class_4_part`, `electricity_network_substation`, `system_type`

```
mv_airflow <= threshold('mv_system') or variable_speed_fan == True
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `electricity_network_substation == True`
- `serves_single_sou_class_2 == True`
- `serves_class_4_part == True`
- `f6_requires_constant_downstream_airflow == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- none

Applicability filters: `system_type` mechanical_ventilation

## Open questions

- This rule and the AC variable-speed rule can both fire on ventilation that is part of an AC system.
- Encoder note: 1000 L/s typed from ABCB clause text; trigger is strictly more than. J6D4(1) general exemption covers only a single SOU in a Class 2 building (not Class 3/9c) or a Class 4 part. Letter (c) confirmed by Guide. Overlaps J6D3(1)(e) where the ventilation is part of an AC system.
- Second pass also recorded (not a threshold in the rule): mv_airflow_trigger_comparison = > (more than) n/a

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
