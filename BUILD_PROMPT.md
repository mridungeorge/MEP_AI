# MEP Co-pilot — Master Build Prompt for Claude Code

Paste everything below the line into Claude Code at the repo root, after copying this kit in.
Run one sprint per session: `/sprint 0`, then `/sprint 1`, and so on.

PRD and spec (source of truth for product decisions):
https://claude.ai/code/artifact/7b69c26e-23bd-49c9-b2ca-210f7a604b9a

---

You are building **MEP Co-pilot**: a tool that takes an architect's drawings (IFC, DWG/DXF, PDF) or a from-scratch spec, plus the engineer's system schedules, and produces an engineer-verified Australian mechanical services package: a cited NCC Part J6 compliance report, draft HVAC drawings, fabrication-ready duct fittings and IFC additions for Revit.

Read `CLAUDE.md` first. Its non-negotiables override anything else, including this prompt.

## 1. The one architecture rule

**AI proposes → code decides → validator measures → human signs.**

- The LLM never produces a compliance result, a clause number or a threshold. Those come only from rule YAML evaluated by the deterministic engine.
- Every generated file passes its validator before a user can see it.
- Nothing is final until designer (gate 1), checker (gate 2) and approver (gate 3) sign.

## 2. Stack (reuse, don't invent)

| Layer | Choice |
| --- | --- |
| Web | Next.js 14 + TypeScript, Tailwind, shadcn/ui |
| API + workers | FastAPI, Python 3.12, uv for deps |
| Data/auth/storage | Supabase (Postgres + RLS, buckets), AU region |
| Agent runtime (in product) | Claude Agent SDK (Python), custom tools only (section 6) |
| CAD | CadQuery (fork earthtojake/text-to-cad skills), ezdxf, IfcOpenShell |
| Units | pint |
| Rule evaluation | Own AST-whitelist evaluator (no `eval`, no `exec`) |
| Tests | pytest, hypothesis for evaluator fuzzing; Playwright for web smoke |
| CI | GitHub Actions: lint, type-check, rule tests, golden evals |

## 3. Repo layout

```text
mep-copilot/
├── CLAUDE.md
├── .claude/                 # hooks, agents, commands (this kit)
├── apps/web/                # Next.js
├── apps/api/
│   └── mep/
│       ├── engine/          # rule loader, evaluator, citation, cross-rule rerun
│       ├── ingest/          # ifc, dxf, pdf, health score, exporter profiles
│       ├── diff/            # revision diff, dependency graph, reasoning trace
│       ├── review/          # gates, exception classifier, sampling, ledger
│       ├── agents/          # runtime agents + tool layer (LLM lives ONLY here)
│       ├── skills_runner/   # calls skills, collects manifests, runs validators
│       └── api/             # FastAPI routers
├── skills/                  # text-to-cad style skills (SKILL.md + scripts + validator)
├── rules/
│   ├── schema/rule.schema.json
│   ├── adoption.yaml        # NCC edition by state, as data
│   ├── ncc2022/j6/*.yaml
│   └── ncc2025/j6/*.yaml
├── evals/golden/<project>/  # inputs + expected report JSON
├── scripts/validate_rules.py
├── tests/{rules,engine,ingest,diff,review,golden}
└── docs/STATUS.md           # current sprint + open issues (read by SessionStart hook)
```

## 4. Data model (Supabase, all tables RLS by firm_id)

firm, user(role: designer|checker|approver, registration_no), project(address, state, climate_zone, building_class, ncc_edition), revision(architect_rev, status, frozen_at, parent_revision_id), space(ifc_guid, name, area_m2, use, ceiling_void_mm, provenance), system(type, airside_airflow_ls, capacity_kw, fan_power_kw, controls jsonb), equipment, rule_result(rule_id, result, inputs jsonb, citation jsonb, fix_hypotheses jsonb, review_class, stale bool), artifact(kind, path, checksum, validator jsonb, released bool), review(gate, user_id, decision, reason), signoff(gate, user_id), ledger_event(append-only), ledger_link(token, expires_at).

Every numeric input column stores value + unit + provenance (`engineer_confirmed | extracted | calculated | default`).

## 5. Sprints and acceptance criteria

Do not start a sprint until the previous one's acceptance tests pass in CI.

### Sprint 0 — Foundations
- Scaffold repo, uv + pnpm workspaces, CI, Supabase migrations for section 4.
- `rules/schema/rule.schema.json` enforced by `scripts/validate_rules.py`.
- `rules/adoption.yaml` with the state table from the PRD.
- Encode first 10 J6 rules **as drafts** for NCC 2022 and NCC 2025, using the `rule-encoder` subagent. Thresholds marked `TODO_FROM_SOURCE` until a human types them from the ABCB text.
- **Accept:** schema validation passes; every draft rule has ≥2 tests; no rule has `status: approved`.

### Sprint 1 — Prove the maths
- `engine/evaluator.py`: parse `check` into AST, allow only names, constants, comparisons, `and/or/not`, arithmetic and whitelisted functions (`threshold`, `min`, `max`, `abs`). Reject everything else.
- `engine/units.py`: all inputs coerced through pint; unit mismatch = `NEEDS_JUDGEMENT`, never a silent cast.
- `engine/runner.py`: select rules by `applies_when` (edition, state, climate zone, class), evaluate, attach citation from YAML, compute near-miss.
- Cited report: JSON + PDF.
- **Accept:** hypothesis fuzz of evaluator finds no escape; all rule tests pass; golden projects match expected JSON exactly; zero results without citation.

### Sprint 2 — Prove the data
- `ingest/ifc.py`: IfcSpace → space table; read header `originating_system`, map to exporter profile in `ingest/profiles/*.yaml`.
- `ingest/health.py`: health score (closed boundaries, scale, layers, duplicates, names, schema, exporter).
- DXF ingest via ezdxf; PDF via vision model **only into the extraction table** (marked `extracted`).
- System/equipment schedule: form + Excel import.
- Gate 1 UI: designer confirms table; engine refuses to run on any `extracted` value.
- **Accept:** golden IFC projects reach ≥ target extraction accuracy (record the number, don't invent one); engine run blocked until gate 1.

### Sprint 3 — Prove the value
- `diff/graph.py`: dependency graph from every rule's `depends_on`.
- `diff/revision.py`: match spaces by IFC GUID, else name + centroid; mark dependent results stale.
- `diff/trace.py`: reasoning trace built from graph edges (never LLM text).
- `engine/cross_rule.py`: any input change re-runs every dependent rule across all packs; fix hypothesis returned only if all pass.
- **Accept:** golden "Rev B → Rev C" pair produces the expected trace and stale set; a fix that breaks another rule is withdrawn with the conflict shown.

### Sprint 4 — Ready for pilot
- `review/classifier.py`: exception classes (fail, judgement, override, fix-driven pass, near miss, checker-agent flag) vs clean pass.
- Bulk approval of clean passes with random sample (size per firm), logged.
- Gates 2 and 3, freeze revision, immutable ledger + time-limited share link.
- `skills/duct-fab`: spec card → CadQuery solid → STEP + flat-pattern DXF; validator proves the pattern folds back to spec within tolerance.
- Runtime agents (section 6) wired to the UI.
- **Accept:** end-to-end golden project from upload to signed package; duct-fab validator suite green; unvalidated artifact never reaches the API response.

## 6. Runtime agents (inside the product)

Three agents on the Claude Agent SDK. The **tool layer is the safety boundary**: the server enforces what each agent may do; the prompt is not trusted to.

| Agent | Allowed tools | Forbidden |
| --- | --- | --- |
| designer | get_project_context, get_spec_card, fill_spec_card, ask_clarifying_question, run_skill, request_rule_run, propose_fix_hypothesis, explain_result | writing rule_result, approving anything, editing rules |
| adversarial_checker | get_project_context, list_results, list_artifacts, raise_flag | any write except raise_flag |
| compliance_risk | get_project_context, list_results, raise_risk | any write except raise_risk |

Tool contracts (implement in `apps/api/mep/agents/tools.py`, each with a Pydantic input model):

- `get_spec_card(skill)` → required fields with units and firm defaults.
- `fill_spec_card(skill, values)` → validated card or list of missing/invalid fields. Never guesses.
- `ask_clarifying_question(field, options[2..4])` → shown to user as multiple choice.
- `run_skill(skill, spec_card_id)` → runs skill, runs validator; returns `{released: bool, artifact_id?, failed_checks[]}`. Retries at most 2 times, then surfaces failed checks.
- `request_rule_run(revision_id)` → engine runs; agent receives results read-only.
- `propose_fix_hypothesis(rule_result_id, input_changes)` → engine applies on a scratch copy, runs cross-rule rerun; returns `{accepted, conflicts[]}`; accepted items are labelled "Hypothesis: verify".
- `explain_result(rule_result_id)` → returns the structured result; the agent may phrase it but may only reference rule IDs present in it. Post-process: strip any clause/rule ID not in the payload.
- `raise_flag(target_id, reason, severity)` / `raise_risk(...)` → added to checker queue.

## 7. How to work in this repo (Claude Code behaviour)

- Plan first for any sprint: list files, tests, and acceptance checks before editing.
- Write tests before implementation for engine, diff and review.
- Use subagents: `rule-encoder` for rules, `validator-author` for validators, `skill-builder` for skills, `adversarial-reviewer` before closing every sprint.
- Use commands: `/sprint N`, `/new-rule`, `/new-skill`, `/golden-eval`.
- Hooks will block some actions (see CLAUDE.md). When blocked, fix the cause; never work around a hook.
- Update `docs/STATUS.md` at the end of each session: sprint, done, next, blockers.
- Small commits, one concern each.

## 8. Definition of done (every sprint)

- All tests green locally and in CI; Stop hook passes.
- `adversarial-reviewer` run, findings fixed or logged in STATUS.md.
- No TODO in engine, diff or review code paths shipped to main.
- STATUS.md updated.
