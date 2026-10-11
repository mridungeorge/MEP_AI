import os

os.chdir("C:/Users/GeorgeMridun/MEP_AI")


def patch(path, edits):
    s = open(path, encoding="utf-8", newline="").read()
    for a, b in edits:
        assert a in s, (path, a[:80])
        s = s.replace(a, b, 1)
    open(path, "w", encoding="utf-8", newline="").write(s)


patch("tests/api/test_ratelimit.py", [("    from starlette.testclient import TestClient\n", "")])

# fixes: dependent rules that need judgement are listed, not called passing; extracted inputs do not 500
patch("apps/api/mep/api/fixes.py", [
    ('            still_failing = [r for r, oc in after.items() if oc == Outcome.FAIL]\n', '            still_failing = [r for r, oc in after.items() if oc == Outcome.FAIL]\n            needs_judgement = [r for r, oc in after.items() if oc == Outcome.NEEDS_JUDGEMENT]\n'),
    ('                        "still_failing": still_failing,', '                        "still_failing": still_failing, "needs_judgement": needs_judgement,'),
    ('"It is accepted only if every other rule that "\n                    "reads the same input still passes.', '"It is accepted only if no other rule that "\n                    "reads the same input fails or gets worse; rules that would then need a person\'s judgement are listed.'),
    ('        live_inputs = _inputs_for(subject, project, rule)\n        ev = evaluate_rule(rule, live_inputs)\n', '        live_inputs = _inputs_for(subject, project, rule)\n        try:\n            ev = evaluate_rule(rule, live_inputs)\n        except ExtractedInputError:                                  # an input went back to \'extracted\' after the run: the stored FAIL is stale, not an error\n            return {**result, "live_outcome": "STALE_INPUTS_CHANGED"}, [], []\n'),
    ("from mep.engine.model import InputValue, Outcome, Provenance\n", "from mep.engine.model import InputValue, Outcome, Provenance\n"),
])
s = open("apps/api/mep/api/fixes.py", encoding="utf-8", newline="").read()
if "ExtractedInputError" not in s.split("def options_for")[0]:
    s = s.replace("from mep.engine.rule_eval import evaluate_rule\n", "from mep.engine.rule_eval import ExtractedInputError, evaluate_rule\n", 1)
open("apps/api/mep/api/fixes.py", "w", encoding="utf-8", newline="").write(s)

patch("apps/web/lib/types.ts", [("dependent_rules: string[]; accepted: boolean;", "dependent_rules: string[]; accepted: boolean; needs_judgement?: string[];")])
patch("apps/web/components/FixPanel.tsx", [
    ("{o.accepted ? <span>Passes every dependent rule ({o.dependent_rules.length}).</span>",
     "{o.accepted ? <span>Breaks no dependent rule ({o.dependent_rules.length}){o.needs_judgement && o.needs_judgement.length > 0 ? `; ${o.needs_judgement.length} would then need a person's judgement: ${o.needs_judgement.join(\", \")}` : \"\"}.</span>"),
])

# declaration panel shows the basis and both banners
patch("apps/web/lib/types.ts", [("applies_to: { state: string; classes: string[] };\n  building", "applies_to: { state: string; classes: string[]; basis?: string }; rules_banner?: string | null; independence_notice?: string | null;\n  building")])
patch("apps/web/components/DeclarationPanel.tsx", [
    ("          <p>{d.building.address}: NSW, class {d.applies_to.classes.join(\", \")}, {d.building.ncc_edition}.</p>",
     "          {d.rules_banner && <p role=\"status\" style={{ background: \"#fef3c7\", padding: 8, fontWeight: 700 }}>{d.rules_banner}</p>}\n          {d.independence_notice && <p role=\"status\" style={{ background: \"#fee2e2\", padding: 8, fontWeight: 700 }}>{d.independence_notice}</p>}\n          <p>{d.building.address}: NSW, class {d.applies_to.classes.join(\", \")}, {d.building.ncc_edition}.</p>\n          {d.applies_to.basis && <p><em>{d.applies_to.basis}</em></p>}"),
])
# honest copy
patch("apps/web/app/welcome/page.tsx", [("From the architect&apos;s model to a signed compliance package for mechanical services: inputs confirmed by an engineer, rules applied the same way every time, every change traced.",
                                          "From the architect&apos;s model to a traceable, signed checking package for mechanical services (today a demonstration: every rule is a draft and not engineer-approved): inputs confirmed by an engineer, rules applied the same way every time, every change traced.")])
print("ok")
