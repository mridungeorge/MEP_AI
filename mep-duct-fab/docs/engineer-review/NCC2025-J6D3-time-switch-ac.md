# Review sheet: NCC2025-J6D3-time-switch-ac

| Field | Value |
|---|---|
| Rule ID | `NCC2025-J6D3-time-switch-ac` |
| Edition | NCC2025 |
| State | ACT, QLD, SA, TAS, VIC, WA |
| Clause | J6D3(3), J6D3(4), J6D3(5) |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2025/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2025/j6/NCC2025-J6D3-time-switch-ac.yaml` |
| Status | draft |
| Tests in rule | 10 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| clause | `ac_trigger` | 2 kW | 2 kWr | yes | |
| clause | `heater_trigger` | 1 kW | 1 kW (subscript heating) | yes | |
| clause | `max_override_resume` | 2 h | 2 hours (no longer than) | yes | |

## Check logic (encoded)

Inputs: `ac_refrigeration_capacity` (kW), `ac_heater_capacity` (kW), `time_switch_provided`, `time_switch_programmable_times_and_days`, `time_switch_holidays_1yr_ahead`, `time_switch_override_demand_or_manual`, `time_switch_override_resume_period` (h), `serves_only_one_sou_class_3_or_9c`, `serves_only_class_4_part`, `continuous_24h_use`, `system_type`, `is_electricity_substation`, `building_class`

```
(ac_refrigeration_capacity <= threshold('ac_trigger') and ac_heater_capacity <= threshold('heater_trigger')) or (time_switch_provided == True and time_switch_programmable_times_and_days == True and time_switch_holidays_1yr_ahead == True and time_switch_override_demand_or_manual == True and time_switch_override_resume_period <= threshold('max_override_resume'))
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `serves_only_one_sou_class_3_or_9c == True`
- `serves_only_class_4_part == True`
- `continuous_24h_use == True`
- `is_electricity_substation == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- none

Applicability filters: `building_class` ['2', '3', '4', '5', '6', '7a', '7b', '8', '9a', '9b', '9c'], `system_type` air_conditioning

## Open questions

- The cooling trigger is printed as kWr and the heating trigger as kW (heating); both are stored as kW.
- Encoder note: Encoded from ABCB NCC 2025 J6D3(3)-(5). 2025 exemption covers only one SOU in Class 3 or 9c (Class 2 SOU is not listed in the 2025 text), only a Class 4 part, and 24 h continuous use. Triggers are strictly greater than 2 kWr / 1 kW. Heater input is the heater used for air-conditioning. A BMS schedule meeting J6D3(4) is assumed to count as a time switch (engineer to confirm).
- Second pass also recorded (not a threshold in the rule): ac_system_capacity_trigger_comparison = > (more than) n/a
- Second pass also recorded (not a threshold in the rule): heater_capacity_trigger_comparison = > (more than) n/a
- Second pass also recorded (not a threshold in the rule): public_holiday_programming_horizon_min = 1 year (at least 1 year ahead)
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
