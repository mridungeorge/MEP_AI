# Revisions and diffs

## Freezing

"Freeze revision" is the designer's sign-off for Gate 1. A frozen revision can never change, and the freeze is written to the audit log. Freeze only after the run is complete and the results are your own (not carried over).

## A new architect revision

When the architect re-issues the model, upload it on the frozen revision under "Upload a new architect revision" and give it a label (B, C, ...). The app creates the new revision as a child of the old one; the old one is untouched.

## The diff

Open "Diff & results" for the new revision. It lists what changed (for example an area changed or a room was added) and what carried over unchanged. Results carried from the parent are labelled "carried from the previous revision; not re-run yet".

"Why these results are stale" shows a trace: change, then input, then rule, then result. It is built only from the rules' declared dependencies. Nothing is guessed.

## Steps for a new revision

1. Open Gate 1 for the new revision and confirm the inputs that changed.
2. Return to the diff and click "Confirm this diff".
3. Click "Re-run rules".
4. Freeze the revision.

## What the app will refuse

- Editing or re-running a frozen revision.
- Confirming the diff while changed inputs are still pending confirmation.
- Re-running rules before the diff is confirmed.
- Freezing when the results are only carried from the parent.
- Rule conflicts between revisions are listed under "Rule conflicts"; they need your attention before freezing.
