# Runtime agents

Three agents run on the Claude Agent SDK (`apps/api/mep/agents/`). The model is a guest: **the tool layer is the safety boundary** and it lives on the server.

| Agent | Tools (besides reading) | Started by |
|---|---|---|
| designer | `fill_spec_card`, `ask_clarifying_question`, `run_skill`, `request_rule_run`, `propose_fix_hypothesis`, `explain_result` | a designer |
| adversarial_checker | `raise_flag` | any signed-in role |
| compliance_risk | `raise_risk` | any signed-in role |

Reading tools: `read_results`, `read_result`, `read_gate_status`, `read_notes`.

Every call goes through `ToolLayer.call`, which refuses (and logs in `agent_call`, which feeds the hash-chained ledger) anything that is not: a tool the AGENT has;
allowed for the HUMAN's role (an agent never has more rights than the person who started it); valid arguments; a revision of that person's firm, open where the tool
changes anything; within the rate and open-note limits. Agents leave NOTES (`agent_note`: draft cards, questions, fix hypotheses, flags, risks, explanations)
that people read and close; there is no tool that writes a rule result, a review, a confirmation, a sign-off, a share link or a file, no shell, no web, no
file access (the SDK's built-in tools are switched off and `can_use_tool` denies everything that is not ours). `run_skill` and `request_rule_run` use the same doors
as a person and refuse in the same ways (validator gate; Gate 1). `explain_result` text is post-filtered to the one result it is about: it may cite only that
result's rule id and name only that result's outcome, and the authoritative facts are attached next to it by the server. Fix hypotheses are stored as
"Hypothesis: verify" and may not claim compliance.

Needs `ANTHROPIC_API_KEY` and `pip install 'mep-copilot[agents]'` (the Docker image installs the extra). Without them the API answers 503 "agents are not configured"
and the UI says so. Tests replace the model with `ScriptedRuntime`, which drives the same tool layer.

Vision (PDF drawings): pages are rendered in a sandbox (pdfium), each page is read by a vision model, and the result is written to the `extraction` evidence
table only (provenance `extracted`); no space is created, and the rule engine never reads it. A designer can add a candidate as a space they enter by hand.
