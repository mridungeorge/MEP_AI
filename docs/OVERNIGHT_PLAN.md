OVERNIGHT AUTONOMOUS RUN. I am not available until morning. Never wait for my input.

STEP 0 (do this first): create a recurring scheduled task every 30 minutes (cron "*/30 * * * *")
with this prompt:
  "Overnight resume: if a turn is already running, do nothing. Otherwise read the 'Resume here'
   block in docs/STATUS.md and git status, then continue the overnight run exactly where it
   stopped, following the OVERNIGHT RULES and phase list saved in docs/OVERNIGHT_PLAN.md. If
   STATUS.md says 'OVERNIGHT RUN COMPLETE', delete this scheduled task and stop."
Confirm the task ID.

STEP 1: save this entire prompt verbatim to docs/OVERNIGHT_PLAN.md, commit, push. Every resume
reads it from there. Then start the first phase without a *-gate tag.

======================== OVERNIGHT RULES ========================
1. Checkpoint constantly. After EVERY numbered item: commit, git push origin main, and rewrite the
   "Resume here" block at the top of docs/STATUS.md (phase, item, next action, anything half-done).
   A usage cut must never lose more than one item.
2. Never ask a question. If a decision is needed, choose the most conservative option, record it
   under "Decisions made overnight" in STATUS.md with the reason, and continue.
3. If an item needs a secret, paid account or licensed standard that isn't available: skip it,
   log it under "Skipped overnight", continue.
4. If a phase can't close (blocker after two review rounds, or the same CI failure twice): leave it
   untagged, log under "Blocked overnight", continue with later items that don't depend on it.
5. Phase close: adversarial review (max two rounds) -> scripts/ci.sh green in devcontainer ->
   GitHub CI green -> tag <phase>-gate -> git push origin main --tags. Per item, run targeted tests
   only; full ci.sh and GitHub CI at phase close.
6. Devcontainer only. Rules stay draft. Never: force-push, delete or move tags or branches, rewrite
   history, edit sign-off fields, approve rules, commit secrets, or edit .claude/ settings or hooks
   to get past a block.
7. Do NOT encode any rule from AS 1668.2, AS/NZS 3000, AS/NZS 3008 or AS 4254. Build slots and UI
   that accept them later, with empty packs and a "licence required" state.
8. Do not write legal text (terms, privacy). Placeholders marked "LEGAL REVIEW REQUIRED" only.
9. When all phases are done or nothing more can proceed: write "OVERNIGHT RUN COMPLETE" at the top of
   STATUS.md with a summary (phases tagged, items done, skipped, blocked, decisions made), push,
   and delete the scheduled task.

======================== PHASE 6: product shell (sprint-6-gate) ========================
1. Firm admin: invite users by email, assign roles, deactivate users; firm settings (signer
   independence, near-miss default, spot-check sample size, title block and layer standard upload).
2. Approver registration admin: firm admin submits registration number + register (NER/RPEQ/state);
   a platform-admin role verifies it; every change ledgered. Service script kept only as logged
   break-glass.
3. Projects dashboard: list, filter by state/edition/status, revision history, who signed what.
4. Email notifications (Resend): review requested, changes requested, signed, share link opened;
   per-user preferences. Skip sending if no key; keep the code and tests with a fake sender.
5. Observability: Sentry (API, web, worker) with PII scrubbing; structured JSON logs; /healthz and
   /readyz; uptime check documented. Skip DSN wiring if no key.
6. Data safety: backup and restore runbook + script, tested restore into a scratch local database;
   per-firm export (JSON + files); firm deletion with documented ledger retention rules.
7. Billing in Stripe TEST mode only: per-seat and per-project plans as config, trial, subscription
   status gating project creation, webhook with signature verification. Use Stripe's test fixtures;
   skip live setup.
8. Terms and privacy placeholder pages marked "LEGAL REVIEW REQUIRED".

======================== PHASE 7: engineering features (sprint-7-gate) ========================
1. Fix hypotheses UI: on FAIL, show the rule's deterministic options labelled "Hypothesis: verify";
   applying one creates a scratch change, runs cross-rule re-run, shows conflicts; accepted only
   after it passes every dependent rule and goes through the gates.
2. Performance Solution flag: when DTS fails and no hypothesis passes, flag "Performance Solution
   pathway likely"; export a starting data package (spaces, systems, schedules, failed DTS
   benchmarks) as JSON + XLSX; record engineer-supplied simulation results as evidence, never as
   rule results. Per-result pathway field (DTS / performance solution).
3. Ceiling-void clash: duct depth + insulation vs ceiling void per space, with clearance setting.
4. Cross-service clash-lite: upload electrical, hydraulic and fire IFC; detect overlaps and clearance
   breaches with proposed ducts in the ceiling void; BCF 2.1 export; warnings only.
5. Quantities: duct area by size, fittings, insulation area, terminals per space; CSV/XLSX export.

======================== PHASE 8: drafting (sprint-8-gate) ========================
1. Duct sizing: metric equal-friction sizing (port the OpenMEP approach to SI), velocity limits as
   firm settings, results feed duct-fab spec cards; validator checks airflow balance per system.
2. hvac-dxf skill: duct layout and schematic DXF with firm layers, symbols and title block; every
   duct tagged with size and airflow; validator checks tags match the sizing schedule.
3. ifc-mep skill: write ducts, terminals and equipment into the architect's IFC (IfcOpenShell),
   connected ports, correct storeys; validator checks schema, connectivity and sizes.
4. space-envelope: multi-storey layouts.
5. Firm templates (title blocks, layer standards) applied by every drafting skill.
6. 3D preview in the browser (web-ifc) for IFC outputs and the architect model.
All new skills run in the isolated skill worker and must pass validators before release.

======================== PHASE 9: v3 items needing no licence (sprint-9-gate) ========================
1. NSW design compliance declaration DRAFT for NSW Class 2, 3, 9c projects, prefilled from the signed
   package. Marked "DRAFT: the registered design practitioner must review and lodge on the NSW
   Planning Portal". No lodging.
2. Commissioning sheets: per-terminal design airflow sheets (PDF/XLSX) from the signed package with
   blank measured columns; re-import of measured values with tolerance flags.
3. Licensed-standard readiness: rule-pack slots and UI for AS 1668.2 and electrical, empty packs,
   "licence required" state. No rules encoded.
4. docs/integrations/revit.md: Revit connector options, effort, licensing. No code.

======================== PHASE 10: launch readiness (sprint-10-gate) ========================
1. Security: rate limiting per user and IP on auth, upload, run and share endpoints; security headers
   and CSP on web; CSRF review; dependency scanning (pip-audit, pnpm audit) and container image
   scanning (trivy) in CI, failing on high/critical with a documented allowlist.
2. Ledger anchoring: periodic export of the signed ledger head hash to a separate storage bucket
   (write-once policy) plus a verification command that checks the database chain against the
   anchors. Tested tamper detection.
3. Close remaining known limits from STATUS.md where no secret or licence is needed (validator blind
   spots, small items). List any left.
4. Accessibility: axe checks in Playwright on every main screen; fix to WCAG 2.1 AA; keyboard
   navigation for Gate 1, review and sign-off.
5. Performance: synthetic 200-room, 20-system project; load test (locust) of upload, run, diff and
   report; record timings in docs/performance.md; fix anything over 10 s for a run or 3 s for a page.
6. Onboarding: first-project wizard, empty states, inline help on every gate, sample demo project
   one click away.
7. User guide: docs/user-guide/ (getting started, each gate, revisions, sign-off, share link,
   drafting, limits, what DRAFT rules mean), linked from the app's help menu.
8. Public landing page and pricing page in the web app, with copy marked "DRAFT COPY" and pricing
   read from billing config. In-app feedback button storing feedback in a table.
9. CHANGELOG.md from the gate tags; versioned release notes; data retention policy document.
10. Final consolidated adversarial review across everything since sprint-5-gate (max two rounds);
    record in STATUS.md which fixes were and weren't re-reviewed.

======================== FINAL: review pack ========================
Write docs/review-pack.md: every feature and where to find it in the UI; a 30-60 minute guided
walkthrough on the seeded demo data; what is synthetic; what needs a licence; known limits;
everything not re-reviewed; skipped and blocked items; decisions made overnight; open decisions for
me. Update docs/demo-script.md. Push. Then follow rule 9.
