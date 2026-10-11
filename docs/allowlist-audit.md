# Security allowlist audit

Every suppressed finding in `.trivyignore` (and any `pip-audit --ignore-vuln` or `pnpm audit` exception) must have a row here
before it merges. A row says why the finding does not apply or cannot yet be fixed, who accepted it, and when it is re-checked.
An entry without a row, or past its review date, is a defect.

| ID | Scanner | Component and image | Why it is accepted | Accepted by | Accepted on | Review by |
|----|---------|---------------------|--------------------|-------------|-------------|-----------|
| (none) | | | | | | |

Current state: the allowlist is empty. CI fails on any HIGH or CRITICAL finding that has a fix available
(`ignore-unfixed: true` in `security.yml`; unfixed findings are reviewed at the weekly scheduled run).
