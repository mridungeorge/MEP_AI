# Review sheet: NCC2022-J6D9-pipe-insulation

| Field | Value |
|---|---|
| Rule ID | `NCC2022-J6D9-pipe-insulation` |
| Edition | NCC2022 |
| State | ALL |
| Clause | J6D9(1)(b), Table J6D9a |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2022/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2022/j6/NCC2022-J6D9-pipe-insulation.yaml` |
| Status | draft |
| Tests in rule | 14 |

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

Inputs: `fluid_service`, `fluid_temp` (degC), `pipe_dn` (mm), `pipe_r_value` (m^2*K/W), `pipe_exemption`, `refill_or_pressure_relief_pipe`, `penetrates_structural_member`, `electricity_network_substation`, `system_type`

```
(fluid_temp <= threshold('t_edge_low') and ((pipe_dn <= threshold('dn_1') and pipe_r_value >= threshold('low_temp_chilled', 'dn_le_40')) or (pipe_dn > threshold('dn_1') and pipe_dn <= threshold('dn_2') and pipe_r_value >= threshold('low_temp_chilled', 'dn_gt_40_le_80')) or (pipe_dn > threshold('dn_2') and pipe_dn <= threshold('dn_3') and pipe_r_value >= threshold('low_temp_chilled', 'dn_gt_80_le_150')) or (pipe_dn > threshold('dn_3') and pipe_r_value >= threshold('low_temp_chilled', 'dn_gt_150')))) or (fluid_temp > threshold('t_edge_low') and fluid_temp <= threshold('t_chilled_max') and ((pipe_dn <= threshold('dn_1') and pipe_r_value >= threshold('chilled', 'dn_le_40')) or (pipe_dn > threshold('dn_1') and pipe_dn <= threshold('dn_2') and pipe_r_value >= threshold('chilled', 'dn_gt_40_le_80')) or (pipe_dn > threshold('dn_2') and pipe_dn <= threshold('dn_3') and pipe_r_value >= threshold('chilled', 'dn_gt_80_le_150')) or (pipe_dn > threshold('dn_3') and pipe_r_value >= threshold('chilled', 'dn_gt_150')))) or (fluid_temp > threshold('t_heated_min') and fluid_temp <= threshold('t_high') and ((pipe_dn <= threshold('dn_1') and pipe_r_value >= threshold('heated', 'dn_le_40')) or (pipe_dn > threshold('dn_1') and pipe_dn <= threshold('dn_2') and pipe_r_value >= threshold('heated', 'dn_gt_40_le_80')) or (pipe_dn > threshold('dn_2') and pipe_dn <= threshold('dn_3') and pipe_r_value >= threshold('heated', 'dn_gt_80_le_150')) or (pipe_dn > threshold('dn_3') and pipe_r_value >= threshold('heated', 'dn_gt_150')))) or (fluid_temp > threshold('t_high') and ((pipe_dn <= threshold('dn_1') and pipe_r_value >= threshold('high_temp_heated', 'dn_le_40')) or (pipe_dn > threshold('dn_1') and pipe_dn <= threshold('dn_2') and pipe_r_value >= threshold('high_temp_heated', 'dn_gt_40_le_80')) or (pipe_dn > threshold('dn_2') and pipe_dn <= threshold('dn_3') and pipe_r_value >= threshold('high_temp_heated', 'dn_gt_80_le_150')) or (pipe_dn > threshold('dn_3') and pipe_r_value >= threshold('high_temp_heated', 'dn_gt_150'))))
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `electricity_network_substation == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- `fluid_temp > threshold('t_chilled_max') and fluid_temp <= threshold('t_heated_min')`
- `pipe_exemption != 'none'`
- `refill_or_pressure_relief_pipe == True`
- `penetrates_structural_member == True`

Applicability filters: `system_type` air_conditioning, `fluid_service` {'in': ['heating', 'cooling']}

## Open questions

- Table J6D9a has no row for fluid above 20 and up to 30 degC; such pipes return NEEDS_JUDGEMENT.
- Halving of R at structural penetrations is not encoded (NEEDS_JUDGEMENT).
- Refill/relief piping and vessels/tanks (Table J6D9b) are not encoded.
- Do split/VRF refrigerant lines count as within MEPS appliances, and which temperature band applies?
- Encoder note: Table J6D9a typed from ABCB text. R-Value is the insulation material R-Value. Not encoded here: halved R-Value at structural penetrations (table note), refill/relief piping (1)(d), vessels/tanks (1)(c), AS/NZS 4859.1 (1)(a). Fluid at >20 to <=30 degC has no table row and returns NOT_APPLICABLE; engineer to confirm.
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
