# Review sheet: NCC2022-J6D6-duct-insulation

| Field | Value |
|---|---|
| Rule ID | `NCC2022-J6D6-duct-insulation` |
| Edition | NCC2022 |
| State | ALL |
| Clause | J6D6(1)(b), Table J6D6 |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2022/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2022/j6/NCC2022-J6D6-duct-insulation.yaml` |
| Status | draft |
| Tests in rule | 11 |

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

Inputs: `duct_type`, `duct_location`, `climate_zone`, `duct_insulation_r_value` (m^2*K/W), `duct_exemption`, `electricity_network_substation`, `system_type`

```
(duct_type == 'flexible' and duct_insulation_r_value >= threshold('flexible')) or (duct_type == 'rigid' and duct_insulation_r_value >= threshold('rigid', duct_location, climate_zone))
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `electricity_network_substation == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- `duct_type != 'rigid' and duct_type != 'flexible'`
- `duct_exemption != 'none'`

Applicability filters: `system_type` air_conditioning

## Open questions

- R-values are printed without a unit; m^2.K/W is assumed.
- Cushion box: matched to the installed (not required) R-value of the connecting duct. Confirm.
- Flexible duct is R1.0 in every location (assumed). Confirm.
- Encoder note: Table J6D6 and flexible R1.0 typed from ABCB text. R-Value is the added insulation material R-Value. Flexible ductwork takes R1.0 regardless of location per clause structure. Cushion boxes (match connecting ductwork) and J6D6(1)(a) AS/NZS 4859.1 conformance and J6D6(2) installation are not encoded here.
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
