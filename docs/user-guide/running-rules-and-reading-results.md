# Running rules and reading results

## Running

"Run rules" evaluates the project's NCC edition rule pack against your confirmed inputs. Every result is produced by the rule engine from rule files; no AI is involved in any outcome.

If a rule needs a value that is missing, the run reports exactly what is missing instead of guessing. If you changed inputs after a previous run, results from that run are stale; run again before freezing.

## Reading the report

Every report and PDF carries a red banner: DRAFT RULES: NOT ENGINEER-APPROVED. Each line shows:

- the subject (a space or system),
- the rule and its clause reference, and the rule version,
- the outcome,
- how the figures were reached, with units.

Outcomes:

- PASS: the encoded rule is satisfied by the confirmed inputs.
- FAIL: the encoded rule is not satisfied. A FAIL can be accepted later at Gate 2, with a reason; it is never silently dropped.
- NEEDS_JUDGEMENT: the rule needs an engineer's decision, or a unit did not match.
- NOT_APPLICABLE: the rule's conditions do not apply here.
- Near miss: a PASS that is close to its threshold. It gets its own review decision at Gate 2.

Clause numbers and thresholds shown come from the rule files. The app never reproduces standards wording; it shows references only. A threshold the rule author could not take from the official source is stored as TODO_FROM_SOURCE and the rule cannot give a result on it.

## What the app will refuse

- A run with unconfirmed inputs.
- A run on a frozen revision whose results already exist; frozen means unchangeable.
- Mixing NCC editions in a single evaluation.
- Returning any outcome from an assistant or agent. Agents may leave notes only.
