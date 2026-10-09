# Review sheet: NCC2025-J6D9-pipe-insulation

| Field | Value |
|---|---|
| Rule ID | `NCC2025-J6D9-pipe-insulation` |
| Edition | NCC2025 |
| State | ALL |
| Clause | J6D9(1)(b), J6D9(4), J6D9(5), Table J6D9a |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2025/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2025/j6/NCC2025-J6D9-pipe-insulation.yaml` |
| Status | draft |
| Tests in rule | 17 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| J6D9a | `chilled.40_80` | 1.5 m^2*K/W | 1.5 R-Value (no unit printed) | yes | |
| J6D9a | `chilled.80_150` | 2.0 m^2*K/W | 2.0 R-Value (no unit printed) | yes | |
| J6D9a | `chilled.gt_150` | 2.0 m^2*K/W | 2.0 R-Value (no unit printed) | yes | |
| J6D9a | `chilled.le_40` | 1.0 m^2*K/W | 1.0 R-Value (no unit printed) | yes | |
| J6D9a | `edge.dn_1` | 40 mm | 40.0 mm (nominal pipe diameter) | yes | |
| J6D9a | `edge.dn_2` | 80 mm | 80.0 mm (nominal pipe diameter) | yes | |
| J6D9a | `edge.dn_3` | 150 mm | 150.0 mm (nominal pipe diameter) | yes | |
| J6D9a | `edge.t_chilled_max` | 20 degC | 20.0 °C | yes | |
| J6D9a | `edge.t_edge_low` | 2 degC | 2 °C (<= 2°C) | yes | |
| J6D9a | `edge.t_heated_min` | 30 degC | 30.0 °C | yes | |
| J6D9a | `edge.t_high` | 85 degC | 85 °C (> 85°C) | yes | |
| J6D9a | `heated.40_80` | 1.7 m^2*K/W | 1.7 R-Value (no unit printed) | yes | |
| J6D9a | `heated.80_150` | 1.7 m^2*K/W | 1.7 R-Value (no unit printed) | yes | |
| J6D9a | `heated.gt_150` | 1.7 m^2*K/W | 1.7 R-Value (no unit printed) | yes | |
| J6D9a | `heated.le_40` | 1.7 m^2*K/W | 1.7 R-Value (no unit printed) | yes | |
| J6D9a | `high_temp_heated.40_80` | 2.7 m^2*K/W | 2.7 R-Value (no unit printed) | yes | |
| J6D9a | `high_temp_heated.80_150` | 2.7 m^2*K/W | 2.7 R-Value (no unit printed) | yes | |
| J6D9a | `high_temp_heated.gt_150` | 2.7 m^2*K/W | 2.7 R-Value (no unit printed) | yes | |
| J6D9a | `high_temp_heated.le_40` | 2.7 m^2*K/W | 2.7 R-Value (no unit printed) | yes | |
| J6D9a | `low_temp_chilled.40_80` | 1.7 m^2*K/W | 1.7 R-Value (no unit printed) | yes | |
| J6D9a | `low_temp_chilled.80_150` | 2.0 m^2*K/W | 2.0 R-Value (no unit printed) | yes | |
| J6D9a | `low_temp_chilled.gt_150` | 2.7 m^2*K/W | 2.7 R-Value (no unit printed) | yes | |
| J6D9a | `low_temp_chilled.le_40` | 1.3 m^2*K/W | 1.3 R-Value (no unit printed) | yes | |

## Check logic (encoded)

Inputs: `pipe_insulation_r_value` (m^2*K/W), `fluid_temperature` (degC), `nominal_pipe_diameter` (mm), `fluid_held_at_conditioned_temperature`, `pipe_within_meps_appliance`, `is_condenser_water`, `pipe_in_last_room_downstream_of_control`, `pipe_encased_in_slab_or_panel_system`, `pipe_integral_to_compliant_plant`, `pipe_inside_ahu_or_fcu`, `pipe_penetrates_structural_member`, `system_type`, `is_electricity_substation`, `building_class`

```
(fluid_temperature <= threshold('t_edge_low') and nominal_pipe_diameter <= threshold('dn_1') and pipe_insulation_r_value >= threshold('low_temperature_chilled', 'le_40')) or (fluid_temperature <= threshold('t_edge_low') and nominal_pipe_diameter > threshold('dn_1') and nominal_pipe_diameter <= threshold('dn_2') and pipe_insulation_r_value >= threshold('low_temperature_chilled', 'gt_40_le_80')) or (fluid_temperature <= threshold('t_edge_low') and nominal_pipe_diameter > threshold('dn_2') and nominal_pipe_diameter <= threshold('dn_3') and pipe_insulation_r_value >= threshold('low_temperature_chilled', 'gt_80_le_150')) or (fluid_temperature <= threshold('t_edge_low') and nominal_pipe_diameter > threshold('dn_3') and pipe_insulation_r_value >= threshold('low_temperature_chilled', 'gt_150')) or (fluid_temperature > threshold('t_edge_low') and fluid_temperature <= threshold('t_chilled_max') and nominal_pipe_diameter <= threshold('dn_1') and pipe_insulation_r_value >= threshold('chilled', 'le_40')) or (fluid_temperature > threshold('t_edge_low') and fluid_temperature <= threshold('t_chilled_max') and nominal_pipe_diameter > threshold('dn_1') and nominal_pipe_diameter <= threshold('dn_2') and pipe_insulation_r_value >= threshold('chilled', 'gt_40_le_80')) or (fluid_temperature > threshold('t_edge_low') and fluid_temperature <= threshold('t_chilled_max') and nominal_pipe_diameter > threshold('dn_2') and nominal_pipe_diameter <= threshold('dn_3') and pipe_insulation_r_value >= threshold('chilled', 'gt_80_le_150')) or (fluid_temperature > threshold('t_edge_low') and fluid_temperature <= threshold('t_chilled_max') and nominal_pipe_diameter > threshold('dn_3') and pipe_insulation_r_value >= threshold('chilled', 'gt_150')) or (fluid_temperature > threshold('t_heated_min') and fluid_temperature <= threshold('t_high') and nominal_pipe_diameter <= threshold('dn_1') and pipe_insulation_r_value >= threshold('heated', 'le_40')) or (fluid_temperature > threshold('t_heated_min') and fluid_temperature <= threshold('t_high') and nominal_pipe_diameter > threshold('dn_1') and nominal_pipe_diameter <= threshold('dn_2') and pipe_insulation_r_value >= threshold('heated', 'gt_40_le_80')) or (fluid_temperature > threshold('t_heated_min') and fluid_temperature <= threshold('t_high') and nominal_pipe_diameter > threshold('dn_2') and nominal_pipe_diameter <= threshold('dn_3') and pipe_insulation_r_value >= threshold('heated', 'gt_80_le_150')) or (fluid_temperature > threshold('t_heated_min') and fluid_temperature <= threshold('t_high') and nominal_pipe_diameter > threshold('dn_3') and pipe_insulation_r_value >= threshold('heated', 'gt_150')) or (fluid_temperature > threshold('t_high') and nominal_pipe_diameter <= threshold('dn_1') and pipe_insulation_r_value >= threshold('high_temperature_heated', 'le_40')) or (fluid_temperature > threshold('t_high') and nominal_pipe_diameter > threshold('dn_1') and nominal_pipe_diameter <= threshold('dn_2') and pipe_insulation_r_value >= threshold('high_temperature_heated', 'gt_40_le_80')) or (fluid_temperature > threshold('t_high') and nominal_pipe_diameter > threshold('dn_2') and nominal_pipe_diameter <= threshold('dn_3') and pipe_insulation_r_value >= threshold('high_temperature_heated', 'gt_80_le_150')) or (fluid_temperature > threshold('t_high') and nominal_pipe_diameter > threshold('dn_3') and pipe_insulation_r_value >= threshold('high_temperature_heated', 'gt_150'))
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `pipe_within_meps_appliance == True`
- `is_condenser_water == True`
- `pipe_in_last_room_downstream_of_control == True`
- `pipe_encased_in_slab_or_panel_system == True`
- `pipe_integral_to_compliant_plant == True`
- `pipe_inside_ahu_or_fcu == True`
- `is_electricity_substation == True`
- `fluid_held_at_conditioned_temperature == False`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- `fluid_temperature > threshold('t_chilled_max') and fluid_temperature <= threshold('t_heated_min')`
- `pipe_penetrates_structural_member == True`

Applicability filters: `building_class` ['2', '3', '4', '5', '6', '7a', '7b', '8', '9a', '9b', '9c'], `system_type` air_conditioning

## Open questions

- Table J6D9a has no row for fluid above 20 and up to 30 degC; such pipes return NEEDS_JUDGEMENT.
- Halving of R at structural penetrations is not encoded (NEEDS_JUDGEMENT).
- Refill/relief piping and vessels/tanks (Table J6D9b) are not encoded.
- Do split/VRF refrigerant lines count as within MEPS appliances, and which temperature band applies?
- Encoder note: Encoded from ABCB NCC 2025 J6D9(1)(b) and Table J6D9a (piping only; vessels/tanks under Table J6D9b and refill/relief piping (1)(d) not encoded). Table has no band for fluid above 20 and up to 30 degC: NEEDS_JUDGEMENT. Diameter is nominal size in mm. Structural-member halving from the table note returns NEEDS_JUDGEMENT. MEPS-appliance exclusion remains in 2025 J6D9(1).
- Second pass also recorded (not a threshold in the rule): dia_band_4 = > 150 mm (nominal pipe diameter)
- Second pass also recorded (not a threshold in the rule): structural_penetration_r_factor = 0.5 multiplier (R-Value may be halved)
- Second pass also recorded (not a threshold in the rule): refill_relief_piping_matching_length = 500 mm

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
