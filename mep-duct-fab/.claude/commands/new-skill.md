---
description: Scaffold a new CAD/BIM skill with spec card, builder and validator
argument-hint: <skill-name> <one-line purpose>
---

Create skill: $ARGUMENTS

1. Use the `skill-builder` subagent to create `skills/<skill-name>/` from `skills/_template/`.
2. Use the `validator-author` subagent to write its validator and fixtures.
3. Run the golden example end to end and confirm the validator passes and two bad fixtures fail.
4. Register the skill in `apps/api/mep/skills_runner/registry.py`.
5. Report the spec card fields and how to run it.
