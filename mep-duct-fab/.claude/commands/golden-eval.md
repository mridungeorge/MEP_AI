---
description: Run golden-project evals and explain any differences
argument-hint: [project name, optional]
---

Run golden evals for: $ARGUMENTS (all projects if blank)

1. `uv run pytest tests/golden -q $ARGUMENTS`
2. For every mismatch, show: rule ID, expected vs actual result, the inputs, and the likely cause (rule logic, units, extraction, edition/state selection).
3. Do not change expected outputs to make tests pass. If you believe an expected output is wrong, say so and stop for my decision.
4. Summarise pass rate per project.
