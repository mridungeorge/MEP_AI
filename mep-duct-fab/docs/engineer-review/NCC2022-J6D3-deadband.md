# Review sheet: NCC2022-J6D3-deadband

| Field | Value |
|---|---|
| Rule ID | `NCC2022-J6D3-deadband` |
| Edition | NCC2022 |
| State | ALL |
| Clause | J6D3(1)(h) |
| ABCB URL | https://ncc.abcb.gov.au/editions/ncc-2022/adopted/volume-one/j-energy-efficiency/part-j6-air-conditioning-and-ventilation |
| Rule file | `rules/ncc2022/j6/NCC2022-J6D3-deadband.yaml` |
| Status | draft |
| Tests in rule | 6 |

## Threshold cells

First pass = the rule YAML. Second pass = independent ABCB extraction by an agent that never saw the rules.
Reviewer: check the value against the ABCB table yourself; agreement of the two passes is not approval.

| Table | Row / zone key | First pass (rule) | Second pass | Match | Reviewer check |
|---|---|---|---|---|---|
| clause | `min_deadband` | 2 K | 2 °C | yes | |

## Check logic (encoded)

Inputs: `control_deadband` (K), `specialised_application_needs_smaller_deadband`, `electricity_network_substation`, `system_type`

```
control_deadband >= threshold('min_deadband')
```

## Exemptions and judgement conditions encoded

NOT_APPLICABLE when any of these is true (exempt_when):
- `electricity_network_substation == True`

NEEDS_JUDGEMENT when any of these is true (needs_judgement_when):
- `specialised_application_needs_smaller_deadband == True`

Applicability filters: `system_type` air_conditioning

## Open questions

- Does the 2 K dead band apply to heating-only or cooling-only systems? Specialised-application claims return NEEDS_JUDGEMENT.
- Encoder note: 2 degC typed from ABCB clause text, encoded as a 2 K temperature difference; engine must treat the input as a delta (pint delta_degC = K), never an absolute degC. Letter (h) confirmed by Guide. Whether a specialised application requires a smaller range is an engineer judgement.
- Second pass also recorded (not a threshold in the rule): deadband_comparison = >= (not less than) n/a

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
