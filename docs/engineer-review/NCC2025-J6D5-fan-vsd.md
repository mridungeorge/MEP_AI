# Review sheet: NCC2025-J6D5-fan-vsd

| Field | Value |
|---|---|
| Rule ID | `NCC2025-J6D5-fan-vsd` |
| Edition | NCC2025 |
| State | ALL |
| Clause | J6D5(5)(a), J6D5(6), J6D5(12), J6D5(1)(b) |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2025/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2025/j6/NCC2025-J6D5-fan-vsd.yaml` |
| Status | draft |
| Tests in rule | 14 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| clause | `small_fan_exempt` | 125 W | 125 W | yes | |
| clause | `unducted_exempt_airflow` | 1000 L/s | 1000 L/s | yes | |
| clause | `vsd_trigger` | 750 W | 750 W | yes | |

## Check logic (encoded)

Inputs: `fan_input_power` (W), `fan_variable_speed_capable`, `fan_part_of_ac_or_mv_system`, `fan_explosion_proof`, `fan_is_supply_fan_in_unitary_ac`, `fan_in_unducted_ac_system`, `unducted_system_supply_airflow` (L/s), `fan_dedicated_fire_smoke_control`, `fan_is_kitchen_exhaust`, `fan_is_process_related`, `fan_system_level_path_claimed`, `is_electricity_substation`, `building_class`

```
fan_input_power <= threshold('vsd_trigger') or fan_variable_speed_capable == True
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `fan_input_power <= threshold('small_fan_exempt')`
- `fan_explosion_proof == True`
- `fan_is_supply_fan_in_unitary_ac == True`
- `fan_in_unducted_ac_system == True and unducted_system_supply_airflow < threshold('unducted_exempt_airflow')`
- `fan_dedicated_fire_smoke_control == True`
- `fan_is_kitchen_exhaust == True`
- `is_electricity_substation == True`
- `fan_part_of_ac_or_mv_system == False`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- `fan_system_level_path_claimed == True`
- `fan_is_process_related == True`

Applicability filters: `building_class` ['2', '3', '4', '5', '6', '7a', '7b', '8', '9a', '9b', '9c']

## Open questions

- Whole-system alternative path (J6D5(1)) returns NEEDS_JUDGEMENT; confirm that is the right treatment.
- Below the 125 W small-fan limit the rule is NOT_APPLICABLE, but below 750 W it is PASS: confirm intended semantics.
- Encoder note: Encoded from ABCB NCC 2025 J6D5(5)(a), (6)(a)-(c), (12)(a)-(d). Trigger is strictly greater than 750 W. Only (5)(a) is encoded; (5)(b) and (c) (reduced flow, variable volume/pressure) need separate rules. J6D5(1)(b) whole-system path gives NEEDS_JUDGEMENT, not FAIL. Unducted exemption airflow is the AC system supply air capacity, strictly below 1000 L/s.
- Second pass also recorded (not a threshold in the rule): fan_input_power_trigger_comparison = > (greater than) n/a
- Second pass also recorded (not a threshold in the rule): fan_input_power_measurement_point = peak efficiency point and full speed, incl. integrated motor control equipment n/a
- Second pass also recorded (not a threshold in the rule): exempt_fan_input_power_comparison = <= (less than or equal to) n/a
- Second pass also recorded (not a threshold in the rule): exempt_unducted_supply_air_comparison = < (less than) n/a

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
