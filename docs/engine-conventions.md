# Rule conventions the engine must implement (Sprint 1, tests first)

The 24 draft packs already follow these; `scripts/validate_rules.py` enforces the static parts and
`tests/rules/rule_runner.py` (test support only, replaced by the engine) executes every rule's own
test cases against them.

## 1. Units: every threshold is a pint quantity

- Every numeric value in `threshold.values` is a leaf `{value, unit}`, a group `{unit, values}`
  whose numbers share that unit (nested maps keyed by climate zone, band, location inherit it),
  or a typed formula `{formula, unit}`. No `per value`, no bare numbers. Unknown value:
  `TODO_FROM_SOURCE` (evaluates to NEEDS_JUDGEMENT).
- Size- or area-dependent limits are formulas whose operands carry units, e.g. an allowed capacity
  in W = `threshold('density', climate_zone, 'le_500') * conditioned_floor_area` (W/m^2 x m^2).
  The validator checks the formula's dimension equals its declared unit.
- Band edges (airflow, area, DN, temperature) are thresholds too, never literals in `check`.
  A bare number may only be compared with a dimensionless input (e.g. `climate_zone <= 5`).
- Every input has a parseable pint unit when numeric. `kWr` is stored as kW. Temperature
  differences (dead band) are K; absolute temperatures are degC.
- The engine converts every input through pint at the boundary. A unit of the wrong dimension, an
  unparseable unit or a missing unit gives NEEDS_JUDGEMENT, never a silent cast
  (`test_unit_mismatch_is_needs_judgement` runs this against every rule).
- `threshold(name, *keys)` walks groups transparently; runtime keys (climate zone, location) are
  looked up when evaluated. A formula threshold is evaluated lazily with the current inputs.

## 2. Applicability and exemptions: one convention, both editions

Evaluation order: `applies_when` filters -> `exempt_when` -> `needs_judgement_when` -> `check`.

- **NOT_APPLICABLE** only when applicability is definitively false on supplied inputs: an
  `applies_when` filter (`key: value`, a list = any-of, `{in: [...]}`, `{not_in: [...]}`) that does
  not match, or an **encoded** exemption in `exempt_when` that is true.
- **NEEDS_JUDGEMENT** for any exemption or condition the rule does not encode: put it in
  `needs_judgement_when` (e.g. specialised dead band, structural penetrations, unknown exemption
  enum, the 20-30 degC pipe gap, system-level alternative paths).
- A missing input read during evaluation (or a `TODO_FROM_SOURCE` value, or a unit problem) gives
  NEEDS_JUDGEMENT. `and`/`or` short-circuit, so an input read only on an untaken branch is not
  needed. If any filter is definitively false the result is NOT_APPLICABLE even if another is
  missing.
- Boolean exemption filters (`flag: false`) are rejected in `applies_when`; they hide unencoded
  exemptions as NOT_APPLICABLE. `exempt_when` needs a NOT_APPLICABLE test and `needs_judgement_when`
  a NEEDS_JUDGEMENT test (validator).

## 3. Inputs

Every `applies_when` key (bar `edition` and `state`, which come from the project) and every name in
an expression must be a declared input with provenance and be listed in `depends_on`, so a changed
project value re-runs the rules that read it. Extracted values are not inputs until gate 1.

## 4. Not yet implemented in any pack (logged in STATUS.md)

State variations that need no rule today (NT, TAS Class 2/4), system-level alternative paths for
J6D5(1)/J6D8(1), and an `edition`/`state` expressive test syntax.


## 5. Implemented in Sprint 1 (apps/api/mep/engine)

- **Evaluator** (`evaluator.py`): AST whitelist, never compiled or executed; limits on length, nodes, depth and a
  per-call step budget (nested threshold lookups share it). Unknown syntax is `EvalError`; unusable data is
  `Missing(kind)` -> NEEDS_JUDGEMENT. Kinds: missing_input, unit, type, domain, unconfirmed, provenance, todo,
  threshold, arithmetic, budget, rule. Absolute temperatures (degC/degF) cannot enter arithmetic, `abs`, `min`,
  `max`, and are not compared across different units; a quantity with units never compares with a bare number.
- **Units** (`units.py`): the caller must state a unit; a missing/empty unit is NEEDS_JUDGEMENT (never cast to the
  declared unit). Unit text never reaches pint's string parser: pint's preprocessor rewrites `sq`, `cubic`,
  `squared`, ` per ` into real powers and reads `9_999_999` as a number, so parsed text can hang the process.
  Instead the text must match a strict grammar (`valid_unit_text`: NAME terms joined by `*` or `/`, each with at
  most one single-digit exponent, no whitespace, no standalone numbers) and the unit is built from pint objects one
  name at a time (`unit_of`). Conversion factors must be within 1e-12..1e12 and a converted value must stay finite
  (prefix stacking such as `Ym^9/ym^7` is refused). Absolute temperatures cannot be part of compound units and must be at or above absolute zero; dimensionless
  names (percent, ppm, pi, turn, radian ...) cannot appear inside a compound unit (they would silently rescale
  the value); logarithmic units (dB, neper ...) are not supported. The same parser is used for units declared in rule YAML (at load, at run time, and in `validate_rules.py`). pint's
  caches are trimmed. Physical quantities cannot be negative (kind `range`) except absolute temperatures.
- **Inputs** (`rule_eval.py`): bool/int/number/text types enforced (no 1-for-true, NaN/inf, bool-for-number);
  enum/string inputs must be in their declared `values`; conditions must give true/false and filters may only use
  `in`/`not_in`, otherwise NEEDS_JUDGEMENT.
- **Provenance:** `extracted` anywhere in a run refuses the whole run (rule 10). `default` is not confirmed. A
  supplied provenance must equal the rule's declared provenance or be `engineer_confirmed`. The project facts that
  choose the edition (state, edition, class, date) must be `engineer_confirmed` (`unconfirmed_facts`); the climate
  zone may additionally be `address_lookup_confirmed`. Provenance strings are converted to the enum; unknown ones
  are refused.
- **Request and facts** (`runner.py`): the caller's request is read once into plain exact-typed copies (subclasses
  and objects with odd methods are refused; later reads cannot differ from what was validated). Shape and size are
  validated first (subjects with unique text ids, rules a list of unique text ids, inputs InputValue with exact
  value types, whole numbers up to 1e15, text caps, at most 5000 rule evaluations per run), all `invalid_request`.
  Climate zone 1-8; building class must be in the ONE canonical list `building_classes` in
  `rules/applicability.yaml` (rule data cannot widen it; CI checks every rule and every carve-out against it);
  approval date a calendar
  date; edition known; state is stripped and upper-cased once and used everywhere. Subjects may not supply
  `climate_zone` or `building_class`.
- **Jurisdiction gate** (`jurisdiction.py`, data in `rules/adoption.yaml`): the edition must be confirmed in force for
  the state, approval date and building class. Refused: unknown state, unverified/not-adopted/replaced edition, not
  yet in force, or past its transition. The class carve-outs are the separate applicability check (section 6). An approver may
  override with a reason of at least 10 letters/digits (control and zero-width characters stripped); the override
  is written to `ledger_event` before any rule runs and the run fails closed if that fails. A refusal for another
  reason (such as an unselected rule) is raised before the ledger write.
- **Selection** (`runner.py`): exactly the project's edition and state; retired rules never; a subject may name only
  selected rules. Every selected rule's expressions are checked (parse, declared names, thresholds) before anything
  is written to the ledger (`rule_error`). The report lists selected rules no subject ran (`unassigned_rules`).
- **Near miss:** each numeric (non-offset) input the rule read is perturbed by +/- the rule's `near_miss`
  fraction; it is a near miss if the outcome flips between PASS and FAIL (a judgement is not a flip).
  Absolute-temperature inputs are reported as `not_evaluated`.
- **Report** (`report.py`): JSON and PDF; `banner` is "DRAFT RULES: NOT ENGINEER-APPROVED" unless every rule used is
  approved (and always on an empty run); PDF: every page; free text is escaped. Every result carries a citation.

## 6. Edition gate versus applicability (two separate checks)

- **Jurisdiction gate** (`jurisdiction.decide`, `rules/adoption.yaml`): decides the NCC EDITION only, from state and
  approval date. It does not know building classes.
- **Applicability check** (`applicability.check`, `rules/applicability.yaml`): decides whether that edition's rule pack
  applies to a building class in that state. Carve-outs (for example Class 2 and Class 4 parts that follow BASIX in NSW
  or NCC 2019 Section J in TAS) each carry an official source. NSW Class 2 on NCC 2022 stays refused for now.
- Both run on every request; either can refuse. One approver override (reason of 10+ letters, written to the ledger first)
  covers whatever refused; the ledger payload and the report record `refused_by` (`jurisdiction`, `applicability`).
- `scripts/reachability.py` lists, for each rule, the state/class combinations that can only run through an override as
  UNREACHABLE (`docs/engineer-review/index.md` and a notice from `validate_rules.py`).

## 7. Sign-off fields and the release gate

- `status: approved` needs `reviewed_by`, `reviewed_on` AND `reviewer_registration_no` (schema, loader, validator).
  Sign-off fields on a draft rule are a load/validation error. `checked_by` + `checked_on` (together) record an optional
  first-pass value check by someone else; they never change status.
- **How agents are kept off these fields (be clear about what this is).** `scripts/signoff_guard.py` parses the rule YAML
  before and after a change and flags any newly filled sign-off/check field or `status: approved`, so quoted keys, flow
  style, merge keys, extra documents and partial edits are all seen (Edit/Write hook `.claude/hooks/guard_rules.py`).
  The hook always runs for rule files, including a deletion-only edit; an Edit whose `old_string` is not in the file, a
  file that cannot be read as UTF-8, or any hook error is refused (fail closed). Paths are matched case-insensitively
  with Windows aliases (trailing dot or space, `::$DATA`) and symlinks resolved; the adoption/applicability/schema
  exclusions are exact names. The same hook stops an agent writing a golden project's `meta.yaml` to `synthetic: false`
  or adding a `data_agreement.*` file. A Bash hook blocks shell commands that name a rule YAML together with a sign-off
  field (a heuristic: a shell can always build a command it does not recognise), blocks setting the commit override
  variable, and blocks `--no-verify` / hooks-path changes. `.githooks/pre-commit` (`scripts/check_signoff_changes.py`)
  compares staged files (added, copied, modified, renamed, type-changed) with HEAD and stops a commit that changes those
  fields unless the engineer declares who is signing in their own shell. **These are tripwires for agents and honest
  mistakes, not locks:** a person, or any process outside these hooks, can bypass them. The controls that count are the
  human review of every rule diff, CI, and the registration number being a field you can check. There is not yet a CI
  step comparing sign-off fields with the merge base; that is an open item. `test_none_approved` stays until the first
  real approval.
- Golden projects carry `meta.yaml`. A project is REAL only with `synthetic: false`, a real firm name, a
  `data_agreement.*` file in the project folder, and an engineer with a registration number (`engineer_signed_off_by`);
  placeholders (TBD, N/A, x ...), invisible characters, non-text values, a `syn-` prefix or a wrong name do not count.
  While there are zero real projects `scripts/release_gate.py` blocks release claims: `--scan` (docs, READMEs, other
  text files and version fields; CI on every push), `--check` (CI on non-sprint tags and published GitHub releases),
  `--message` (commit-msg hook), `--tag` (pre-push: only `sprint-N` tags). The claim scan ignores a claim only when it is
  quoted or a negation sits in the few words before it. Local hooks can be skipped; CI is the backstop.
- `rules/applicability.yaml` is validated on load (state exactly one of the adoption states, known edition, classes in
  the canonical list, effect, reason, official `.gov.au` source); malformed data stops the run (`invalid_data`).
  The override ledger kind follows what refused: `jurisdiction_override` or `applicability_override`.
