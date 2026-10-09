---
description: Run a build sprint from BUILD_PROMPT.md (plan, tests first, build, review, status)
argument-hint: <sprint number 0-4>
---

Run Sprint $ARGUMENTS of MEP Co-pilot.

1. Read `CLAUDE.md`, `docs/STATUS.md`, and the Sprint $ARGUMENTS section of `BUILD_PROMPT.md`.
2. Confirm the previous sprint's acceptance checks pass. If not, stop and report what's failing.
3. Write a plan: files to create or change, tests to write, acceptance checks. Show it before editing.
4. Write tests first for engine, diff and review work.
5. Build in small steps; run tests after each step.
6. Delegate: rules → `rule-encoder`, validators → `validator-author`, skills → `skill-builder`.
7. Before finishing, run the `adversarial-reviewer` subagent and fix blockers.
8. Update `docs/STATUS.md`: sprint, done, next, blockers, open `TODO_FROM_SOURCE` items.
