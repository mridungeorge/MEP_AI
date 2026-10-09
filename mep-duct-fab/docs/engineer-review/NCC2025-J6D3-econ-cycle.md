# Review sheet: NCC2025-J6D3-econ-cycle

| Field | Value |
|---|---|
| Rule ID | `NCC2025-J6D3-econ-cycle` |
| Edition | NCC2025 |
| State | ALL |
| Clause | J6D3(1)(d), Table J6D3 |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2025/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2025/j6/NCC2025-J6D3-econ-cycle.yaml` |
| Status | draft |
| Tests in rule | 11 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| J6D3 | `cz2` | 9000 L/s | 9000 L/s | yes | |
| J6D3 | `cz3` | 7500 L/s | 7500 L/s | yes | |
| J6D3 | `cz4` | 3500 L/s | 3500 L/s | yes | |
| J6D3 | `cz5` | 3000 L/s | 3000 L/s | yes | |
| J6D3 | `cz6` | 1200 L/s | 1200 L/s | yes | |
| J6D3 | `cz7` | 2500 L/s | 2500 L/s | yes | |
| J6D3 | `cz8` | 2000 L/s | 2000 L/s | yes | |

## Check logic (encoded)

Inputs: `max_airside_component_airflow` (L/s), `climate_zone`, `economy_cycle`, `system_type`, `provides_required_mech_ventilation`, `dehumidification_control_needed`, `is_electricity_substation`, `building_class`

```
max_airside_component_airflow < threshold('by_climate_zone', climate_zone) or economy_cycle == True
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `is_electricity_substation == True`
- `provides_required_mech_ventilation == False`
- `dehumidification_control_needed == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- none

Applicability filters: `building_class` ['2', '3', '4', '5', '6', '7a', '7b', '8', '9a', '9b', '9c'], `climate_zone` {'not_in': [1]}, `system_type` air_conditioning

## Open questions

- Is 'any airside component' the single largest component's airflow (assumed) or each air-conditioner?
- The clause text gives only two exemptions (climate zone 1, dehumidification). The Guide text is not encoded.
- Encoder note: Encoded from ABCB NCC 2025 J6D3. Letter (d) verified by item order (14 items a-n; (c) is the multi-zone item, confirmed by the explanatory note citing (1)(c)(iii)). Trigger is >= table value. max_airside_component_airflow = the largest total airflow of any single airside component (interpretation; engineer to confirm). Table J6D3 has no CZ1 row by design.
- Second pass also recorded (not a threshold in the rule): trigger_comparison = >= (greater than or equal to table value) n/a
- Second pass also recorded (not a threshold in the rule): exempt_climate_zone = 1 climate zone

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
