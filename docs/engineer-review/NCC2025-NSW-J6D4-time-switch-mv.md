# Review sheet: NCC2025-NSW-J6D4-time-switch-mv

| Field | Value |
|---|---|
| Rule ID | `NCC2025-NSW-J6D4-time-switch-mv` |
| Edition | NCC2025 |
| State | NSW |
| Clause | J6D4(4), J6D4(5), NSW J6D4(6), J6D4(7) |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2025/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2025/j6/NCC2025-NSW-J6D4-time-switch-mv.yaml` |
| Status | draft |
| Tests in rule | 9 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| clause | `max_override_resume` | 2 h | 2 hours (no longer than) | yes | |
| clause | `mv_trigger` | 1000 L/s | 1000 L/s | yes | |

## Check logic (encoded)

Inputs: `mv_airflow` (L/s), `time_switch_provided`, `time_switch_programmable_times_and_days`, `time_switch_holidays_1yr_ahead`, `time_switch_override_demand_or_manual`, `time_switch_override_resume_period` (h), `serves_only_one_sou_class_3_or_9c`, `building_needs_24h_mv_occupancy`, `is_carpark_exhaust`, `required_24_7_contaminant_or_pressurisation`, `system_type`, `is_electricity_substation`, `building_class`

```
mv_airflow <= threshold('mv_trigger') or (time_switch_provided == True and time_switch_programmable_times_and_days == True and time_switch_holidays_1yr_ahead == True and time_switch_override_demand_or_manual == True and time_switch_override_resume_period <= threshold('max_override_resume'))
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `serves_only_one_sou_class_3_or_9c == True`
- `building_needs_24h_mv_occupancy == True`
- `is_carpark_exhaust == True`
- `required_24_7_contaminant_or_pressurisation == True`
- `is_electricity_substation == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- none

Applicability filters: `building_class` ['2', '3', '4', '5', '6', '7a', '7b', '8', '9a', '9b', '9c'], `system_type` mechanical_ventilation

## Open questions

- The cooling trigger is printed as kWr and the heating trigger as kW (heating); both are stored as kW.
- Encoder note: NSW state variation of the J6D4 MV time switch requirement, encoded from the NSW J6D4(6) replacement on the ABCB NCC 2025 page. Same logic as NCC2025-J6D4-time-switch-mv except the Class 4 part exemption is absent. J6D4(7) exemptions (24 h occupancy, carpark exhaust, 24/7 contaminant or pressurisation) are not varied for NSW. Engineer to confirm NSW adoption of NCC 2025 Section J.
- Second pass also recorded (not a threshold in the rule): mv_airflow_trigger_comparison = > (more than) n/a
- Second pass also recorded (not a threshold in the rule): public_holiday_programming_horizon_min = 1 year (at least 1 year ahead)
- Second pass also recorded (not a threshold in the rule): exempt_occupancy_hours = 24 hour
- Second pass also recorded (not a threshold in the rule): exempt_continuous_operation = 24/7 hours/days

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
