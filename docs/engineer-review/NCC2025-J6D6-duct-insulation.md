# Review sheet: NCC2025-J6D6-duct-insulation

| Field | Value |
|---|---|
| Rule ID | `NCC2025-J6D6-duct-insulation` |
| Edition | NCC2025 |
| State | ALL |
| Clause | J6D6(1)(b), J6D6(3), J6D6(4), Table J6D6 |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2025/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2025/j6/NCC2025-J6D6-duct-insulation.yaml` |
| Status | draft |
| Tests in rule | 15 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| J6D6 | `conditioned_space.cz1` | 1.2 m^2*K/W | 1.2 R-Value (no unit printed) | yes | |
| J6D6 | `conditioned_space.cz2` | 1.2 m^2*K/W | 1.2 R-Value (no unit printed) | yes | |
| J6D6 | `conditioned_space.cz3` | 1.2 m^2*K/W | 1.2 R-Value (no unit printed) | yes | |
| J6D6 | `conditioned_space.cz4` | 1.2 m^2*K/W | 1.2 R-Value (no unit printed) | yes | |
| J6D6 | `conditioned_space.cz5` | 1.2 m^2*K/W | 1.2 R-Value (no unit printed) | yes | |
| J6D6 | `conditioned_space.cz6` | 1.2 m^2*K/W | 1.2 R-Value (no unit printed) | yes | |
| J6D6 | `conditioned_space.cz7` | 1.2 m^2*K/W | 1.2 R-Value (no unit printed) | yes | |
| J6D6 | `conditioned_space.cz8` | 2.0 m^2*K/W | 2.0 R-Value (no unit printed) | yes | |
| J6D6 | `direct_sunlight.cz1` | 3.0 m^2*K/W | 3.0 R-Value (no unit printed) | yes | |
| J6D6 | `direct_sunlight.cz2` | 3.0 m^2*K/W | 3.0 R-Value (no unit printed) | yes | |
| J6D6 | `direct_sunlight.cz3` | 3.0 m^2*K/W | 3.0 R-Value (no unit printed) | yes | |
| J6D6 | `direct_sunlight.cz4` | 3.0 m^2*K/W | 3.0 R-Value (no unit printed) | yes | |
| J6D6 | `direct_sunlight.cz5` | 3.0 m^2*K/W | 3.0 R-Value (no unit printed) | yes | |
| J6D6 | `direct_sunlight.cz6` | 3.0 m^2*K/W | 3.0 R-Value (no unit printed) | yes | |
| J6D6 | `direct_sunlight.cz7` | 3.0 m^2*K/W | 3.0 R-Value (no unit printed) | yes | |
| J6D6 | `direct_sunlight.cz8` | 3.0 m^2*K/W | 3.0 R-Value (no unit printed) | yes | |
| J6D6 | `flexible` | 1.0 m^2*K/W | 1.0 R-Value (no unit printed) | yes | |
| J6D6 | `other.cz1` | 2.0 m^2*K/W | 2.0 R-Value (no unit printed) | yes | |
| J6D6 | `other.cz2` | 2.0 m^2*K/W | 2.0 R-Value (no unit printed) | yes | |
| J6D6 | `other.cz3` | 2.0 m^2*K/W | 2.0 R-Value (no unit printed) | yes | |
| J6D6 | `other.cz4` | 2.0 m^2*K/W | 2.0 R-Value (no unit printed) | yes | |
| J6D6 | `other.cz5` | 2.0 m^2*K/W | 2.0 R-Value (no unit printed) | yes | |
| J6D6 | `other.cz6` | 2.0 m^2*K/W | 2.0 R-Value (no unit printed) | yes | |
| J6D6 | `other.cz7` | 2.0 m^2*K/W | 2.0 R-Value (no unit printed) | yes | |
| J6D6 | `other.cz8` | 3.0 m^2*K/W | 3.0 R-Value (no unit printed) | yes | |

## Check logic (encoded)

Inputs: `duct_insulation_r_value` (m^2*K/W), `duct_type`, `duct_location`, `connecting_duct_insulation_r_value` (m^2*K/W), `climate_zone`, `duct_in_only_or_last_room_served`, `duct_is_conditioned_space_interface_fitting`, `duct_is_return_air_in_conditioned_space`, `duct_is_outdoor_or_exhaust_air`, `duct_is_in_situ_ahu_floor`, `duct_is_flexible_fan_connection`, `duct_is_active_component`, `system_type`, `is_electricity_substation`, `building_class`

```
(duct_type == 'flexible' and duct_insulation_r_value >= threshold('flexible')) or (duct_type == 'cushion_box' and duct_insulation_r_value >= connecting_duct_insulation_r_value) or (duct_type == 'rigid' and duct_insulation_r_value >= threshold('rigid', duct_location, climate_zone))
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `duct_in_only_or_last_room_served == True`
- `duct_is_conditioned_space_interface_fitting == True`
- `duct_is_return_air_in_conditioned_space == True`
- `duct_is_outdoor_or_exhaust_air == True`
- `duct_is_in_situ_ahu_floor == True`
- `duct_is_flexible_fan_connection == True`
- `duct_is_active_component == True`
- `is_electricity_substation == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- none

Applicability filters: `building_class` ['2', '3', '4', '5', '6', '7a', '7b', '8', '9a', '9b', '9c'], `system_type` air_conditioning

## Open questions

- R-values are printed without a unit; m^2.K/W is assumed.
- Cushion box: matched to the installed (not required) R-value of the connecting duct. Confirm.
- Flexible duct is R1.0 in every location (assumed). Confirm.
- Encoder note: Encoded from ABCB NCC 2025 J6D6. 2025 (3) has no exemption for MEPS-covered equipment. Assumed: flexible duct uses 1.0 regardless of location; cushion box compares with the installed R of the connecting duct; rigid uses Table J6D6 by location and CZ. Engineer to confirm both readings. Evaluate per duct segment. (2) vapour barrier and weather protection not encoded.
- Second pass also recorded (not a threshold in the rule): r_min_cushion_box = equal to connecting ductwork R-Value
- Second pass also recorded (not a threshold in the rule): r_comparison = >= (greater than or equal to) n/a
- Second pass also recorded (not a threshold in the rule): vapour_barrier_membrane_min_overlap = 50 mm

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
