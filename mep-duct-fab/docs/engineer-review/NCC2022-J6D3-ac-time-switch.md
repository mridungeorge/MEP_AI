# Review sheet: NCC2022-J6D3-ac-time-switch

| Field | Value |
|---|---|
| Rule ID | `NCC2022-J6D3-ac-time-switch` |
| Edition | NCC2022 |
| State | ALL |
| Clause | J6D3(3)(a)-(c) |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2022/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2022/j6/NCC2022-J6D3-ac-time-switch.yaml` |
| Status | draft |
| Tests in rule | 11 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| clause | `ac_system` | 2 kW | 2 kWr | yes | |
| clause | `heater` | 1 kW | 1 kW (subscript heating) | yes | |

## Check logic (encoded)

Inputs: `ac_refrigeration_capacity` (kW), `heater_heating_capacity` (kW), `time_switch_provided`, `time_switch_variable_times_and_days`, `serves_single_sou_class_2_3_9c`, `serves_class_4_part`, `needs_24h_continuous_use`, `electricity_network_substation`, `system_type`

```
(ac_refrigeration_capacity <= threshold('ac_system') and heater_heating_capacity <= threshold('heater')) or (time_switch_provided == True and time_switch_variable_times_and_days == True)
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `electricity_network_substation == True`
- `serves_single_sou_class_2_3_9c == True`
- `serves_class_4_part == True`
- `needs_24h_continuous_use == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- none

Applicability filters: `system_type` air_conditioning

## Open questions

- The cooling trigger is printed as kWr and the heating trigger as kW (heating); both are stored as kW.
- Encoder note: 2 kWr and 1 kW heating typed from ABCB clause text; trigger is strictly more than. ac_refrigeration_capacity is kWr (pint has no kWr unit, so kW is used). Exemption letters (c)(i)/(ii) confirmed by Guide reference to J6D3(3)(c). Heater must be one used for air-conditioning.
- Second pass also recorded (not a threshold in the rule): ac_system_capacity_trigger_comparison = > (more than) n/a
- Second pass also recorded (not a threshold in the rule): heater_capacity_trigger_comparison = > (more than) n/a
- Second pass also recorded (not a threshold in the rule): exempt_continuous_use_hours = 24 hour

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
