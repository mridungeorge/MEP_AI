# Demo script: 15 minutes with a mechanical engineer

**Audience**: a practising mechanical services engineer. **Goal**: they see that the tool is careful, traceable and checkable, and they
tell you what is missing. Do not sell accuracy. Sell *traceability and discipline*.

**Before you start** (5 minutes, not part of the 15): `docs/runbooks/deploy.md` done and `scripts/seed_demo.py` run. Three browser
profiles (or three private windows), signed in as **designer**, **checker**, **approver**. Files ready on the desktop:
`docs/demo-assets/office-rev-a.dxf` and `office-rev-b.dxf`. Have `docs/engineer-review/` open in a tab (the rule review packs).

## The one thing to say about DRAFT rules (say it first, and again at the report)

> "Every rule in here is a **draft**. I encoded the clause references and the logic, but no engineer has signed any of them off yet.
> That is why every report and PDF has a red *DRAFT RULES: NOT ENGINEER-APPROVED* banner and why this is a demonstration, not a
> compliance tool. Approving a rule is a human act, done in the repository by a named engineer; nothing in this application, and no AI,
> can approve a rule or decide a result. Part of why I'm here is to get *you* to read the review packs and tell me where the draft is wrong."

If asked "is it accurate?": "The engine only evaluates the rule as written. Whether the rule as written matches the clause is exactly the
question I need your help on. The inputs and the arithmetic are deterministic and traceable; the encoding is what is still unreviewed."

## Run sheet

| Min | Who | Screen | Do | Say |
|---|---|---|---|---|
| 0:00 | designer | Revisions list | Show the project "1 Demo Street, Melbourne VIC (synthetic office)". Open **Open Gate 1**. | "Synthetic project, Class 5 office, VIC, NCC 2025. Nothing real. The header shows who I am and my role: the server decides what I may do, not the page." |
| 1:00 | designer | Gate 1, upload | Upload `office-rev-a.dxf`. | "The architect's plan. A DXF here, IFC works too. The file's *content* is checked, not its name." |
| 2:00 | designer | Ingest health | Point at "Ingest health: N%" and the fixes it suggests. | "Before anything is checked I want to know how well the model was read. Low score means: add the rooms by hand. Everything read from the file is marked **extracted**: the engine refuses extracted values until a person confirms them." |
| 3:00 | designer | Gate 1 tables | Fill the **Storey** of each of the four rooms (Level 1). Show the project facts row, the building part, the system schedule (four air handlers already entered). Change nothing else. | "Gate 1 is where an engineer takes responsibility for the inputs. Edit a value and its confirmation is withdrawn." |
| 5:00 | designer | Gate 1 confirm | **Select all unconfirmed**, **Confirm selected**. Try **Run rules** first, before confirming, to show it is blocked and why. | "One unconfirmed row and the run refuses, with the reason." |
| 6:00 | designer | Report | **Run rules**. Show the DRAFT banner, a PASS and a FAIL row with clause references; open the near-miss. | **Say the DRAFT paragraph again.** "Each line cites the clause and the rule version. Fix suggestions, where they appear, are *hypotheses to verify*, never answers." |
| 7:00 | designer | Diff & results | Open **Diff & results**, **Freeze revision**. | "Freezing is the designer's sign-off for Gate 1: this revision can never change now. It is in the audit log." |
| 8:00 | designer | New revision | Upload `office-rev-b.dxf` under **Upload a new architect revision** (label B). | "The architect re-issued the model. The tool makes revision B as a child of A; A is untouched." |
| 9:00 | designer | Diff + trace | Show the diff: *Open plan office* area changed, *Meeting room 2* added, the rest carried over unchanged. Open the **Why these results are stale** trace. | "I have to confirm what changed, not re-do everything. The trace is built only from the rule dependency graph: change, input, rule, result. Nothing is guessed." Open Gate 1 for B, confirm the two changed rooms (storey), come back, **Confirm this diff**, **Re-run rules**, **Freeze revision**. |
| 11:00 | checker | Review and sign-off | **Draw the spot-check sample**; examine the highlighted lines (approve with a reason each); **Approve the remaining clean passes**. The page lists the other classes (FAIL, needs judgement, not applicable, outcome changed, near miss): **every one of those needs its own decision with a reason** before Gate 2 can be signed (the demo project has several; decide them in front of the engineer, it is the point). | "Gate 2 is a different person. Clean passes can be approved together, but only after I've personally examined a random sample. One problem in the sample and every line must be reviewed one by one." |
| 12:00 | checker | A FAIL line | On each FAIL: pick a **reason category**, write the explanation, **Accept this FAIL** (or Reject it and say what you would send back to the designer). Approve the judgement and not-applicable lines with a reason. Then **Sign Gate 2**. | "A FAIL can be accepted, but never silently or in bulk: a reason category, a written explanation, and the approver must acknowledge it separately. If I choose *rule disputed* it also lands on the list of rules for the engineers to review." |
| 13:00 | approver | Acknowledge + sign | **Acknowledge** each accepted FAIL, type the registration number (`DEMO-0001` for the demo) and **Sign Gate 3**. Show the package: accepted FAILs first, the three sign-offs, the ledger line. | "Three different people. The registration number is checked against what the administrator verified on the register. The ledger is hash-chained: tampering is detectable." |
| 14:00 | approver | Share link | **Create share link**, copy it, open it in a private window. | "For the certifier: read-only, expires, every opening is logged, and the token is in the address *fragment*, so it is never sent to a server or logged." Open the PDF. |
| 15:00 | | | Stop. Ask the questions below. | |

If the firm is a one-person practice: say "In small-firm mode one person can hold all three gates, and everything the tool produces is
stamped **NOT INDEPENDENTLY CHECKED**. I'd rather the stamp be honest than the process be impossible."

## Questions to ask them (write the answers down)

1. Open one of the rule review packs. Is the clause reference right? Is the logic what you would apply? (Pick J6D3 economy cycle.)
2. Which input would you never trust from an architect's model? What should the health score look for?
3. Would you accept a FAIL on the strength of a performance solution? What evidence would you need to see attached?
4. What would the certifier want on the first page that is not there?
5. What would stop you using this on a live job tomorrow?

## If something goes wrong

- **Magic link doesn't arrive**: check spam and Supabase > Authentication > Logs; use a second address; the SMTP step in `deploy.md`.
- **"revision_frozen" / 409 messages**: the server is explaining a rule. Read the message aloud; it is the point of the demo.
- **Run rules blocked**: something is unconfirmed; the page lists what.
- **Checker cannot sign**: they must have reviewed at least one line themselves, and the person who froze the revision cannot be the checker.
- **Reset**: the ledger is append-only, so a demo project cannot be deleted. Make a fresh set instead:
  `seed_demo.py --firm-name "Demo Mechanical (synthetic) 2" --designer ... --checker ... --approver ...` with new addresses (plus-addressing works).
- Never demonstrate with a real client's drawings.

## Extended run sheet for Phases 6 to 10 (add 30 to 45 minutes)

Do the 15-minute sheet above first, then, with the same sessions:

| Min | Who | Screen | Do | Say |
|---|---|---|---|---|
| +0 | designer | Revision page, results | On a FAIL open **Fix hypotheses**; press **Apply as an unconfirmed change**; try **Run rules** (refused until Gate 1 is done again). | "The tool finds the smallest single change the rules accept, checks every other rule that reads the same input, and still makes you confirm it. It is a hypothesis, labelled so." |
| +5 | designer | Performance Solution | Set the pathway on a failed result, add one piece of evidence, download the XLSX. | "Evidence you supply is kept with the revision. It is never a rule result and no rule reads it." |
| +10 | designer | Services | Add two ducts with airflows and one terminal; read the ceiling-void table, sizing and airflow balance; upload a fire IFC for clash-lite; export BCF. | "Warnings only. A void that was never confirmed at Gate 1 cannot say CLEAR." |
| +20 | designer | 3D preview, Drafting | Upload the architect IFC; run `hvac-dxf` from the Services draft, then `ifc-mep`; open both in the 3D preview. Change a duct size in the card and run again. | "The server fills the sizing schedule; a drawing that disagrees with it, or an IFC that altered the architect's model, is refused by an independent validator." |
| +30 | approver | Review (after Gate 3) | Open the **NSW declaration** draft (NSW class 2 project only) and the **Commissioning** sheet; import two measured values with a tolerance you choose. | "The draft states nothing and lodges nothing; the practitioner completes it. Measured values are records." |
| +35 | admin | Admin, Standards, Help | Show feedback list, the licensed-standard slots (all "licence required") and the user guide. | "Nothing from AS 1668.2 or the electrical standards is encoded; the slots wait for a licence." |

See `docs/review-pack.md` for the complete list of what exists, what is synthetic and what is not yet reviewed.
