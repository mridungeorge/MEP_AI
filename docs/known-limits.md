# Known limits

Collected from `docs/STATUS.md` (Phases 2.5 to 8). Each entry says why it is not fixed.
Reason codes: LICENCE (needs a licensed standard), SECRET (needs a key or account), PAID (needs a paid account or host),
OWNER (needs an owner or legal decision), LARGE (too large for a safe small fix), DESIGN (deliberate trade-off), REVIEW (small, but needs its own tested change and review).

This list is not a compliance statement. All rules remain `draft`.

## Attempted in Phase 10.3 and left

| Limit | Why left |
|---|---|
| Near-miss firm default (`firm.near_miss_default`) is stored and editable but the engine does not use it. | LARGE/REVIEW. The engine reads the near-miss fraction from each rule's YAML (`rule_eval._near_miss`). Applying a firm value means a new engine parameter passed through `runner.py`, the run endpoints in `server.py`/`pg_revisions.py` and the stored results, plus a decision on whether it overrides or only fills rules that declare none. That changes engine behaviour and golden outputs, so it needs an owner decision and tests first. |
| Validator blind spots in space-envelope (VOLUMEUNIT prefix, `IfcQuantityArea` unit overrides, property set shared by two spaces, DXF TEXT style / oblique / width factor / mirror flags, geometry inside arrow-head blocks). | DESIGN. STATUS lists these as deliberately left and gives no check specification, so adding checks would be guessing. Each needs a spec and a negative test fixture first. |

## Phase 2.5 to 5

- Orphaned storage objects when ingest fails after the store (LARGE: needs a cleanup job).
- Co-designers can grief a known file hash (409) within a firm (OWNER).
- IFC geometry cost bounded only by sandbox limits; non-Linux hosts have no rlimits (DESIGN).
- Web token is in localStorage (Supabase default) (DESIGN).
- Reasoning traces list the stale results only; carried confirmations keep the parent's values for matched spaces (DESIGN).
- Untagged systems are diffed by id (DESIGN).
- Share links carry the token in the URL on legacy paths of reverse proxies; the share package shows the ledger position and signer e-mails (OWNER).
- A withheld (validator-failed) PDF is not itself ledgered (REVIEW).
- Ledger is not proof against the database owner; the live head is not exported outside the database (an operational control; `scripts/ledger_anchor.py` is the start of it).
- A one-person firm needs `small_firm` mode; the mode stamp on a review is read in a separate statement (REVIEW).
- Throttles and the vision reader are per process or per replica (LARGE: needs shared state).
- Vision runs in the request thread, up to about 150 s per file, with no per-firm quota (LARGE).
- Worker sandbox is rlimits only in LocalExecutor (development). The Docker executor needs a Docker host; Railway cannot run it (PAID).
- Dispatcher holds the full service DSN and the Docker socket; other local users can read the job work directory (LARGE: dedicated role, 0700 parent).
- API trusts the worker's manifest (re-hashes files, does not re-run the validator); unbounded JSON bodies on card-preview/confirm-card (REVIEW).
- Raw tool arguments go into the hash-chained ledger and cannot be purged (OWNER: data retention, see `docs/data-retention.md`).
- Notes and clarifying questions can be added to a frozen revision (marked post-freeze) (DESIGN).
- `run_skill(use_draft)` by an agent builds without a human confirmation step (OWNER).
- Explanation and reply filters cannot check numbers or truth in prose; a denylist cannot catch every paraphrase (DESIGN).
- An agent message that fails still counts toward the throttle (REVIEW).
- "Add as a space" from evidence stores default/manual provenance; areas are assumed m2 (DESIGN).
- duct-fab needs cadquery, which the API image does not carry (PAID/LARGE: a worker host).

## Phase 6

- No e-mail-verified self-signup; people join by invitation only (OWNER).
- Platform-admin console is minimal; billing has no invoices UI (LARGE).
- Sentry, Resend and Stripe paths are tested with fakes only (SECRET: `SENTRY_DSN`, `RESEND_API_KEY`, `STRIPE_*`).
- Two simultaneous Stripe checkouts can both charge (REVIEW, needs Stripe test account).
- Permanent ledger holds the registration number and platform-admin note only as a hash (DESIGN).
- Invitation e-mail has no rate limit (REVIEW; `ratelimit.py` exists and may cover it).
- `/terms` and `/privacy` are placeholders marked LEGAL REVIEW REQUIRED (OWNER).
- Round-2 fixes (0036, billing adoption/ordering) had no third review.

## Phase 7

- Integer-only inputs are not distinguished (the spec has no integer type) (OWNER).
- MAX_MODELS count is not transactional (REVIEW).
- A fix option is `accepted` even if a dependent rule goes to NEEDS_JUDGEMENT (OWNER).
- Clash detection is O(ducts x elements) with early stop only; boxes are axis-aligned (LARGE).
- Round-2 fixes had no re-review.

## Phase 8

- Sizing defaults (friction rate, roughness, increment, minimum size, aspect ratios) are design-practice defaults, not from a standard; velocity limits have no default (OWNER).
- Balance tolerance for hvac-dxf is a card field (default 1 %), not read from the firm (REVIEW).
- ifc-mep has no tee fittings; branches need an element at the junction (LARGE).
- 3D preview WebGL/WASM path has no automated browser test (needs a GPU context; check by hand).
- Round-2 fixes had no re-review.

## Phase 9 and licences

- AS 1668.2, AS/NZS 3000, 3008 and AS 4254 are not encoded; licensed-standard slots are empty (LICENCE).
- Revit and Archicad export profiles are `unverified` until pilot-firm exports exist (OWNER).
- `NCC2025-J6D10-elec-resistance-heat` density.8 (CZ8) is `TODO_FROM_SOURCE` (needs the official ABCB text).
- ABCB 2022 adoption page is stale; some WA/TAS/VIC regulator dates not found.
- All rules are `draft`; sign-off by a human engineer is required before any package loses its DRAFT banner.
- `deploy/api/Dockerfile` and the web and worker images were first built in CI only; local Docker builds hang Docker Desktop here.

## Engine

- pint and its unit cache are not thread-safe: one worker per process.
- `str.upper()` folds look-alike Unicode letters in the state code (applied consistently).
- TAS/NSW is not caught by the jurisdiction gate without a per-subject class or an engineer ruling.
- The evaluator allows `==` between a dimensionless quantity and a bare number, and ordered text comparison.
- Golden coverage: 11 results over 6 projects; no golden for a rejected override, a missing unit or offset temperatures.


## Added by the consolidated review (Phase 10.10): found, not fixed overnight

None of these is a blocker; each is a decision or a larger change. Severity as reported by the reviewers.

- (major) Fix search treats every engineer-confirmed number as a design lever, including measured plant facts; rules should declare which inputs are levers (`fix_lever`), and conjunctive checks need multi-input options.
- (major) The "Performance Solution pathway likely" flag looks at one nearest-boundary option; a better single-input value may exist that it does not try.
- (minor) Performance and fixes endpoints recompute the fix search twice per FAIL on each load (cost grows with the number of FAILs); concurrent applies of different fixes on one system are not serialised.
- (major) Clash-lite upload has no process-wide concurrency cap and its model-count check can race; the load test was not run.
- (minor) hvac-dxf/ifc-mep airflow balance gates use terminals and tolerance supplied by the card (only the duct schedule is server-filled); the firm-sheet templates are not part of the confirmed card digest.
- (minor) Commissioning sheets/PDFs have no validator of their own; measured values carry no unit column (they are L/s by the column header).
- (minor) A verified registration number cannot be revoked (no revoke function); an approver demoted later keeps the number on file.
- (minor) Anchors link backwards only: deleting the newest anchor file is not noticed unless the store enforces object lock; nothing schedules the anchoring yet (a cron entry is the operator's).
- (major, owner decision) The web app is on Next 14.2; ten advisories are fixed only in Next >= 15.5.24 (a major upgrade). They are listed in `docs/allowlist-audit.md` and are the top security item before any real firm is onboarded.
- (minor) The rate limiter is per process and in memory; add an edge limiter in production. Trivy and pip-audit steps in CI have never run to completion on a real image.
- (minor, round 2) A dependent rule reading another input that went back to `extracted` still raises inside the cross-rule re-run (fails closed with a 500, not a message); an unknown Stripe plan id sets unlimited projects and plan changes in the Billing Portal keep the old limit (plan comes from checkout metadata); title blocks with nested inserts only in block definitions or paperspace, and layer maps differing only in case, are not refused at upload; the per-address rate ceiling trusts the rightmost X-Forwarded-For hop (confirm the edge appends it); `applied_by` has no composite FK.
