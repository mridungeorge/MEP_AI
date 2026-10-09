---
description: Encode a new draft rule from an official source
argument-hint: <edition> <clause> <source URL or "excerpt follows">
---

Use the `rule-encoder` subagent to encode: $ARGUMENTS

Then:
- Run `python3 scripts/validate_rules.py` on the new file and `pytest tests/rules -k <RULE_ID>`.
- Check the dependency graph picks up its `depends_on` inputs.
- Report the file, logic summary, remaining `TODO_FROM_SOURCE` values, and questions for the engineer.
- Remind me the rule stays `draft` until an engineer reviews it.
