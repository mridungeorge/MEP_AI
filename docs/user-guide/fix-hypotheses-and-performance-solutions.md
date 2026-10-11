# Fix hypotheses and Performance Solutions

## Fix hypotheses

Under each FAIL on the revision screen, open the fix panel. The app searches for the smallest single change to an input that an engineer decides. Every option is labelled "Hypothesis: verify".

- An option is accepted only if no dependent rule gets worse and no dependent rule still fails. Otherwise it is shown as Withdrawn with the conflicts.
- "Apply as an unconfirmed change" writes the value as an unconfirmed hand entry. You then confirm it at Gate 1 and re-run. Nothing is final until that happens.
- The panel also shows the rule's own suggestions, untested.
- If no option exists, the panel says so and points to the Performance Solution pathway.

## Performance Solution pathway

Open the Performance Solution page for a revision. It lists failed results where no tested option works. Such a result is flagged only if today's inputs reproduce the stored FAIL; otherwise it is marked stale.

1. For each result choose a pathway: DTS (deemed-to-satisfy) or Performance Solution.
2. Record the evidence you supply: a title, the tool (for example the name of your simulation software), figures as a list with name, value and unit, and optionally a file.
3. Download the starting data package (XLSX or JSON). It carries the draft-rules banner.

Evidence is stored append-only, marked engineer supplied, and is never read by the rule engine. It does not turn a FAIL into a PASS.

## What the app will refuse

- Suggesting a change to the climate zone, building class or system type.
- Using any unconfirmed input in a fix search.
- Applying a withdrawn option.
- Treating recorded evidence as a result.
- Anything from an assistant that says a fix is fine or compliant: such wording is filtered out.
