# Review pack: everything built in Phases 6 to 10

Prepared at the end of the overnight run. Read `docs/STATUS.md` ("Resume here" and the phase sections) for the working detail. Everything here is a **DRAFT** product: every
rule is a draft and not engineer-approved, there are no real golden projects (7 synthetic), legal pages are placeholders, and prices are Stripe TEST-mode placeholders.

## 1. What exists, and where to find it in the app

Sign in with the demo users (see "Walkthrough" below). The top bar has Projects, Notifications, Standards, Help (and Admin / Billing / Registrations for the right people).

| Phase | Feature | Where in the UI | Notes |
| --- | --- | --- | --- |
| 6 | Firm admin: invitations, roles, deactivation, firm settings, title block + layer standard upload | **Admin** | settings include signer mode, spot-check sample, void clearance |
| 6 | Approver registration admin (platform admin verifies) | **Admin** (submit) and **Registrations** (platform admin) | every change in the ledger |
| 6 | Projects dashboard, revision history, who signed what | **Projects**, then a project | filters by state, edition, status |
| 6 | E-mail notifications (Resend; fake sender in tests) and preferences | **Notifications** | no key = nothing is sent |
| 6 | Observability: `/healthz`, `/readyz`, JSON logs, Sentry hooks | API endpoints; `docs/runbooks/observability.md` | no DSN wired |
| 6 | Backup / restore / export / retire / purge | `scripts/dataops.py`; `docs/runbooks/backup-restore.md` | tested restore into a scratch database |
| 6 | Billing, Stripe TEST mode only | **Billing** | project creation is gated by subscription status |
| 6 | Terms, privacy placeholders | footer links | "LEGAL REVIEW REQUIRED" |
| 7 | Fix hypotheses on every FAIL, applied as an unconfirmed change | **Revision** page, results table, "Fix hypotheses" | always labelled "Hypothesis: verify" |
| 7 | Performance Solution flag, pathway per result, engineer evidence, starting package JSON/XLSX | project row, **Performance Solution** | evidence is never a rule result |
| 7 | Services schedule, ceiling-void check (firm clearance), quantities CSV/XLSX | **Services** | CLASH / CLEAR / NO DATA only |
| 7 | Clash-lite against electrical, hydraulic, fire IFC + BCF 2.1 export | **Services** | warnings only |
| 8 | Duct sizing (equal friction, SI), balance check, reducer spec-card drafts | **Services** (Sizing) | velocity limits are the firm's; none by default |
| 8 | Drafting skills: `hvac-dxf`, `ifc-mep`, `space-envelope-stack` (plus the earlier `duct-fab`, `space-envelope`) | **Drafting** | each output passes an independent validator first |
| 8 | Firm templates applied to every DXF (`*.firm.dxf`) | Admin uploads; **Drafting** results | additional file; original untouched |
| 8 | 3D preview (web-ifc) of the architect model and released IFC | project row, **3D preview** | no automated browser test of the WebGL path |
| 9 | NSW design compliance declaration DRAFT (class 2, 3, 9c) | **Review** page, after Gate 3 | states nothing, lodges nothing |
| 9 | Commissioning sheets (XLSX/PDF) and re-import with tolerance flags | **Review** page, after Gate 3 | readings are records, not results |
| 9 | Licensed-standard slots (AS 1668.2, AS/NZS 3000, 3008, AS 4254) | **Standards** | all empty, "licence required" |
| 9 | Revit connector options | `docs/integrations/revit.md` | no code |
| 10 | Rate limiting, security headers/CSP, audit and image scanning in CI, CSRF review | `docs/security.md`; `.github/workflows/` | see section 5 |
| 10 | Ledger anchoring and verification | `scripts/ledger_anchor.py` | write-once directory store; bucket wiring needs credentials |
| 10 | Accessibility checks, onboarding wizard, empty states, inline help | web app | see `docs/known-limits.md` |
| 10 | User guide, help page | **Help**, `docs/user-guide/` | |
| 10 | Welcome and pricing pages, in-app feedback | `/welcome`, `/pricing`, "Send feedback" | "DRAFT COPY" |
| 10 | Changelog, release notes, retention description | `CHANGELOG.md`, `docs/release-notes/`, `docs/data-retention.md` | retention is a technical description, LEGAL REVIEW REQUIRED |

## 2. Guided walkthrough on the seeded demo data (about 45 minutes)

Preparation: follow `docs/demo-script.md` ("Before you start") so the synthetic office project exists and you have designer, checker and approver sessions.

1. (5 min) Run the first 15-minute run sheet in `docs/demo-script.md` up to the signed package. Say the DRAFT-rules paragraph first.
2. (5 min) **Fix hypotheses**: on a FAIL row open "Fix hypotheses". Read the option, note it is "Hypothesis: verify", press Apply: the value becomes unconfirmed and Run rules refuses until Gate 1 is done again.
3. (5 min) **Services**: add two ducts with airflows and a terminal in a space with a confirmed ceiling void; read the ceiling-void table (CLEAR needs a confirmed void), the sizing table and the airflow balance. Upload `tests/fixtures/ifc/bsi-hvac-ifc4.ifc` as a "fire" model to see clash-lite warnings; export BCF.
4. (5 min) **Performance Solution**: on a failed result set the pathway, add a piece of evidence, download the XLSX package. Open it: the DRAFT-rules banner is on the first sheet.
5. (10 min) **Drafting**: upload the architect IFC on **3D preview**; run `hvac-dxf` using the draft from the Services page (the server fills the sizing schedule); run `ifc-mep`; open the 3D preview with both. Break something on purpose (change a duct size in the card) and watch the validator refuse.
6. (5 min) After Gate 3: open the **NSW declaration** draft (set the project state to NSW and a class 2 building part in the seed first) and the **commissioning** sheet; fill two measured values and import them with a tolerance you choose.
7. (5 min) **Standards**, **Help**, **Send feedback**, **Admin > feedback**; **/pricing**.
8. (5 min) Operations: `python scripts/ledger_anchor.py export --store anchors/` then `verify`; show that editing an anchor file or a ledger row is reported.

## 3. What is synthetic

All projects, rooms, systems and airflows; the 7 golden projects; every rule's thresholds as the product sees them (DRAFT); prices and plans; the demo users and
registration numbers (`DEMO-0001`); the IFC fixtures (buildingSMART samples); performance numbers (small dev container).

## 4. What needs a licence or an account that this run did not have

- AS 1668.2, AS/NZS 3000, AS/NZS 3008 and AS 4254: slots exist and are empty (`rules/licensed/`). A person sets `licence_held` only after the firm holds the licensed copy.
- Stripe live mode, Resend, Sentry DSNs, an object-lock bucket for anchors, Autodesk developer account for a Revit connector: none used.
- NCC thresholds not yet taken from the official ABCB text stay `TODO_FROM_SOURCE` (see the rule files).

## 5. Known limits

See `docs/known-limits.md` (compiled in Phase 10) and `docs/STATUS.md`. The main ones: the WebGL 3D preview has no automated browser test; clash-lite uses axis-aligned boxes; `ifc-mep`
has no tee fittings; the `.firm.dxf` is an additional file, not a replacement; sizing defaults (1 Pa/m, 0.09 mm, 50 mm step) are design-practice choices the firm can change and
velocity limits have no default; the load test (locust) was written but not run against a deployed stack; full local `scripts/ci.sh` was not run on this machine (low RAM), GitHub CI is the full check.

## 6. Reviews and what was not re-reviewed

- Phase 6: two rounds; round-2 fixes not re-reviewed.
- Phase 7: two rounds; round-2 fixes (claim-then-write, stale flag, rlimits, BCF cleaning) not re-reviewed.
- Phase 8: two rounds; round-2 fixes (validator relationship checks, per-card schedule subset, agent digest, firm-sheet signature/lock) not re-reviewed.
- Phase 9: see `docs/STATUS.md` ("Phase 9 review").
- Phase 10: the consolidated review (10.10) record is in `docs/STATUS.md`.
- Never reviewed by anyone but the automated adversarial reviewer: everything above. No human engineer has reviewed any rule.

## 7. Skipped and blocked overnight

See the "Skipped overnight" and "Blocked overnight" lists at the end of `docs/STATUS.md`.

## 8. Decisions made overnight and open decisions for you

Decisions are recorded in `docs/STATUS.md` ("Decisions made overnight"). Open for you:

1. Docker clean-up (prune images, `.wslconfig` memory cap) so the full `scripts/ci.sh` can run locally.
2. Sizing defaults and who owns velocity limits per firm.
3. Retention period, backup retention and jurisdiction wording (needs a lawyer).
4. Which standards licences the firm will buy first, and who may set `licence_held`.
5. Whether to build the Revit add-in (option A or B in `docs/integrations/revit.md`).
6. Real golden projects and an engineer to approve the first rules (nothing is releasable without them).
