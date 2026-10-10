"""Fix hypotheses for failed results.

For a FAIL the engine's own evaluation finds the smallest single-input changes that make that rule pass (`mep.engine.fix_search`); the cross-rule re-run then says
whether each one breaks any other rule that reads the same input. Every option is labelled "Hypothesis: verify". A designer may record one as a scratch change
and apply it, but ONLY if, when it is applied, it passes every dependent rule (the engine is run again at that moment: the stored row is a record, not a
permission). Applying writes the value as a hand-entered, unconfirmed input: it must be confirmed at Gate 1, the rules re-run and the gates signed like any
other change. Nothing here decides compliance.
"""
import json
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from mep.api.gate1 import _project
from mep.api.revisions import DESIGNER_ROLES, Graph, Pack, Repo, _err, _subjects
from mep.api.revisions import User as RevUser
from mep.engine.cross_rule import _outcomes, cross_rule_rerun
from mep.engine.fix_search import FixOption, candidate_fixes
from mep.engine.model import InputValue, Outcome, Provenance
from mep.engine.rule_eval import evaluate_rule
from mep.engine.runner import _inputs_for

router = APIRouter()


def get_dsn() -> str:
    raise HTTPException(status_code=503, detail="fix hypotheses are not configured")


Dsn = Annotated[str, Depends(get_dsn)]


def options_for(repo: Any, pack: Any, graph: Any, revision_id: UUID, firm_id: UUID, subject_id: str, rule_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[FixOption]]:
    """(the stored result, the options with their cross-rule verdicts, the raw options)."""
    results = [r for r in repo.current_results(revision_id, firm_id) if r["subject_id"] == subject_id and r["rule_id"] == rule_id]
    if not results:
        raise _err(404, "not_found", "no such current result")
    result = results[0]
    rule = pack.rules.get(rule_id)
    subjects, data = _subjects(repo, revision_id, firm_id)
    subject = subjects.get(subject_id)
    if rule is None or subject is None:
        raise _err(404, "not_found", "no such system or rule")
    out: list[dict[str, Any]] = []
    raw: list[FixOption] = []
    if result["outcome"] == "FAIL":
        project = _project(data["project"], revision_id, firm_id)
        live = evaluate_rule(rule, _inputs_for(subject, project, rule)).outcome
        result = {**result, "live_outcome": live.value}
        raw = candidate_fixes(rule, _inputs_for(subject, project, rule))
        for o in raw:
            change = {o.input_name: InputValue(o.to_value, o.unit, Provenance.ENGINEER_CONFIRMED)}
            cross = cross_rule_rerun(subject=subject, project=project, changes=change, pack=pack, graph=graph, target_rule=rule_id)
            after = _outcomes(subject, project, pack, sorted({*cross.dependents, rule_id}), change)
            still_failing = [r for r, oc in after.items() if oc == Outcome.FAIL]
            out.append({"id": o.id, "label": o.label, "kind": o.kind, "input": o.input_name, "from": o.from_value, "to": o.to_value, "unit": o.unit,
                        "target_after": "PASS", "dependent_rules": cross.dependents, "accepted": not cross.withdrawn and not still_failing,
                        "still_failing": still_failing,
                        "moves": [{"rule_id": m.rule_id, "before": m.before.value, "after": m.after.value} for m in cross.moves],
                        "conflicts": [c.describe() for c in cross.conflicts] + [f"{r} would still FAIL" for r in still_failing]})
    return result, out, raw


@router.get("/revisions/{revision_id}/results/{subject_id}/{rule_id}/fixes")
def fixes(revision_id: UUID, subject_id: str, rule_id: str, user: RevUser, repo: Repo, pack: Pack, graph: Graph) -> dict[str, Any]:
    result, options, _ = options_for(repo, pack, graph, revision_id, user.firm_id, subject_id, rule_id)
    return {"subject_id": subject_id, "rule_id": rule_id, "outcome": result["outcome"], "label": "Hypothesis: verify", "options": options,
            "rule_text_hypotheses": result.get("fix_hypotheses", []),
            "note": "Each option is the smallest single change the rule engine finds that makes this rule pass. It is accepted only if every other rule that "
                    "reads the same input still passes. Applying it creates an UNCONFIRMED change that goes through Gate 1 and the gates again."}


def _write(dsn: str, sql: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        return conn.execute(sql, args).fetchone()


@router.post("/revisions/{revision_id}/results/{subject_id}/{rule_id}/fixes/{option_id}/scratch")
def make_scratch(revision_id: UUID, subject_id: str, rule_id: str, option_id: str, user: RevUser, repo: Repo, pack: Pack, graph: Graph, dsn: Dsn) -> dict[str, Any]:
    if user.role not in DESIGNER_ROLES:
        raise _err(403, "forbidden", "only a designer records a fix")
    info = repo.revision_info(revision_id, user.firm_id)
    if info is None:
        raise _err(404, "not_found", "revision not found")
    if info.get("frozen"):
        raise _err(409, "revision_frozen", "a frozen revision cannot change: a fix goes into a new architect revision")
    _, options, _ = options_for(repo, pack, graph, revision_id, user.firm_id, subject_id, rule_id)
    chosen = next((o for o in options if o["id"] == option_id), None)
    if chosen is None:
        raise _err(404, "not_found", "no such option (the results may have changed: reload)")
    row = _write(dsn, "insert into fix_scratch (firm_id, revision_id, subject_id, rule_id, option_id, option, cross_rule, accepted, created_by)"
                      " values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s) returning id",
                 (user.firm_id, revision_id, subject_id, rule_id, option_id, json.dumps(chosen), json.dumps({"moves": chosen["moves"], "conflicts": chosen["conflicts"],
                                                                                                           "dependent_rules": chosen["dependent_rules"]}),
                  chosen["accepted"], user.user_id))
    assert row is not None
    return {"scratch_id": str(row["id"]), "accepted": chosen["accepted"], "option": chosen}


@router.post("/revisions/{revision_id}/fix-scratch/{scratch_id}/apply")
def apply_scratch(revision_id: UUID, scratch_id: UUID, user: RevUser, repo: Repo, pack: Pack, graph: Graph, dsn: Dsn) -> dict[str, Any]:
    if user.role not in DESIGNER_ROLES:
        raise _err(403, "forbidden", "only a designer applies a fix")
    info = repo.revision_info(revision_id, user.firm_id)
    if info is None:
        raise _err(404, "not_found", "revision not found")
    if info.get("frozen"):
        raise _err(409, "revision_frozen", "a frozen revision cannot change: a fix goes into a new architect revision")
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        sc = conn.execute("select subject_id, rule_id, option_id, status from fix_scratch where id = %s and revision_id = %s and firm_id = %s",
                          (scratch_id, revision_id, user.firm_id)).fetchone()
    if sc is None:
        raise _err(404, "not_found", "no such scratch change")
    if sc["status"] == "applied":
        raise _err(409, "already_applied", "this scratch change was already applied")
    # the engine decides again NOW, from the current inputs: the stored verdict is not trusted
    _, options, _ = options_for(repo, pack, graph, revision_id, user.firm_id, sc["subject_id"], sc["rule_id"])
    chosen = next((o for o in options if o["id"] == sc["option_id"]), None)
    if chosen is None:
        raise _err(409, "stale", "the inputs or results changed since this was proposed: propose it again")
    if not chosen["accepted"]:
        raise _err(409, "not_accepted", "this change breaks another rule that reads the same input: " + "; ".join(chosen["conflicts"] or ["it does not make the rule pass"]))
    view = repo.get_gate1_view(revision_id, user.firm_id)
    row = next((i for i in (view or {}).get("inputs", []) if i.get("system") == sc["subject_id"] and i.get("name") == chosen["input"]), None)
    if row is None:
        raise _err(409, "not_editable", "that input is not a hand-editable row of this system")
    try:                                                       # claim the scratch change first: a second concurrent apply loses here
        claimed = _write(dsn, "update fix_scratch set status = 'applied', applied_at = now() where id = %s and status = 'proposed' returning id", (scratch_id,))
    except psycopg.errors.Error:
        claimed = None
    if claimed is None:
        raise _err(409, "already_applied", "this scratch change was already applied")
    repo.upsert_input(revision_id, user.firm_id, row["id"], {"name": chosen["input"], "system": sc["subject_id"], "value": chosen["to"], "unit": chosen["unit"]})
    return {"applied": True, "needs": "Confirm the changed value at Gate 1, re-run the rules, and take the revision through the gates again.",
            "input": chosen["input"], "value": chosen["to"]}
