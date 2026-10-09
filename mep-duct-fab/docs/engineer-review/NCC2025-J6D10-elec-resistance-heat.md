# Review sheet: NCC2025-J6D10-elec-resistance-heat

| Field | Value |
|---|---|
| Rule ID | `NCC2025-J6D10-elec-resistance-heat` |
| Edition | NCC2025 |
| State | ALL |
| Clause | J6D10(1)(e)(i), J6D10(1)(e)(ii), Table J6D10 |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2025/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2025/j6/NCC2025-J6D10-elec-resistance-heat.yaml` |
| Status | draft |
| Tests in rule | 13 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| J6D10 | `annual_energy` | 15 kW*h/m^2 | 15 kWh/m2 | yes | |
| J6D10 | `area_split` | 500 m^2 | 500 m2 | yes | |
| J6D10 | `cz1.gt_500` | 40 W/m^2 | 40 W/m2 | yes | |
| J6D10 | `cz1.le_500` | 40 W/m^2 | 40 W/m2 | yes | |
| J6D10 | `cz2.gt_500` | 40 W/m^2 | 40 W/m2 | yes | |
| J6D10 | `cz2.le_500` | 40 W/m^2 | 40 W/m2 | yes | |
| J6D10 | `cz3.gt_500` | 40 W/m^2 | 40 W/m2 | yes | |
| J6D10 | `cz3.le_500` | 50 W/m^2 | 50 W/m2 | yes | |
| J6D10 | `cz4.gt_500` | 50 W/m^2 | 50 W/m2 | yes | |
| J6D10 | `cz4.le_500` | 60 W/m^2 | 60 W/m2 | yes | |
| J6D10 | `cz5.gt_500` | 45 W/m^2 | 45 W/m2 | yes | |
| J6D10 | `cz5.le_500` | 55 W/m^2 | 55 W/m2 | yes | |
| J6D10 | `cz6.gt_500` | 55 W/m^2 | 55 W/m2 | yes | |
| J6D10 | `cz6.le_500` | 65 W/m^2 | 65 W/m2 | yes | |
| J6D10 | `cz7.gt_500` | 60 W/m^2 | 60 W/m2 | yes | |
| J6D10 | `cz7.le_500` | 70 W/m^2 | 70 W/m2 | yes | |
| J6D10 | `cz8.gt_500` | TODO_FROM_SOURCE W/m^2 | UNREADABLE W/m2 | unresolved | |
| J6D10 | `cz8.le_500` | TODO_FROM_SOURCE W/m^2 | UNREADABLE W/m2 | unresolved | |

## Check logic (encoded)

Inputs: `electric_heating_capacity` (W), `conditioned_floor_area` (m^2), `climate_zone`, `heater_type`, `annual_energy_path_claimed`, `annual_heating_energy_intensity` (kW*h/m^2), `in_duct_heater_reheat_path_claimed`, `is_bathroom_heater`, `is_electricity_substation`, `building_class`

```
(conditioned_floor_area <= threshold('area_split') and electric_heating_capacity <= threshold('allowed_capacity_le_500')) or (conditioned_floor_area > threshold('area_split') and electric_heating_capacity <= threshold('allowed_capacity_gt_500')) or (climate_zone <= 5 and annual_energy_path_claimed == True and annual_heating_energy_intensity <= threshold('annual_energy'))
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `is_electricity_substation == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- `in_duct_heater_reheat_path_claimed == True`
- `is_bathroom_heater == True`

Applicability filters: `building_class` ['2', '3', '4', '5', '6', '7a', '7b', '8', '9a', '9b', '9c'], `heater_type` electric_resistance

## Open questions

- Table J6D10 has columns for climate zones 3 to 7 only. What applies in climate zone 8 (2025 clause says 3 to 8)?
- Is the floor area per heater or per system served?
- Annual heating energy intensity is a required engineer-confirmed input for the alternative route.
- Encoder note: Encoded from ABCB NCC 2025 J6D10(1)(e)(i)-(ii). CZ8: clause cites Table J6D10 for CZ3-8 but the table has CZ3-7 only; CZ8 is TODO_FROM_SOURCE and returns NEEDS_JUDGEMENT. Area band: <= 500 m2 vs > 500 m2. Floor area is that of the conditioned space served (engineer to confirm per heater or per system). Mixed heater types under (1)(f) not encoded.
- Second pass also recorded (not a threshold in the rule): table_climate_zones_per_clause = 3 to 8 climate zone
- Second pass also recorded (not a threshold in the rule): heating_capacity_comparison = <= (not more than) n/a
- Second pass also recorded (not a threshold in the rule): alt_annual_energy_climate_zones = 1, 2, 3, 4, 5 climate zone
- Second pass also recorded (not a threshold in the rule): alt_in_duct_reheat_limit_ref = J6D3(1)(c)(iv): 7.5 °C
- Second pass also recorded (not a threshold in the rule): bathroom_heater_max_capacity = 1.2 kW
- Second pass also recorded (not a threshold in the rule): bathroom_heater_max_run_on = 15 minutes
- TODO_FROM_SOURCE in the rule: `cz8.gt_500`, `cz8.le_500` (cannot be approved until typed from ABCB)

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
