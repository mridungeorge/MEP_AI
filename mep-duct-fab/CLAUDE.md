# MEP Co-pilot — Project Rules

Australian mechanical services design-to-verification tool. Full build plan: `BUILD_PROMPT.md`. Product spec: the PRD linked there.

## Non-negotiables (hooks enforce several of these)

1. **No LLM decides compliance.** Rule results, clause numbers and thresholds come only from `rules/**/*.yaml` evaluated by `apps/api/mep/engine/`. Code under `apps/api/mep/agents/` must never construct a PASS/FAIL result.
2. **No `eval` or `exec`** anywhere in `engine/`. The evaluator is an AST whitelist.
3. **No hard-coded clause IDs or thresholds in Python.** They live in rule YAML only.
4. **Agents never approve rules.** New and edited rules are `status: draft`. Only a human engineer sets `approved`, with `reviewed_by` and `reviewed_on`.
5. **Never reproduce standards text.** Rules store clause references and encoded logic, never `clause_text` or copied wording.
6. **Thresholds come from the official source.** ABCB NCC text for NCC; licensed AS text for Australian Standards. Never from memory, blogs or secondary summaries. Unknown value = `TODO_FROM_SOURCE`.
7. **One NCC edition per project.** NCC 2022 and NCC 2025 are separate rule packs; never mix them in one evaluation.
8. **Units everywhere.** Every numeric input passes through pint. Unit mismatch → `NEEDS_JUDGEMENT`.
9. **Validators gate outputs.** A skill artifact with a failed validator is never returned to the user.
10. **Extracted values are not inputs.** The engine refuses any value with provenance `extracted` until gate 1 confirms it.

## Conventions

- Python 3.12, uv, ruff, mypy strict on `engine/`, `diff/`, `review/`.
- Tests first for engine, diff, review. Golden evals in `evals/golden/` must match exactly.
- Rule IDs: `NCC<edition>-<clause>-<slug>` (e.g. `NCC2022-J6D3-econ-cycle`). State variations: `NCC2022-NSW-J6D2-...`.
- Every rule declares `depends_on` so revision diff and cross-rule rerun work.
- Fix suggestions are always called **fix hypotheses** and labelled "Hypothesis: verify".

## Session habits

- Read `docs/STATUS.md` at start (SessionStart hook prints it).
- Update `docs/STATUS.md` before ending a session.
- If a hook blocks you, fix the cause. Never disable or bypass hooks.
- Review agents (adversarial-reviewer, code-review, any other) never touch the shared local DB: they use a disposable
  database from `python scripts/review_db.py` with its read-only / client-level roles.
