# Review and sign-off (Gates 2 and 3)

Open the Review screen for a frozen revision. The progress line shows how many results are approved and which gates are signed.

## Gate 2: the checker

1. Clean passes may be approved together, but only after a spot check. Click "Draw the spot-check sample", examine each highlighted line yourself, then click "Approve the remaining clean passes".
2. If you find a problem in the sample, every clean pass must be reviewed one by one.
3. Every other class needs its own decision with a written reason: FAIL, NEEDS_JUDGEMENT, NOT_APPLICABLE, outcome changed since the previous revision, and near miss.
4. To accept a FAIL you choose a reason category and write an explanation. If you choose "rule disputed", the rule also lands on the list for the engineers to review. You may instead reject the line and say what goes back to the designer.
5. Click "Sign Gate 2".

## Gate 3: the approver

1. Acknowledge each accepted FAIL separately, with a note.
2. Type your registration number and click "Sign Gate 3". The number is checked against what the firm administrator submitted and a platform administrator verified on the register (NER, RPEQ or state). If it does not match, signing is refused.
3. The package then shows accepted FAILs first, the three sign-offs and the ledger line. The ledger is hash-chained so tampering is detectable. "Open the PDF" produces the report.

## Independence notice

Gates must normally be held by three different people. If your firm allows one person to hold more than one gate (small-firm mode), a red notice shows in the header and on this screen, and everything produced is stamped NOT INDEPENDENTLY CHECKED.

## What the app will refuse

- Signing Gate 2 before the revision is frozen.
- A checker who froze the revision, or who reviewed no line personally.
- Bulk-approving FAILs, or accepting a FAIL without a reason category and explanation.
- Signing Gate 3 before Gate 2, before every accepted FAIL is acknowledged, or with a registration number the register check does not accept.
- Any attempt by an agent to approve. Agents raise flags and risks only; a human decides.
