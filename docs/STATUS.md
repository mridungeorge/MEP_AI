# Status

Sprint: 2 (prove the data) rebuilt and finished after the 2026-10-09 recovery; two review rounds done (see "Sprint 2 finish");
local devcontainer gate is the reference (tag `sprint-2-gate`). GitHub CI has never run (no remote CI evidence).
Updated: 2026-10-09 (recovery after a device change; see "Recovery 2026-10-09")

## Resume here (updated 2026-10-12, OVERNIGHT RUN: plan in docs/OVERNIGHT_PLAN.md, scheduled task 53299a5d every 30 min)
Phase 7 DONE (`sprint-7-gate`). Phase 8 items 8.1-8.6 DONE and pushed. NEXT ACTION: Phase 8 close = adversarial review (max 2 rounds) of 8.1-8.6 since `sprint-7-gate`, fix, GitHub CI green on head, tag `sprint-8-gate`, push --tags; then Phase 9.
Done and tagged: 2.5, 3, 4a, 4a.1, 4b, 5. **Phases 6-9 are the standing order** (user prompt: run 6, 7, 8, 9 in order without stopping, then write
`docs/review-pack.md` and STOP; do not encode AS 1668.2, AS/NZS 3000, 3008 or AS 4254).
Phase 6 (product shell) is DONE: tag `sprint-6-gate`. Next: Phase 7 (engineering features), then 8, 9, then `docs/review-pack.md`.
**Local environment is fragile:** the Windows host has ~2 GB free RAM and ~7 GB free disk (Docker holds ~45 GB: 8 GB of unused images, 7.7 GB build cache; WSL has
no memory cap). Claude Code stopped one long test run for low memory and said not to re-launch it unasked. Until the user frees space (suggested: prune unused
images + a `.wslconfig` memory cap), run only SMALL local test batches and use GitHub CI (it runs the whole suite and e2e) as the full check. A clean-clone
`scripts/ci.sh` was NOT run locally for Phase 6 for that reason: CI mirrors it.
Notes: the local Supabase signs tokens with ES256 (JWKS). Mail for magic links goes to Mailpit (`http://127.0.0.1:54324`). Run everything in
the devcontainer; `~/ws` is a synced work copy (never `uv sync` there); clean gates run from `git clone` copies (`~/gate3`). On the Windows
host put `AppData/Local/Python/bin` (under the user profile) first on PATH (the WindowsApps `python` stub fails). `.env.example` files cannot
be written here (permission rule): the templates are `deploy/env.api.example` and `deploy/env.web.example`. DO NOT run `docker build` in the
devcontainer: it hung Docker Desktop (restarted once); `deploy/api/Dockerfile` is unverified by a build (read-reviewed; uv image 0.12.24 matches the lock).
Existing open revisions have results without an input fingerprint (`inputs_hash` NULL): re-run once before freezing.

## Phase 6 product shell (2026-10-12)
- 6.1 Firm admin (0029, `api/admin.py`, `/admin`): `app_user.is_admin/active/email`; invitations joined only by a CONFIRMED matching address
  (`accept_invitation`); roles, admin flag, deactivation (deactivated = nobody: `current_firm_id()` returns null, API 403 `deactivated`); the last admin cannot go;
  firm settings (name, signer independence, spot-check sample size, near-miss default (stored; the engine does not use it yet)); title block (DXF) and layer
  standard (JSON) uploads (`firm_template`, newest of each kind is the active one; used by the Phase 8 drafting skills).
- 6.2 Registration (0030, `api/platform.py`, `/platform`): the firm admin SUBMITS number + register (NER/RPEQ/STATE); a platform administrator (`platform_admin`, set in
  the database) VERIFIES with a note; cannot decide their own or one they submitted; ledgered; `scripts/admin_users.py register-approver` is break-glass (needs
  `--reason`; the ledger says `registration_path: break_glass`; any other direct change reads `direct`).
- 6.3 Projects dashboard (0031, `api/projects.py`, `/projects`, `/projects/{id}`): list + filters (state, edition, status, search), revision history, who signed what.
  `POST /projects` creates a project (billing-gated, 0034).
- 6.4 E-mail (0032, `api/notifications.py`): the database writes an outbox (review requested -> checkers, ready for Gate 3 -> approvers, signed, changes requested,
  share link opened); per-person preferences; Resend sender in a background thread; e-mails carry a link, never results. Needs `RESEND_API_KEY`.
- 6.5 Observability (`observability.py`, `lib/sentry.ts`): Sentry (API, dispatcher, web) with PII scrubbing, JSON logs with request ids, `/healthz` + `/readyz`,
  uptime runbook. Needs `SENTRY_DSN` (optional).
- 6.6 Data safety (0033, `scripts/dataops.py`, `docs/runbooks/backup-restore.md`): backup, restore into a scratch DB with row/ledger/file comparison,
  per-firm export, retire (erases personal data + drawings, keeps the signed record and ledger), purge after >= 7 years. Found and fixed: purge left the
  append-only guards switched off.
- 6.7 Billing (0034, `api/billing.py`, `/billing`): Stripe TEST mode only (`sk_test_` enforced), plans in `apps/api/billing/plans.json`, trial, signed webhook,
  gate on NEW projects only (signed work never locked). Needs `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, price ids.
- 6.8 `/terms`, `/privacy`: placeholders marked LEGAL REVIEW REQUIRED (no legal text drafted).
- Not done / limits: the near-miss firm default is stored but not applied by the engine; no email-verified self-signup (people join by invitation only); the
  platform-admin console is minimal; billing has no invoices UI; Sentry/Resend/Stripe paths are tested with fakes only (no live keys exist).

### Phase 6 review
- **Phase 6 review record.** Round 1 (two reviewers): 2 blockers (clients could INSERT projects/revisions around the billing gate; an admin could make themselves
  approver and submit their own registration number) + should-fix (last-admin race, invitation oracle and no choice among invitations, Stripe event ordering and
  duplicate subscriptions, Sentry local variables, uvicorn logs, `/readyz` DoS and detail, share links surviving retirement, outbox to deactivated people): fixed in
  0035 + code. Round 2: no blocker; should-fix (project address reuse, one registration number for two accounts, re-subscription after cancel, equal-time events,
  concurrent project limit): fixed in 0036. **Re-reviewed: round-1 fixes (by round 2). NOT re-reviewed: the round-2 fixes (0036, billing adoption/ordering change).**
  CI also found: `ruff` shebang, and purge-firm leaving append-only guards off (fixed + tested). A clean-clone `scripts/ci.sh` was not run locally (memory); GitHub
  CI, which runs the same steps including e2e, is green on `9f4b839`.
  Unfixed nits: two simultaneous Stripe checkouts can both charge (the second subscription is ignored but billed: cancel unadopted subscriptions); the permanent ledger
  holds the registration NUMBER and the platform administrator's note only as a hash; the invitation e-mail has no rate limit (admin-chosen firm name in a mail to any
  address); the near-miss firm default is stored but not applied by the engine.

## Phase 5 pilot hardening (2026-10-12): no new features
- (1) Evidence spaces: "Add as a space" from a drawing reading is `evidence_add_space` (0022): the space stays `extracted`, links to the extraction row, the
  page and the source file, records the unit the designer declared (m^2/ft^2/mm^2; void mm/m/in/ft; no unit is assumed, PDF numbers are stored with unit
  `unverified`), needs Gate 1, and database triggers refuse any edit or relabel of the evidence-derived area/void or the link (all roles). Tests:
  `tests/rls/test_pg_evidence_space.py`.
- (2) An agent's `run_skill` returns `needs_confirmation` (and leaves a note) unless a designer confirmed the EXACT effective card version (hash of the card with
  firm defaults and constants applied): `spec_confirmation` + `spec_card_confirm` (0023), API `card-preview` / `confirm-card`, drafting-screen review button;
  `perform_run(expected_digest=...)` refuses a card that changed after confirmation.
- (3) Drafting builds: `skill_job` queue (0024) + dispatcher (`python -m mep.skills_runner.dispatcher`) running the build and the independent re-check as two
  separate containers (non-root, no network, read-only, `/job/out` the only writable place, CPU/memory/pids/time limits; `sandbox_docker.docker_run_args` is the one
  place that defines it). `MEP_SKILL_EXECUTOR=queue` makes the API enqueue. `deploy/worker/Dockerfile` carries cadquery. Isolation tests run real probes in a
  container (`MEP_ISOLATION_IMAGE=python:3.12-slim` locally, the built worker image in CI). **The dispatcher needs a Docker host: Railway cannot run it**
  (docs/runbooks/skill-worker.md); without one, drafting says "no drafting worker is running" and the rest works.
- (4) PDF reading is a background job (`vision_job`, 0025): the upload stores the file and returns `queued`; a thread in the API (or `process_pending`) renders
  and reads it; the upload panel shows queued/running/done/failed.
- (5) Agent notes written after a freeze are marked `post_freeze` by a database trigger, labelled in the UI, and are not in the signed package or its PDF (tested).
- (6) CI `images` job builds the API, web (`deploy/web/Dockerfile`) and worker images and starts/checks each; `scripts/staging_smoke.py` (sign in, upload the
  fixture IFC, Gate 1, run, report; with checker+approver tokens also freeze, sign Gates 2 and 3 and use/revoke a share link) runs against any URL and is tested
  against a live uvicorn (`tests/rls/test_pg_smoke.py`). None of the three images could be built locally (Docker build hangs Docker Desktop here): the CI job
  is their first build.
- Migrations 0024 and 0025 were edited once after being pushed and before any deployment (column grants, firm_id on skill_job_file).

- **Phase 5 review record.** One consolidated review of everything since `sprint-3-gate` (4 reviewers, round 1) + a round 2 on the fixes (2 reviewers).
  Round 1: 2 blockers (a designer could attach a forged evidence link to a typed space by UPDATE; the dispatcher read worker output by following symlinks/FIFOs) +
  the smoke script able to freeze/sign any revision, vision jobs that could stick, silent unisolated builds in a deployment, filter vocabulary, idempotent card
  confirmation, 500 on an unchanged evidence value, and more: all fixed (0027, runner.read_regular/check_output_dir, smoke refuses non-SMOKE-TEST revisions,
  `app_from_env` disables local builds). CI also found a real bug: files written by the container user were unreadable by the collecting user (worker now
  chmods output 0644) and the API image lacked `pypdf` (now a lazy import).
  Round 2: 1 blocker (an evidence space could never be removed or corrected: now `evidence_remove_space` + DELETE route + button, 0028) + should-fix (a crafted
  insert could half-link a row and lock it: guard tightened; cleanup of root-owned job output: executor `cleanup`). **Re-reviewed: round-1 fixes (by round 2).
  NOT re-reviewed: the round-2 fixes (0028, `_cleanup`/`DockerExecutor.cleanup`, dispatcher rowcount check, `vision_job._finish` guard).**
  Unfixed nits: other local users on the dispatcher host can read/write the job's work directory while it runs (use a private 0700 parent or userns remap);
  the dispatcher holds the full service DSN and the Docker socket (give it a dedicated Postgres role); the API trusts the worker's manifest (it re-hashes the
  files but does not re-run the validator); unbounded JSON bodies on card-preview/confirm-card; raw tool arguments (incl. unfiltered explanation text) go
  into the hash-chained ledger and cannot be purged; unit conversion for evidence lives in SQL (tied to pint by a test).

### Known limits (validator blind spots and others, deliberately left)
- space-envelope validator does not check: the VOLUMEUNIT prefix, `IfcQuantityArea` unit overrides, a property set shared between two spaces, DXF TEXT style /
  oblique / width factor / mirror flags, or geometry placed inside the standard arrow-head blocks (never drawn). It does check the world geometry (placement chain,
  context, map conversion, solid, DXF entity attributes).
- The explanation/reply filters remove outcome and compliance wording, foreign rule ids and clause numbers; they cannot check numbers or truth in prose, and a
  denylist cannot catch every paraphrase of "this is fine" in a fix hypothesis.
- LocalExecutor (development) is not isolated; the message throttle and the vision reader are per process; one vision job holds a request-independent thread for up
  to ~150 s per file; notes and clarifying questions can be added to a frozen revision (marked post-freeze).

## Phase 4b drafting skills, agents, PDF evidence (2026-10-12)
- Built: space-envelope skill (rooms/plant rooms -> IFC4 + plan DXF, 30-check independent validator, determinism, round-trip through the app readers);
  skills_runner (sandboxed build, separate-process re-check of the manifest checksums AND of the submitted spec, release only when both pass; bytes
  stored only for released artifacts; firm defaults only on `x-firm-default` fields, type-checked); drafting wizard (card as a form, sentence shortcut read back
  with assumptions and unread text, confirmation before Build); runtime agents (designer / adversarial_checker / compliance_risk; the tool layer enforces
  permissions, human role, open revision, argument shape, rate limits; every call logged and ledgered; model replies and explanations filtered; notes only);
  PDF pages rendered in the worker sandbox (pdfium) and read by a vision model into the evidence table only (provenance `extracted`, never a space, never an input).
  Docs: `docs/skills/space-envelope.md`, `docs/agents.md`. Migrations 0018-0021.
- Review round 1 (two reviewers): 2 blockers (validator measured only local profiles so placement/rotation/extra-entity tampering passed; the model's own
  chat reply was shown unfiltered) + ~15 should-fix, all fixed or listed below (c4811cf, migration 0021). Round 2 found 2 blockers (the
  validator still ignored the representation context/map conversion/spatial chain; the reply filter was beaten by soft hyphens, variation selectors and
  look-alike letters) + should-fix (flags/risks/questions unfiltered, underscore ids, over-broad stems, PDF-on-frozen checked after the vision spend, profile
  subtype, validate-child cwd), all fixed. **The round-2 fixes had no third review.** Unfixed nits: validator blind spots (VOLUMEUNIT prefix, quantity unit
  overrides, TEXT style/oblique, geometry inside the standard arrow blocks); an agent message that fails still counts toward the throttle.
- Needs on a server: `ANTHROPIC_API_KEY` for agents and vision (without it both say so and nothing breaks); `--extra agents` in the image (Dockerfile does);
  duct-fab needs cadquery, which the API image does not carry (the card answers "not installed on this server"). CI pins cadquery 2.8.0 / ocp 7.9.3.1.1.
- Known limits, not fixed: the worker sandbox is rlimits only (same uid, network allowed); vision runs in the request thread (150 s cap per file, no
  per-firm quota); the message throttle is per process; "Add as a space" from evidence stores `default`/manual provenance (the UI says areas are assumed m2);
  notes and clarifying questions can still be added to a frozen revision; `run_skill(use_draft)` by an agent builds without a human confirmation step (the
  validator gate and designer role still apply); the explanation/reply filters remove outcome and compliance wording and foreign rule ids but cannot check
  numbers or truth in prose; Dockerfile still never build-tested.

## Phase 4a.1 pilot readiness (2026-10-12): done, tag `sprint-4a1-gate`
- Review: round 1 found 0 blockers + 9 should-fix (mode-switch laundering, own acknowledgements, acting-role lock-out, share leakage,
  onboarding tools, one-person seed, demo script, Dockerfile pin, throttle key), all fixed (0016). Round 2 found 1 blocker (the per-signer
  acknowledgement collided with `unique (rule_result_id)`: an absent approver could make Gate 3 unsignable) + should-fix (voiding an open
  spot-check sample when a checker's role changes, Secure cookie by default, negative script tests, DEMO- case), fixed (0017).
  **The round-2 fixes had no third review.** Unfixed nits: the mode stamp on a review is read in a new statement (a mode switch mid-call could
  stamp `strict`); no backfill of `signer_mode` onto rows older than 0016 (no deployed data exists); the throttle is per replica.
- Signer independence is a firm setting (`firm.signer_mode`): `strict` (default, three different people) or `small_firm` (one person may hold
  several gates by ACTING in roles listed in `app_user.also_roles`; the acting role travels in the request claims and the database re-checks
  it against the firm mode and the person's roles). In small_firm every ledger entry (inside the hash), signoff row, package, PDF page and the
  UI says NOT INDEPENDENTLY CHECKED. Mode, roles and registration changes are ledgered. Tests for both modes: `tests/rls/test_pg_pilot.py`.
- Accepted FAIL: approving a FAIL needs a category (performance_solution + reference, rule_disputed, out_of_scope) and an explanation;
  never in bulk or a spot-check; `rule_disputed` writes `rule_dispute` and appends to `docs/engineer-review/disputed.md` (or
  `scripts/export_disputed.py`); the approver acknowledges each one individually (`gate3_acknowledge_fail`) before Gate 3; listed first in
  the package, PDF and UI.
- Registration numbers stay service-only: `scripts/admin_users.py register-approver` (evidence, ledgered), runbook `docs/runbooks/register-approver.md`.
- Share link: token in the URL FRAGMENT (`/share#token`); the page POSTs it once to `/share-api/exchange` (a Next rewrite to the API),
  gets a 15-minute HttpOnly SameSite=Strict session cookie; reads use the cookie; every exchange/read is logged; no token in any request URL, log
  or Referer (the Playwright test records every request). Old `/share/<token>` routes are gone.
- Races reproduced with two live database sessions (`test_pg_pilot.py`, last section): edit during freeze, freeze during edit, run during
  freeze, freeze during run. All four behaved correctly (the lock order from migrations 0012/0013 holds); no code change was needed.
- Deployment: `deploy/api/Dockerfile`, `railway.json`, `deploy/env.*.example`, `docs/runbooks/{deploy,migrate,register-approver}.md`,
  `scripts/seed_demo.py` (demo firm, three users, synthetic VIC office project), `/healthz`. `docs/demo-script.md` + `docs/demo-assets/`.
  The user must create the accounts and keys by hand: `docs/runbooks/deploy.md` Step 0 to 6.

## Phase 4a (2026-10-10): done, tag `sprint-4a-gate`
- Built: exception classifier (`review/classifier.py`; 7 exception classes + clean pass; reasons stored with the result); migration 0010
  (Gate 2 `gate2_review` / `gate2_prepare_bulk` / `gate2_bulk_approve` with a random spot-check, `sign_gate` state machine: gate order,
  frozen-before-sign, one signer per gate, approver registration number, signed revision immutable; ledger hash chain with per-firm
  sequence, `verify_ledger`, audit triggers on review/signoff/diff/sample/artifact; certifier share links stored as hashes); API
  (`api/review.py`, `review_pg.py`), signed package JSON + deterministic PDF with validator (`review/package.py`, artifact row, released
  only when all gates are signed and the ledger verifies); UI (review worksheet, bulk spot-check, sign-off, package view, public
  `/share/[token]`); Playwright designer -> checker -> approver -> signed package -> share link -> revoke (11 e2e tests).
- Review: round 1 found 2 blockers (a failed spot-check could be undone; a failed-validator PDF was still returned) + 9 should-fix, all
  fixed (0013). Round 2 found 2 blockers (rejection outside the sample could be laundered; one person could check and approve) + 4
  should-fix, all fixed (0014). **The round-2 fixes had no third review**; covered by `tests/rls/test_pg_signoff.py` (last section).
- Policies worth the user's attention: (a) the Gate 1 signer, the Gate 2 reviewers and the Gate 3 approver must be three different people
  (a one-person firm cannot sign off); (b) a checker may approve a FAIL (shown as "accepted FAIL" in the package); (c) approver registration
  numbers are set by the service only (no UI); (d) rules remain `draft`, so every package carries the DRAFT banner.
- Ledger honesty: the chain detects edits and deletions by anyone who cannot also disable triggers and rewrite `ledger_head`; it is not
  proof against the database owner (hashes are unkeyed). The PDF prints the ledger position fixed at the last sign-off; keeping the live
  head outside the database (e.g. a periodic export) is an operational control not built yet.
- Known limits: share links carry the token in the URL (redacted from the API access log, not from a reverse proxy's); the share package
  still shows the sign-off ledger position and signer e-mails; a withheld (validator-failed) PDF is not itself ledgered.

## Phase 3 (2026-10-10): done, tag `sprint-3-gate`
- Built: revision lineage (child revision on upload to a frozen revision; systems copied with confirmations; unchanged confirmed spaces
  carried and ledgered), dependency graph (all 24 rules), revision diff (GUID else name + centroid), stale set, reasoning trace verified
  against graph edges, cross-rule re-run with per-input conflicts, synthetic Rev B -> C golden pair, run persistence, diff confirmation
  bound to the diff hash, freeze with an input fingerprint (`live_inputs_hash`), revision screen + Playwright (10 e2e tests).
- Review: round 1 found 2 blockers + 5 should-fix (frozen results rewritten by a re-run; freeze with stale results; conflict
  misattribution; diff confirm not bound to what was seen; run TOCTOU; sibling children; client-made children), all fixed (0011).
  Round 2 found 3 blockers (snapshot mix in the run load, lock order at freeze, hash collisions) + hash coverage + guard whitelist +
  confirm function exposed + hash oracle + `created_from_sha256` writable, all fixed (0012). **The round-2 fixes had no third review**;
  they are covered by `tests/rls/test_pg_signoff.py` (last section) and `tests/rls/test_pg_lineage.py`. Not reproduced with two live
  sessions: the freeze/edit and run/freeze races (argued from lock order).
- Known limits: reasoning traces list the stale results only; carried confirmations keep the parent's values for matched spaces
  within tolerance (the discarded new-model values are in the ledger); untagged systems are diffed by id.

## Phase 2.5 (2026-10-10): done, tag `sprint-2.5-gate`
- Built: local auth config (port 3100, mail rate limit, public signup off), Supabase magic-link sign-in, `/me`, `/revisions`, role in the
  header, protected routes, IFC/DXF upload (content sniffing, 50 MiB cap, Supabase Storage under the firm, ingest + health), building part per
  system (mixed-use runs per part when every part is allowed; any refused part refuses the run), Playwright sign-in -> upload -> Gate 1 -> cited
  DRAFT report (9 tests).
- Review: round 1 found 1 blocker (a small DXF could run the parser for hours) + 6 should-fix; round 2 found 2 blockers (the sandbox's OUTPUT was
  unbounded; path spellings skipped the upload guard) + 5 should-fix. All of them were fixed afterwards (sandbox with output cap and self-applied
  limits, DXF complexity budget, guard for every path spelling, concurrency cap, JWKS expiry, hash check on a storage conflict, migration 0009).
  **The round-2 fixes had no third review** (two rounds is the maximum); they are covered by `tests/ingest/test_sandbox_budget.py`,
  `tests/api/test_upload_guard.py` and the RLS tests.
- Known limits: orphaned storage objects when ingest fails after the store; co-designers can grief a known file hash (409) within a firm; IFC
  geometry cost is bounded only by the sandbox limits; non-Linux hosts have no rlimits; the web token is in localStorage (Supabase default).
- Gate: `scripts/ci.sh` green on a fresh clone (1329 + 251 RLS/API + 9 e2e + 15 web + 134 golden), GitHub CI green, tag at `83f4b03`.

## Recovery 2026-10-09
- **What happened:** the work was moved to a new device. The GitHub repo `mridungeorge/MEP_AI` holds ONE snapshot commit of
  the old worktree (under `mep-duct-fab/`). Git history, tags and branches did not survive.
- **What was lost:** every commit before the snapshot (no `git blame`/`git log`, no per-round review history, no
  sign-off trailer history); all tags; all branches; the local `.git/config` (hooks path, user) and untracked/ignored
  files (`.venv`, `node_modules`, the local Supabase volumes); file modes (executable bits on 14 shebang scripts, restored
  in the index); and the **unfinished Sprint 2 finish** (listed under "Sprint 2 finish" below), which was not in the snapshot.
  Rule files carry their own `reviewed_*` fields, so no sign-off state was lost; all 24 rules are still `draft`.
- **Old tag/commit map (HISTORICAL ONLY, these commits no longer exist in this repo):** `sprint-0` and `sprint-1` = `c0a62ee`,
  `sprint-1-gate` = `8e3e0a9`, `sprint-2-gate` = `ca34a40`. Do not treat them as verified baselines here.
- **New baselines:** `restore-2026-10-09` = the flatten commit `25b9fd2` (snapshot moved to the repo root, nothing else
  changed). `sprint-2-gate-restored` = the first commit whose clean `git archive` passed `scripts/ci.sh` in the devcontainer
  (Linux, Python 3.12, nested Docker Supabase). `scripts/check_signoff_changes.py --base-tag` compares with the newest
  `*-gate` or `restore-*` tag that is an ancestor of HEAD and fails closed if none exists (`ci.yml` uses it).
- **Gate run (2026-10-09, clean archive, devcontainer):** ruff clean, mypy strict clean, 24 rules valid, 1197 engine/rules/
  ingest/API/skills tests (last known 1184; +13 are the new base-tag tests), 181 RLS, 128 golden passed + 2 skipped
  (the refused-run cases "refused runs produce no report"; whether they were skipped before is unknown), web 11 vitest,
  review_db selftest. GitHub CI is still unrun (no remote CI evidence).
- **Fixes made for the gate (not environment-only):** 6 ruff findings in the duct-fab files (4 unused `noqa`, `Callable`
  from `collections.abc`, `check=False`); executable bit restored on shebang scripts (git index). Env notes: run the gate on a
  clean checkout, not the Windows bind mount (it makes every file look executable); run `uv` as the `vscode` user.
  `pnpm install` run as `vscode` twice left out the `@supabase/cli-linux-x64` package (cause not found); the install
  done as root had it, and copying that `node_modules` into the clean checkout worked.
- **Host note:** all checks run in the devcontainer. The Stop hook and the post-edit lint now run there too
  (`.claude/hooks/devcontainer_exec.py`; the stop gate blocks if the container cannot be reached). The host Python is not used.

## Sprint 2 finish (2026-10-09, after the recovery)
- **Gate (2026-10-09):** tag `sprint-2-gate` = `dca8eb0`. `scripts/ci.sh` ran green, with `set -e`, on a fresh `git clone` of that commit in the
  devcontainer (Linux, Python 3.12, local Supabase in Docker): ruff, mypy strict, 24 rules, sign-off check against `restore-2026-10-09`,
  web typecheck + 15 vitest, 1251 engine/rules/ingest/API/hook tests, 210 RLS + Postgres API tests, 5 Playwright e2e, review_db selftest,
  128 golden (+2 skipped: the refused-run cases). Last known before the loss: 1184 / 181 / 128. GitHub CI has not run. `tests/skills`
  (235, duct-fab) is not part of `ci.sh` yet (needs cadquery, see docs/skills/duct-fab.md); it passed separately in the container.
Rebuilt because the snapshot did not hold it. Reviewed in two rounds by the adversarial reviewer (round 1: no blockers, ten
should-fix; round 2: no blockers, four should-fix, all fixed; the fixes in the last commit before the tag have had no third review).
- **Hooks:** every hook that matches Bash also matches PowerShell (`Bash|PowerShell`); the sign-off guard reads a command two ways
  (backslash deleted for bash, backslash as a path separator for Windows) and knows the PowerShell and .NET write verbs;
  `-EncodedCommand` to a PowerShell invocation is blocked; the shared-database guard also blocks any `supabase_db*` container and
  `PGPORT`. `tests/rules/test_hooks_powershell.py` runs both guards on blocked and benign commands (a crashing hook fails the
  benign ones). Still tripwires, not locks: string concatenation and variable indirection get through.
- **Sign-off base:** `scripts/check_signoff_changes.py --base-tag` (newest `*-gate` or `restore-*` tag that is an ancestor of
  HEAD, fail closed with none) runs in `ci.sh` and in the CI `signoff` job on every push. **Open:** anyone who can push a tag with such a
  name moves the base forward and empties the diff. Needs GitHub tag protection (a ruleset on `*-gate` and `restore-*`) before CI is relied on.
- **Database:** migration 0005 adds `system.tag`; `gate1_confirm(kind, ids, revision)` now writes the ledger row in the same
  transaction and checks the rows belong to the revision it ledgers; a client may keep a provenance label only while the value
  and unit it describes are unchanged. `apps/api/mep/api/pg.py` implements both repositories as the signed-in user
  (`set local role authenticated` + JWT claims), so RLS and the triggers apply; an Excel import is the one write that is
  'extracted', so it goes through the service connection after the designer check, scoped to the user's firm and revision.
- **Confirmation:** one transaction for all kinds; each row names the etag (hash of its confirmable columns) the designer was shown and a
  changed row is refused; Gate 1 inputs get the schedule's checks (name in the edition, pint unit conversion, system type domain);
  an edit withdraws the confirmation and keeps `extracted` on untouched values; the IFC GUID is never client-writable.
- **Auth:** `mep/api/auth.py` verifies HS256 tokens (exp, aud, sub required) and reads firm and role from `app_user`; the public
  Supabase demo secret is refused unless `MEP_ALLOW_DEMO_JWT_SECRET=1`.
- **Rule assignment:** `engine/assignment.py` assigns a system the selected rules whose own YAML names its type, from the CONFIRMED
  `system_type` input; rules naming no system type are listed as unassigned in the report.
- **UI + e2e:** Confirm for project facts, building parts, spaces and schedule rows (designer only; the server enforces it and
  ledgers it), report view with the DRAFT banner, citations, causes and unassigned rules. Playwright (`pnpm --filter @mep/web run e2e`) runs a real Next build,
  the real API on the local Supabase and Chromium: IFC -> health score -> confirm everything -> cited DRAFT report, and for each of
  project facts / building part / space / schedule row one unconfirmed item blocks the run (API 409 with the engine's code) until confirmed.
  The web app is pinned to TypeScript 5.9 (Next 14 cannot load TypeScript 7) and `@/` is aliased in `next.config.mjs`.
- **Known limits:** the e2e uses one representative row per kind and a single designer (no checker path in the browser); the schedule
  has no UI to create a system (import or API); `etag` ignores `project.building_class` (the run requires building parts anyway);
  the web token lives in localStorage.

## Gate record
- 2026-10-07: **local Linux gate passed, GitHub CI pending.** `scripts/ci.sh` ran green inside the devcontainer
  (Linux, Python 3.12.11, nested Docker for Supabase) against a clean `git archive` of the commit tagged `sprint-0`
  and `sprint-1` (ruff, mypy strict, 24 rules, 801 rule/engine/db tests, 134 RLS tests, 18 golden, review_db selftest).
  Windows/3.14 results were not used for this gate.
- Sprint 0 and Sprint 1 were developed and verified together and were committed as ONE commit; both tags point at it.
- The Linux gate found two real problems the Windows runs could not: the devcontainer base image's expired yarn apt key
  (fixed with `.devcontainer/Dockerfile`) and scripts with a shebang but no executable bit in git (fixed in the index);
  `git archive` applied Windows CRLF, so `.gitattributes` now forces LF.
- Merged locally on `main`; no remote exists yet.

## Part 1 (2026-10-07): steps 2-4 done, reviewed
- Sign-off: `checked_by`/`checked_on` (non-approving first-pass check) and `reviewer_registration_no` added; approved needs
  all three reviewer fields. Detection is parse-based (`scripts/signoff_guard.py`), plus a Bash hook and a pre-commit
  tripwire; these are tripwires, not locks (see engine-conventions section 7). Rules are all still `draft`.
- Gate granularity: `adoption.yaml` decides the edition only; Class 2/4 carve-outs are `rules/applicability.yaml` +
  `applicability.py` (validated on load, fail closed). NSW Class 2 on NCC 2022 stays refused. UNREACHABLE combinations are
  listed in `docs/engineer-review/index.md` and printed by `validate_rules.py`. Ledger kinds: `jurisdiction_override`,
  `applicability_override`
- Golden: seven synthetic projects with `meta.yaml`; test output says SYNTHETIC; `scripts/release_gate.py` + hooks + CI
  block release claims while zero real projects exist (`--labels` and `--scan` run in CI; `--check` on non-sprint tags)
- Adversarial review of steps 2-4 found 2 blockers (hook bypasses, placeholder metadata counted as real) and should-fix
  items; all fixed
- Confirming review found one more blocker (Edit hook skipped the check on an empty `new_string`, so deleting a `#` could
  activate a commented sign-off; a rename escaped the commit diff filter). Fixed: check always runs for rule files, fails
  closed on any error or unmatched edit, Windows path aliases and symlinks resolved, `ACMRT` diff filter, `--no-verify`
  and hooks-path changes blocked in Bash, agents cannot flip a golden `meta.yaml` to real, `SYN-` case closed.
- Open: CI step comparing sign-off fields to the merge base; ci.yml tag condition is only an approximation of
  `^sprint-\d+$`; claim-scan regex has known gaps. Local Windows run is green (635 rules/golden tests, ci.sh).
  The Linux/3.12 gate must be re-run on the final commit (an earlier commit passed).

## Sprint 2 (prove the data): built, review and Linux gate pending
- Building parts: `ProjectFacts.building_class` is a class (text, one part) or a list of `BuildingPart(class, storeys, area_m2)`;
  `Subject.part` names the part (required when there is more than one). Applicability runs per part and names the
  refused part; ANY refused part refuses the run (conservative). The report keeps `building_class` as text for one part
  and says `mixed` + `building_parts` otherwise. Migration 0004 `building_part` (RLS, freezes with results/frozen revision).
- Ledger: when BOTH the edition check and the applicability check refuse a run, a `run_refused` record (project,
  revision, reasons, refused_by, UTC timestamp) is written; a rejected override of such a refusal is recorded too; a
  failed write refuses the run as `ledger_unavailable`. Tested.
- IFC (`ingest/ifc.py`): IfcSpace -> GUID, name, area, use, storey, ceiling void; header `originating_system` -> profile
  `ingest/profiles/{revit,archicad,sketchup,unknown}.yaml` (only sketchup is `verified`; revit/archicad are `unverified`
  because no real export is available). Fixtures are public buildingSMART files (CC BY 4.0, `tests/fixtures/ifc/SOURCES.md`).
- Health score (`ingest/health.py`, policy `ingest/health.yaml`: weights and a 70% minimum are product policy): percent + fix
  list; below the minimum it recommends requesting a clean IFC/DXF.
- DXF (`ingest/dxf.py`, ezdxf): closed LWPOLYLINE on space layers -> spaces; unknown units -> no areas (never guesses a
  scale). DWG refused. PDF (`ingest/pdf.py`): vision output goes ONLY to `extraction` evidence (provenance extracted); the
  vision callable is injected, none is wired. Evidence/spaces are stored by `ingest/store.py` (one transaction).
- Schedule: Excel template per edition built from the rules' inputs (`docs/templates/`, `scripts/build_schedule_template.py`),
  reader refuses formulas/macros/hidden/over-limit, units via the engine unit grammar; API `/revisions/{id}/schedule/*`;
  `system_input` table (unit required on every number, provenance, confirmation).
- Gate 1: clients may only write provenance `default`; confirmation is the security-definer `gate1_confirm()` (designer
  only, own firm); an edit withdraws it. The engine refuses `extracted` (`extracted_inputs`) and unconfirmed
  (`gate1_required`, `InputValue.confirmed=False`) values. API `apps/api/mep/api/gate1.py` (`run-rules` returns 409 with the
  engine code); UI `apps/web` (Next.js 14, tsc and vitest green; pages NOT rendered or run against a live API).
- Measured accuracy: `docs/ingest-accuracy.md` (generated, CI-checked). IFC 4/4 on 2 files / 1 exporter (SketchUp); DXF is
  SYNTHETIC; PDF and Excel designers' sheets NOT MEASURED. Small samples: no claim about real exports.
- Local gate: `scripts/ci.sh` green on Windows (1178 + 178 RLS + 128 golden tests, ruff, mypy, web typecheck/tests).
  Linux/3.12 gate for the final Sprint 2 commit: pending.
- Review round 1 (3 blockers): an Edit with an empty old_string skipped the sign-off guard; the run API never passed a
  ledger; building parts reached the engine as engineer_confirmed without confirmation. Round 2 (2 blockers): project
  facts (state, edition, climate zone, date) were unconfirmed and writable by any role; Bash and commit hook bypasses
  (bundled short flags on commit, `cd rules` before sed).
  Fixed: parts, project facts, spaces and inputs are confirmed only via `gate1_confirm()` (designer, own firm); an edit,
  reorder or delete withdraws confirmation for every role; Gate 1 data writes are designer-only (RLS); the run endpoint
  needs a ledger (503 without) and a strict audit with no ledger fails closed; the Bash hook normalises quotes and covers
  bundled flags and `cd rules`; hard-linked files are refused by the Edit hook; CI job `signoff` runs
  `check_signoff_changes.py --base origin/main` (needs an `Engineer-Signoff:` trailer; not yet run on GitHub).
- Review round 3 (1 blocker): a symbolic link under rules/ could point at an unguarded signed file and defeat the CI and
  pre-commit sign-off checks. Fixed: symlinks under rules/ are refused by the loader, validate_rules, both modes of
  check_signoff_changes and the Edit hook; the `Engineer-Signoff:` trailer must be on a commit touching that file; approval
  date freezes with the other edition facts; project facts cannot be confirmed incomplete; inserting or moving a building
  part withdraws every part confirmation; the API shows the project facts, derives facts provenance from the recorded
  confirmation and refuses a missing approval date. **These round-3 fixes have NOT had a confirming review** (stopped on
  the user's request to limit token use); the next session should run one before relying on them.
- Round 3 / round 2 items that the 2026-10-09 finish ADDRESSED (details in "Sprint 2 finish"): PowerShell coverage of the hooks;
  CI `signoff` job on pushes to main; confirm-by-id without a version check (now an etag per row); DB-backed repositories and
  system-to-rule assignment; Confirm for building parts and project facts in the UI.
- Still open from before: the Bash/PowerShell hooks remain text heuristics (a post-command diff check would be stronger);
  the DB guards decide "client" by `current_user in (anon, authenticated)`, so a login role that inherits authenticated
  without `set role` is not covered (not reachable via PostgREST); `space`/`system`/`equipment` writes (0001 policies) are
  still open to checker/approver (the new provenance trigger limits what they can label, not whether they can write);
  the DXF extents check is skipped when the bounding box fails and label/polyline counts are uncapped; an override
  together with an unknown rule is not audited; `invalid_data` reasons include exception text.
- Decisions for the engineer: (1) a mixed-use run refuses wholesale if any part is refused: should allowed parts run?
  (2) system_input `dimensionless` unit convention; (3) pilot-firm Revit/Archicad exports needed to verify profiles and
  measure real accuracy; (4) the real PDF vision model/renderer to wire (pypdf cannot rasterise pages).
- Not built (deferred): sign-in screen and token issuing (the API verifies Supabase-style HS256 tokens; the web app reads one
  from localStorage `mep_access_token`), multi-part mapping (a system cannot yet name its building part, so a project with
  more than one part refuses to run), ceiling-void sources in the profiles, legacy POLYLINE/HATCH in DXF, IFC zip, an upload
  endpoint for IFC/DXF (ingest runs through `store_ingest`; the e2e seeds it that way).

## Tags (what each points at, and what passed the Linux/3.12 gate)
- `sprint-0` and `sprint-1` both point at `c0a62ee`: this commit **passed** the Linux gate (earlier run, 2026-10-07).
- `sprint-1-gate` points at `8e3e0a9` (the Part 1 fixes): this commit **passed** the Linux gate (930 + 134 + 128 tests,
  ruff, review_db selftest). It is the reference for "last Linux-verified commit"; GitHub CI is still pending.

## Deferred from Part 1 (kept open)
- CI step comparing sign-off/check fields with the merge base
- `ci.yml` release-tag condition only approximates `^sprint-\d+$`
- release-claim scan regex gaps
- validate injected adoption/applicability data at the engine boundary
- (moved INTO Sprint 2 scope: ledger record when both jurisdiction and applicability refuse a run)

## Local CI
`bash scripts/ci.sh` is green on this machine: ruff, mypy strict (13 engine files), validate_rules (24 rules),
801 rule/engine/db/golden tests, 134 RLS tests on a real local Supabase Postgres, review_db isolation selftest.
Local runs use the system Python 3.14, not the pinned 3.12: Windows blocks launching the uv-created venv here.
The GitHub workflow (Linux, uv, 3.12, locked deps) is unverified until a remote exists. Devcontainer added.

## Sprint 1 (engine): done
- `apps/api/mep/engine/`: units (pint, mandatory units, strict offset temperatures, plain unit text only),
  evaluator (AST whitelist, no eval/exec, length/node/depth limits and a per-call step budget), loader (schema,
  approved needs reviewer fields, retired rules never selected), rule_eval (types, closed enum domains, provenance
  vs the rule's declared provenance, near miss), jurisdiction, runner, report, ledger, adoption
- E: rule selection intersects `rules/adoption.yaml` by state, edition, approval date and building class.
  Refused: unknown/unverified/not-adopted/replaced editions, not yet in force, past transition, TAS/NSW Class 2/4
  on NCC 2022 (different code). Approver-only override with a real reason is written to `ledger_event` before any
  rule runs (fails closed; not written when the run is refused for another reason). Tests: NT, QLD, VIC, ACT
  transition, NSW, TAS, unknown state
- F: every report carries "DRAFT RULES: NOT ENGINEER-APPROVED" unless every rule used is approved (also on an
  empty run); the PDF has it on every page; report lists `unassigned_rules` (selected but run on no subject)
- G: state regulator pages checked (QLD first). QLD NCC 2025 start 2027-05-01 regulator-verified; no official QLD page
  states the NCC 2022 commercial Section J start, so QLD NCC 2022 stays unverified (refused). NT regulator: NCC 2025
  does not apply; NCC 2019 Section J for commercial. WA NCC 2025, TAS and VIC NCC 2022 commercial dates remain
  ABCB-only. Per-edition status and citations in `rules/adoption.yaml`
- Extracted values refused anywhere in a run (rule 10); `default` provenance is unconfirmed; project facts must be
  confirmed because they choose the edition; subjects cannot supply project facts; climate zone 1-8; building
  class must be in the rules' declared domain
- Fuzz: ~9,000 generated expressions plus hypothesis fuzz of `evaluate_rule` over all 24 rules; no escape
- Tests: the 24 rules' own cases run on the real engine; 6 synthetic golden projects with hand derivations
- Review round 6 (zero blockers) should-fixes: all pint caches trimmed with a test that fails without the trim;
  dimensionless names refused inside compound units; temperatures below absolute zero refused; logarithmic units
  unsupported; `scripts/threshold_diff.py` uses the safe unit parser; total input cap; validate_rules reads utf-8
- Review round 5 fixes: units are built from pint objects name by name (pint never parses unit text: its
  preprocessor words and power towers cannot be reached); conversion factors bounded and overflow refused; request read
  once into exact-typed copies; whole numbers bounded; evaluation cap 5000; pint caches trimmed
- Review round 4 fixes: unit text is now an allow-list grammar (NAME, one single-digit ^exponent, * or /) shared by the
  engine, the rule loader (declared units) and `validate_rules.py`; request snapshotted into exact-typed plain objects;
  text/size/total-evaluation caps; duplicate rule ids refused; unknown declared units are a `rule_error`
- Review round 3 fixes: unit exponents can no longer hang pint; negative physical quantities refused; request shape/size
  validated; one canonical building-class list; rules checked before any ledger write; engineer-confirmed facts only
  choose the edition; golden derivations now cover near-miss flips and unassigned rules independently
- Review round 1-2 findings fixed (missing units, building class, provenance, PDF escaping, ledger transaction,
  evaluator comparisons, retired rules, class domain, step budget, ledger ordering, coverage list)

## Sprint 1 acceptance (BUILD_PROMPT)
Fuzz finds no escape: met. All rule tests pass on the engine: met. Goldens match exactly: met on SYNTHETIC projects
only (the 5 pilot-firm projects are outstanding). Zero results without citation: met (tested). Cited report
JSON + PDF: met.

## Next
- Engine review rounds are done; next is a decision on the Linux/3.12 CI proof, then Sprint 1 merges
- Engineer review of all 24 drafts and every typed threshold (`docs/engineer-review/`); values were typed by agents
- Private GitHub repo to prove CI on Linux/3.12; then close Sprint 0 and allow Sprint 1 merges

## Blockers
- Engineer advisor to review drafts; 5 golden projects from a pilot firm
- GitHub remote (Linux/3.12 CI proof)

## Decisions needed from the engineer
- NSW variant of the 2022 electric heating rule applies to Class 2, but adoption.yaml refuses NSW Class 2/4 on NCC
  2022 (they follow BASIX): one of the two is wrong
- Mixed-use buildings: `building_class` is one value per project, so a Class 2/4 part inside a Class 5 building in
  TAS/NSW is not caught by the jurisdiction gate; needs a per-subject class or an engineer ruling
- CZ8 for Table J6D10 (no column in either edition); 20-30 degC pipe gap; 2025 Class 2 time-switch reading

## Engine contract not yet implemented (the runner has no DB access yet)
- Hold `select ... for share` on the project row for the whole run; run only on frozen revisions or mark results
  stale; every service writer at READ COMMITTED; freeze code takes the revision lock first
- The override `role` is a string the caller asserts: the API layer must take it from the authenticated user, never
  from a request body (Sprint 4 API)
- Never read provenance from client-written jsonb (`system.controls`, `equipment.attributes`) or project facts

## Deferred (decided or accepted)
- Ledger hash chain and audit triggers on service-role rewrites/deletes: Sprint 4
- Gate sign-off state machine (gate order, frozen-before-sign, one signer per gate): Sprint 4
- Gate-1 confirmation as a first-class DB record
- Loader runs only the JSON schema; the stricter `validate_rules.py` checks run in CI (the engine now also guards
  non-boolean conditions, unknown filter operators and evaluation budgets at run time)
- `PG17 MAINTAIN` privilege and default privileges for future supabase_admin-owned tables: review for prod

## Known engine limits (documented, not fixed)
- pint and the unit cache are not thread-safe: run one worker per process (or add a lock) before an API exists
- `str.upper()` folds look-alike Unicode letters in the state code (applied consistently; not an edition mix)

## Open issues
- ABCB 2022 adoption page is "as at 22 Sep 2023": stale; WA/TAS/VIC regulator pages for some dates not found
- J6 encoder uncertainties: sub-clause letters inferred; 2022 J6D3(3)(b) vs Guide label; Specs 46/47 and Part F6 not
  fetched; 2022 rules 5a/5b can overlap; second-pass notes may sit close to ABCB wording (check before publishing)
- Goldens: refusal derivations repeat engine strings; only 11 results over 6 projects; no golden for a rejected
  override, a missing unit or offset temperatures (covered in unit tests)
- `==` between a dimensionless quantity and a bare number, and ordered text comparison, are allowed by the evaluator

## Open TODO_FROM_SOURCE
- NCC2025-J6D10-elec-resistance-heat: density.8 (CZ8), both bands

## Sprint 0 summary (kept for reference)
- Migrations 0001-0003 with RLS, composite firm FKs, edition per project, frozen-revision and race guards (row
  locks proven by two-session tests), 134 RLS tests incl. attacks per firm and role
- Rule schema with unit-bearing thresholds, validator with pint dimension analysis, 24 draft rules, dual-encoding
  second pass (`docs/threshold-diff.md`: 178 cells match, 0 disagreements, 0 swaps, 4 unresolved CZ8)
- Engineer review pack `docs/engineer-review/`, disposable review database `scripts/review_db.py` + hook


## Phase 7 engineering features (2026-10-12)
- 7.1 Fix hypotheses: `engine/fix_search.py` (smallest single-input change found by bisection/choice, only engineer-confirmed inputs, never climate zone/class/system type),
  `api/fixes.py` + 0037 (`fix_scratch` records, client writes revoked, ledger `fix_proposed`/`fix_applied`). An option is `accepted` only if no dependent rule gets worse
  AND no dependent rule still FAILs. Apply recomputes now, writes an UNCONFIRMED hand-entered value, then marks the scratch applied; the run refuses until Gate 1 again.
  UI: `FixPanel` under each FAIL on the revision screen, every option labelled "Hypothesis: verify".
- 7.2 Performance Solution: 0038, `api/performance.py`, `/projects/.../performance`. Flag only when the stored FAIL is reproduced by today's inputs and no option is
  accepted; otherwise `stale`. Pathway per result (DTS/PERFORMANCE_SOLUTION), engineer evidence append-only with provenance `engineer_supplied` (never read by the engine),
  JSON/XLSX starting package with the draft-rules banner; spreadsheet cells neutralise formulas and control characters.
- 7.3/7.5 `api/services.py` + 0039: duct/fitting/terminal schedule (lengths via pint to mm), ceiling-void check (deepest duct + insulation both faces + firm clearance;
  only a CONFIRMED void can be CLEAR), firm setting `void_clearance_mm`, quantities with CSV/XLSX. Results are CLASH/CLEAR/NO DATA, never PASS/FAIL.
- 7.4 Clash-lite: `mep/clash.py` (axis-aligned boxes, IFC read in an isolated child process with CPU/memory/time limits, caps on models/elements/clashes),
  BCF 2.1 export, warnings only. UI: `/projects/.../services`.
- Review: round 1 (12 findings) fixed; round 2 (6 findings): claim-then-write order fixed, stale flag from unconfirmed inputs fixed, rlimits moved into the child, clash
  early-stop, BCF guid/author cleaned. NOT re-reviewed after round 2: those last fixes. Known limits: integer-only inputs are not distinguished (spec has no integer type);
  MAX_MODELS count is not transactional; `accepted` allows NEEDS_JUDGEMENT on a dependent rule; clash detection is O(ducts x elements) with early stop only.

## Decisions made overnight
- Migrations 0037 and 0039 were edited in place after being pushed (round-1 fixes): no deployed database had applied them (staging is not deployed); local DBs are reset.
- Unanswered Docker clean-up question: continued with small local batches and GitHub CI as the full check.
- 8.1 sizing: friction rate default 1.0 Pa/m, roughness 0.09 mm, increment 50 mm, min size 150 mm, target aspect 2, max aspect 4 are firm-changeable DESIGN-PRACTICE defaults (not from a standard); velocity limits have NO default ("NO LIMIT SET").
- 8.4 multi-storey is a separate skill name (`space-envelope-stack`) so the single-storey card, validator and expected manifests are untouched; its validator reuses space-envelope's DXF checks per storey.
- 8.5 firm templates are applied as an ADDITIONAL file (`<stem>.firm.dxf`, role `dxf_firm`) after the skill released its validated output, only when an independent re-read confirms every original entity is kept on its mapped layer; this leaves the strict per-skill validators (colours/layers) untouched. Placeholders: {{MARK}} {{SKILL}} {{PROJECT}} {{TITLE}} {{DRAWING_NO}} {{REVISION}} {{DATE}} {{DRAWN_BY}} {{FIRM}} {{NOTES}}.
- 8.6 3D preview: `web-ifc` 0.0.66 (WASM copied to public/wasm by scripts/copy-wasm.mjs), own small WebGL orbit viewer; the mesh/camera maths is unit-tested (lib/ifcmesh.test.ts) and `next build` passes, but the WebGL/WASM path itself has NO automated browser test (needs a real GPU context): check by hand on the review pack walkthrough. Models above 3,000,000 triangles are refused.
