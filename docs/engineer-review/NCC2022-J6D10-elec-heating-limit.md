# Review sheet: NCC2022-J6D10-elec-heating-limit

| Field | Value |
|---|---|
| Rule ID | `NCC2022-J6D10-elec-heating-limit` |
| Edition | NCC2022 |
| State | ACT, QLD, SA, TAS, VIC, WA |
| Clause | J6D10(1), Table J6D10 |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2022/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2022/j6/NCC2022-J6D10-elec-heating-limit.yaml` |
| Status | draft |
| Tests in rule | 14 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| J6D10 | `annual_energy` | 15 kW*h/m^2 | 15 kWh/m2 | yes | |
| J6D10 | `area_split` | 500 m^2 | 500 m2 | yes | |
| J6D10 | `cz1` | 10 W/m^2 | 10 W/m2 | yes | |
| J6D10 | `cz2` | 40 W/m^2 | 40 W/m2 | yes | |
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
| J6D10 | `cz8` | - | UNREADABLE W/m2 | unresolved | |

## Check logic (encoded)

Inputs: `building_class`, `serves_class_4_part`, `electricity_network_substation`, `is_j6d10_2_bathroom_heater`, `climate_zone`, `electric_heating_capacity` (W), `conditioned_floor_area` (m^2), `reticulated_gas_at_boundary`, `annual_heating_energy_intensity` (kW*h/m^2), `in_duct_heater_complies_reheat_limit`, `system_type`

```
(climate_zone == 1 and electric_heating_capacity <= threshold('allowed_capacity_cz1')) or (climate_zone == 2 and electric_heating_capacity <= threshold('allowed_capacity_cz2')) or (climate_zone >= 3 and climate_zone <= 7 and reticulated_gas_at_boundary == False and ((conditioned_floor_area <= threshold('area_split') and electric_heating_capacity <= threshold('allowed_capacity_le_500')) or (conditioned_floor_area > threshold('area_split') and electric_heating_capacity <= threshold('allowed_capacity_gt_500')))) or (climate_zone <= 5 and annual_heating_energy_intensity <= threshold('annual_energy')) or in_duct_heater_complies_reheat_limit == True
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `electricity_network_substation == True`
- `serves_class_4_part == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- `is_j6d10_2_bathroom_heater == True`

Applicability filters: `building_class` ['3', '5', '6', '7a', '7b', '8', '9a', '9b', '9c'], `system_type` air_conditioning_heating

## Open questions

- Table J6D10 has columns for climate zones 3 to 7 only. What applies in climate zone 8 (2025 clause says 3 to 8)?
- Is the floor area per heater or per system served?
- Annual heating energy intensity is a required engineer-confirmed input for the alternative route.
- Encoder note: Values typed from ABCB J6D10(1)(e) and Table J6D10. Table J6D10 has CZ3-7 columns only, applies only without reticulated gas at the boundary. (e)(ii) is CZ1-5 only. Check relies on short-circuit and/or. TAS: Class 2/4 Section J is BCA 2019 (excluded here anyway). NT has its own Part J6 variation, not reviewed.
- Second pass also recorded (not a threshold in the rule): table_applies_condition = reticulated gas not available at allotment boundary n/a
- Second pass also recorded (not a threshold in the rule): heating_capacity_comparison = <= (not more than) n/a
- Second pass also recorded (not a threshold in the rule): alt_annual_energy_climate_zones = 1, 2, 3, 4, 5 climate zone
- Second pass also recorded (not a threshold in the rule): alt_in_duct_heater_reheat_limit_ref = J6D3(1)(b)(iii): 7.5 K
- Second pass also recorded (not a threshold in the rule): bathroom_heater_max_capacity = 1.2 kW

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
