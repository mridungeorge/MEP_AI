# Golden projects

Each directory is one project: `project.yaml` (inputs), `derivation.yaml` (outcomes derived BY HAND from the
rule text before the engine was run), and `expected_report.json` or `expected_refusal.json` (the exact output).

**These are synthetic.** They exercise the engine and are not pilot-firm projects. Seven synthetic projects exist; the five pilot-firm
golden projects are still outstanding (see docs/STATUS.md). Do not regenerate expected files to make a test
pass: a difference is a finding. `python scripts/golden_regen.py` shows a diff and only writes with `--accept`.

The report's `rule_pack[*].sha256` is removed before comparison (it changes with any edit to a rule file);
`tests/golden/test_golden.py` checks it against the rule files separately.

## meta.yaml and the release gate

Every project has a `meta.yaml`. It is REAL only when `synthetic: false` and `firm`, `data_agreement` and
`engineer_signed_off_by` are all filled in. All seven here are SYNTHETIC; test output says so (`golden projects: 7
SYNTHETIC, 0 REAL`) and `scripts/release_gate.py` blocks any release claim while zero real projects exist.
