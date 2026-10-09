"""Run a project's subjects through the selected rules and build the cited report.

Gates, in order (each refuses with RunRefused rather than producing a partial report):
  1. request shape and size, then project facts (closed domains from rules/adoption.yaml)
  2. extracted values (rule 10): any input or project fact with provenance 'extracted' refuses the run
  3. the facts that choose the edition (state, edition, class, date) must be engineer-confirmed
  4. two independent checks: the jurisdiction gate (the NCC EDITION must be confirmed in force for the state and
     approval date: rules/adoption.yaml) and the applicability check (the edition's rule pack must apply to the building
     class: rules/applicability.yaml)
  5. selection: one NCC edition per project, state-matching rules only; a subject may name only selected rules;
     every selected rule's expressions must be well formed (checked before anything is written to the ledger)
  6. an approver may override a refusal of either check with a reason that is written to the ledger first
"""
import dataclasses
import math
from datetime import UTC, date, datetime
from typing import Any

from mep.engine import applicability, jurisdiction
from mep.engine.adoption import load_adoption
from mep.engine.applicability import canonical_classes, load_applicability
from mep.engine.evaluator import EvalError
from mep.engine.loader import Rule, RulePack
from mep.engine.model import (
    BuildingPart,
    InputValue,
    Outcome,
    ProjectFacts,
    Provenance,
    RunRequest,
    Subject,
)
from mep.engine.report import build_report
from mep.engine.rule_eval import (
    Evaluation,
    ExtractedInputError,
    check_rule_expressions,
    evaluate_rule,
)

PROJECT_FACT_INPUTS = ("climate_zone", "building_class")
KNOWN_EDITIONS = ("NCC2022", "NCC2025")
MAX_SUBJECTS = 5000
MAX_PARTS = 50                   # building parts in one project
MAX_STOREYS = 200
MAX_AREA_M2 = 10_000_000
MAX_RULES_PER_SUBJECT = 100
MAX_INPUTS_PER_SUBJECT = 500
MAX_TOTAL_INPUTS = 50000
MAX_EVALUATIONS = 5000          # subjects x rules in one run
MAX_TEXT = 512                   # input text values, units
MAX_ID = 128                     # subject ids, rule ids, user ids
MAX_REASON = 2000
VALUE_TYPES = (bool, int, float, str)
MAX_INT = 10**15                 # whole-number input magnitude (keeps reports serialisable)


class RunRefused(Exception):
    def __init__(self, code: str, reasons: list[str]) -> None:
        super().__init__(f"{code}: " + "; ".join(reasons))
        self.code = code
        self.reasons = reasons


def _copy_request(request: Any) -> RunRequest:
    """Read every attribute of the caller's request exactly once into plain exact-typed dataclasses.

    All later validation and evaluation use only this copy, so a caller's property that answers differently on
    a second read, or a subclass with odd methods, cannot make the validated data differ from the evaluated data.
    Values that are not the expected exact types are kept as they are for the validators to refuse.
    """
    try:
        if type(request) is not RunRequest:
            raise TypeError("request must be a RunRequest")
        p = request.project
        if type(p) is not ProjectFacts:
            raise TypeError("project must be ProjectFacts")
        bc = p.building_class
        if type(bc) is list:
            bc = [BuildingPart(x.building_class, x.storeys, x.area_m2) if type(x) is BuildingPart else x for x in bc]
        project = ProjectFacts(p.state, p.ncc_edition, p.climate_zone, bc, p.approval_date,
                               p.firm_id, p.revision_id, p.facts_provenance, p.climate_zone_provenance)
        raw_subjects = request.subjects
        if type(raw_subjects) is not list:
            raise TypeError("subjects must be a list")
        subjects: list[Any] = []
        for s in raw_subjects:
            if type(s) is not Subject:
                subjects.append(s)
                continue
            sid, rules, inputs = s.id, s.rules, s.inputs
            if type(inputs) is dict:
                inputs = {k: (InputValue(v.value, v.unit, v.provenance, v.confirmed) if type(v) is InputValue else v)
                          for k, v in inputs.items()}
            subjects.append(Subject(sid, list(rules) if type(rules) is list else rules, inputs, s.part))
        ov = request.override
        override = jurisdiction.Override(ov.user_id, ov.role, ov.reason) if type(ov) is jurisdiction.Override else ov
        return RunRequest(project, subjects, override)
    except Exception as exc:
        raise RunRefused("invalid_request", [f"malformed request: {type(exc).__name__}"]) from exc


def _shape_problems(request: Any) -> list[str]:
    """Hostile or malformed request shapes are refused here, never crashed on later."""
    if type(getattr(request, "project", None)) is not ProjectFacts:
        return ["project must be ProjectFacts"]
    subjects = getattr(request, "subjects", None)
    if type(subjects) is not list or not all(type(s) is Subject for s in subjects):
        return ["subjects must be a list of Subject"]
    problems: list[str] = []
    if len(subjects) > MAX_SUBJECTS:
        problems.append(f"too many subjects (max {MAX_SUBJECTS})")
    seen: set[str] = set()
    for s in subjects:
        if type(s.id) is not str or not s.id.strip():
            problems.append("every subject needs a non-empty text id")
            continue
        if s.id in seen:
            problems.append(f"duplicate subject id {s.id!r}")
        seen.add(s.id)
        if len(s.id) > MAX_ID:
            problems.append("subject id too long")
        if type(s.rules) is not list or not all(type(r) is str and len(r) <= MAX_ID for r in s.rules):
            problems.append(f"{s.id}: rules must be a list of rule ids")
        elif len(s.rules) > MAX_RULES_PER_SUBJECT:
            problems.append(f"{s.id}: too many rules (max {MAX_RULES_PER_SUBJECT})")
        elif len(set(s.rules)) != len(s.rules):
            problems.append(f"{s.id}: a rule is listed more than once")
        if type(s.inputs) is not dict or not all(
                type(k) is str and len(k) <= MAX_ID and type(v) is InputValue for k, v in s.inputs.items()):
            problems.append(f"{s.id}: inputs must map names to InputValue")
        elif len(s.inputs) > MAX_INPUTS_PER_SUBJECT:
            problems.append(f"{s.id}: too many inputs (max {MAX_INPUTS_PER_SUBJECT})")
        else:
            problems += [f"{s.id}.{n}: {why}" for n, v in s.inputs.items() if (why := _input_problem(v))]
    if not problems and sum(len(s.rules) for s in subjects) > MAX_EVALUATIONS:
        problems.append(f"too many rule evaluations in one run (max {MAX_EVALUATIONS})")
    if not problems and sum(len(s.inputs) for s in subjects) > MAX_TOTAL_INPUTS:
        problems.append(f"too many inputs in one run (max {MAX_TOTAL_INPUTS})")
    ov = request.override
    if ov is not None and not (type(ov) is jurisdiction.Override and all(
            type(x) is str for x in (ov.user_id, ov.role, ov.reason))
            and len(ov.user_id) <= MAX_ID and len(ov.role) <= MAX_ID and len(ov.reason) <= MAX_REASON):
        problems.append("override must be an Override with text user_id, role and reason (reason <= 2000 chars)")
    return problems


def _input_problem(v: InputValue) -> str | None:
    """Exact types only: no subclasses with hostile comparison or conversion methods."""
    if type(v.value) not in VALUE_TYPES or (type(v.value) is str and len(v.value) > MAX_TEXT):
        return "value must be a bool, int, float or short text"
    if type(v.value) is int and abs(v.value) > MAX_INT:
        return "whole-number value is too large"
    if v.unit is not None and (type(v.unit) is not str or len(v.unit) > MAX_TEXT):
        return "unit must be short text or missing"
    if type(v.confirmed) is not bool:
        return "confirmed must be true or false"
    if type(v.provenance) not in (Provenance, str) or (type(v.provenance) is str and len(v.provenance) > 64):
        return "provenance must be a Provenance or its text"
    try:
        Provenance(v.provenance)
    except ValueError:
        return f"unknown provenance {v.provenance!r}"
    return None


def _invalid_facts(request: RunRequest, classes: set[str], states: set[str]) -> list[str]:
    p = request.project
    problems: list[str] = []
    if type(p.state) is not str or p.state.strip().upper() not in states:
        problems.append(f"state must be one of {sorted(states)} (spelled exactly)")
    if type(p.ncc_edition) is not str or p.ncc_edition not in KNOWN_EDITIONS:
        problems.append(f"ncc_edition must be one of {KNOWN_EDITIONS}")
    if type(p.climate_zone) is not int or not 1 <= p.climate_zone <= 8:
        problems.append("climate_zone must be a whole number from 1 to 8")
    part_count = _part_problems(p.building_class, classes, problems)
    for label, prov, required in (("facts_provenance", p.facts_provenance, True),
                                  ("climate_zone_provenance", p.climate_zone_provenance, False)):
        if prov is None and not required:
            continue
        if prov is None or type(prov) not in (Provenance, str):
            problems.append(f"{label}: must be a Provenance")
            continue
        try:
            Provenance(prov)
        except ValueError:
            problems.append(f"{label}: unknown provenance {prov!r}")
    if type(p.approval_date) is not date:
        problems.append("approval_date must be a calendar date")
    if (type(p.firm_id) is not str or type(p.revision_id) is not str
            or len(p.firm_id) > MAX_ID or len(p.revision_id) > MAX_ID):
        problems.append("firm_id and revision_id must be short text")
    for subject in request.subjects:
        if part_count:
            problems += _subject_part_problems(subject, part_count)
        for name in PROJECT_FACT_INPUTS:
            if name in subject.inputs:
                problems.append(f"{subject.id}.{name}: project facts come from the project, not from a subject")
    return problems


def _part_problems(building_class: Any, classes: set[str], problems: list[str]) -> int:
    """Validate the building class (text) or list of parts; returns the number of parts, 0 when invalid."""
    if type(building_class) is str:
        if building_class not in classes:
            problems.append(f"building_class must be one of {sorted(classes)}")
            return 0
        return 1
    if type(building_class) is not list:
        problems.append("building_class must be a class as text or a list of building parts")
        return 0
    if not 1 <= len(building_class) <= MAX_PARTS:
        problems.append(f"a project has 1 to {MAX_PARTS} building parts")
        return 0
    start = len(problems)
    for i, part in enumerate(building_class):
        if type(part) is not BuildingPart:
            problems.append(f"part {i}: must be a BuildingPart")
            continue
        if type(part.building_class) is not str or part.building_class not in classes:
            problems.append(f"part {i}: building_class must be one of {sorted(classes)}")
        s = part.storeys
        if s is not None and (type(s) is not int or not 1 <= s <= MAX_STOREYS):
            problems.append(f"part {i}: storeys must be a whole number from 1 to {MAX_STOREYS}")
        a = part.area_m2
        if a is not None and (type(a) not in (int, float) or not math.isfinite(a) or not 0 < a <= MAX_AREA_M2):
            problems.append(f"part {i}: area_m2 must be a number above 0 and at most {MAX_AREA_M2}")
    return len(building_class) if len(problems) == start else 0


def _subject_part_problems(subject: Subject, part_count: int) -> list[str]:
    part = subject.part
    if part is None:
        if part_count > 1:
            return [f"{subject.id}: a mixed-use project needs every subject to name its building part"]
        return []
    if type(part) is not int or not 0 <= part < part_count:
        return [f"{subject.id}: part must be a whole number from 0 to {part_count - 1}"]
    return []


def _snapshot(request: RunRequest) -> RunRequest:
    """Copy the (already validated) request into plain objects with provenance as the enum, so nothing a caller
    can mutate or subclass is read again."""
    p = request.project
    project = ProjectFacts(
        state=str(p.state), ncc_edition=str(p.ncc_edition), climate_zone=int(p.climate_zone),
        building_class=_parts_of(p.building_class), approval_date=date(p.approval_date.year, p.approval_date.month,
                                                                 p.approval_date.day),
        firm_id=str(p.firm_id), revision_id=str(p.revision_id), facts_provenance=Provenance(p.facts_provenance),
        climate_zone_provenance=(None if p.climate_zone_provenance is None
                                 else Provenance(p.climate_zone_provenance)))
    subjects = [
        Subject(str(s.id), [str(r) for r in s.rules],
                {str(n): InputValue(v.value, v.unit, Provenance(v.provenance), v.confirmed) for n, v in s.inputs.items()},
                None if s.part is None else int(s.part))
        for s in request.subjects]
    ov = request.override
    override = None if ov is None else jurisdiction.Override(str(ov.user_id), str(ov.role), str(ov.reason))
    return RunRequest(project, subjects, override)


def _parts_of(building_class: str | list[BuildingPart]) -> list[BuildingPart]:
    """Plain copies of the building parts; a class given as text is a one-part building."""
    if isinstance(building_class, str):
        return [BuildingPart(str(building_class))]
    return [BuildingPart(str(x.building_class), x.storeys, None if x.area_m2 is None else float(x.area_m2))
            for x in building_class]


def _part_label(parts: list[BuildingPart], index: int) -> str:
    return f"part {index}: " if len(parts) > 1 else ""


def _extracted_names(request: RunRequest) -> list[str]:
    names: list[str] = []
    if Provenance.EXTRACTED in (request.project.facts_provenance, request.project.climate_zone_provenance):
        names.append("project facts (climate_zone, building_class)")
    for subject in request.subjects:
        names += [f"{subject.id}.{n}" for n, v in subject.inputs.items() if v.provenance == Provenance.EXTRACTED]
    return names


def _unconfirmed_names(request: RunRequest) -> list[str]:
    """Inputs whose stored row has no recorded Gate 1 confirmation, whatever provenance they claim."""
    return [f"{s.id}.{n}" for s in request.subjects for n, v in s.inputs.items() if not v.confirmed]


def select_rules(pack: RulePack, edition: str, state: str) -> dict[str, Rule]:
    """Rules for this project: exactly its edition (never both) and its state."""
    return {rid: r for rid, r in sorted(pack.rules.items()) if r.selected_for(edition, state.strip())}


def _inputs_for(subject: Subject, project: ProjectFacts, rule: Rule) -> dict[str, InputValue]:
    values = dict(subject.inputs)
    parts = _parts_of(project.building_class)
    part_class = parts[subject.part or 0].building_class
    cz_provenance = project.climate_zone_provenance or project.facts_provenance
    for name, value, provenance in (("climate_zone", project.climate_zone, cz_provenance),
                                    ("building_class", part_class, project.facts_provenance)):
        if name in rule.inputs and name not in values:
            values[name] = InputValue(value, None, provenance)
    return values


def _result(subject: Subject, rule: Rule, evaluation: Evaluation) -> dict[str, Any]:
    src = rule.raw["source"]
    return {
        "subject_id": subject.id,
        "rule_id": rule.id,
        "outcome": evaluation.outcome.value,
        "causes": evaluation.causes,
        "citation": {
            "rule_id": rule.id, "document": src["document"], "edition": rule.edition, "clause": src["clause"],
            "url": src["url"], "rule_status": rule.status,
        },
        "inputs_used": evaluation.inputs_used,
        "near_miss": evaluation.near_miss,
        "fix_hypotheses": evaluation.fix_hypotheses,
    }


def _audit_double_refusal(
    ledger: jurisdiction.LedgerWriter | None, project: ProjectFacts, parts: list[BuildingPart],
    decision: jurisdiction.Decision, refused_by: tuple[str, ...], *, strict: bool,
) -> None:
    """When both the edition check and the applicability check refuse a run, record the refusal in the ledger.

    strict: a failed write refuses the run as `ledger_unavailable` (the refusal itself stands either way).
    """
    if len(refused_by) < 2:
        return
    if ledger is None:
        if strict:
            raise RunRefused("ledger_unavailable", ["no ledger is available to record the refusal"])
        return
    payload = {
        "project": {"state": project.state, "edition": project.ncc_edition,
                    "building_parts": [_part_json(p) for p in parts], "approval_date": project.approval_date.isoformat()},
        "firm_id": project.firm_id, "revision_id": project.revision_id, "reasons": list(decision.reasons),
        "refused_by": list(refused_by), "timestamp": datetime.now(UTC).isoformat(),
    }
    try:
        ledger.write("run_refused", payload, firm_id=project.firm_id, revision_id=project.revision_id)
    except Exception as exc:
        if strict:
            raise RunRefused("ledger_unavailable", ["the refusal could not be recorded in the ledger"]) from exc


def _part_json(part: BuildingPart) -> dict[str, Any]:
    out: dict[str, Any] = {"building_class": part.building_class}
    if part.storeys is not None:
        out["storeys"] = part.storeys
    if part.area_m2 is not None:
        out["area_m2"] = part.area_m2
    return out


def run(
    request: RunRequest,
    pack: RulePack,
    *,
    adoption: dict[str, Any] | None = None,
    applicability_data: dict[str, Any] | None = None,
    ledger: jurisdiction.LedgerWriter | None = None,
) -> dict[str, Any]:
    request = _copy_request(request)
    shape = _shape_problems(request)
    if shape:
        raise RunRefused("invalid_request", shape)
    try:
        data = adoption if adoption is not None else load_adoption()
        app_data = applicability_data if applicability_data is not None else load_applicability()
        states = {str(s) for s in data["jurisdictions"]}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise RunRefused("invalid_data", [f"adoption/applicability data unusable: {exc}"]) from exc
    invalid = _invalid_facts(request, canonical_classes(app_data), states)
    if invalid:
        raise RunRefused("invalid_request", invalid)
    request = _snapshot(request)
    extracted = _extracted_names(request)
    if extracted:
        raise RunRefused("extracted_inputs", [f"extracted value not confirmed at gate 1: {n}" for n in extracted])
    unconfirmed = _unconfirmed_names(request)
    if unconfirmed:
        raise RunRefused("gate1_required", [f"value not confirmed at gate 1: {n}" for n in unconfirmed])
    # the facts that choose the edition (state, edition, class, date) must be engineer-confirmed
    if Provenance(request.project.facts_provenance) != Provenance.ENGINEER_CONFIRMED:
        raise RunRefused("unconfirmed_facts", [
            "the project facts that choose the NCC edition must be engineer_confirmed"])
    project = dataclasses.replace(request.project, state=request.project.state.strip().upper())

    edition_decision = jurisdiction.decide(project.state, project.ncc_edition, project.approval_date, data)
    parts = _parts_of(project.building_class)
    applic_reasons: list[str] = []
    applic_notes: list[str] = []
    try:
        for i, part in enumerate(parts):  # every part is checked on its own: one refused part refuses the run
            d = applicability.check(project.state, project.ncc_edition, part.building_class, app_data)
            applic_reasons += [_part_label(parts, i) + r for r in d.reasons]
            applic_notes += [_part_label(parts, i) + n for n in d.notes]
    except (ValueError, TypeError, AttributeError) as exc:  # malformed data must stop the run, never widen it
        raise RunRefused("invalid_data", [f"applicability data unusable: {exc}"]) from exc
    applic_decision = jurisdiction.Decision(not applic_reasons, tuple(applic_reasons), tuple(applic_notes))
    refused_by = tuple(name for name, d in (("jurisdiction", edition_decision), ("applicability", applic_decision))
                       if not d.allowed)
    decision = jurisdiction.Decision(
        edition_decision.allowed and applic_decision.allowed,
        edition_decision.reasons + applic_decision.reasons, edition_decision.notes + applic_decision.notes)
    selected = select_rules(pack, project.ncc_edition, project.state)
    unknown = sorted({rid for s in request.subjects for rid in s.rules if rid not in selected})
    if unknown and not decision.allowed and request.override is None:
        _audit_double_refusal(ledger, project, parts, decision, refused_by, strict=True)
    if unknown:
        raise RunRefused("rule_not_selected", [
            f"{rid}: not a rule for {project.ncc_edition} in {project.state}" for rid in unknown])
    malformed = [f"{rid}: {msg}" for rid in sorted({r for s in request.subjects for r in s.rules})
                 for msg in check_rule_expressions(selected[rid])]
    if malformed:
        if not decision.allowed and request.override is None:
            _audit_double_refusal(ledger, project, parts, decision, refused_by, strict=True)
        raise RunRefused("rule_error", malformed)

    override_record: dict[str, str] | None = None
    if not decision.allowed:
        if request.override is None:
            _audit_double_refusal(ledger, project, parts, decision, refused_by, strict=True)
            raise RunRefused("jurisdiction" if "jurisdiction" in refused_by else "applicability",
                             list(decision.reasons))
        try:
            jurisdiction.check_override(
                request.override, decision, ledger, state=project.state, edition=project.ncc_edition,
                approval_date=project.approval_date, firm_id=project.firm_id, revision_id=project.revision_id,
                refused_by=refused_by)
        except jurisdiction.OverrideRejected as exc:
            _audit_double_refusal(ledger, project, parts, decision, refused_by, strict=False)
            raise RunRefused("override_rejected", [str(exc)]) from exc
        override_record = {"user_id": request.override.user_id, "role": request.override.role,
                           "reason": jurisdiction.clean_reason(request.override.reason)}

    results: list[dict[str, Any]] = []
    for subject in request.subjects:
        for rid in subject.rules:
            rule = selected[rid]
            try:
                evaluation = evaluate_rule(rule, _inputs_for(subject, project, rule))
            except ExtractedInputError as exc:  # unreachable after gate 2; kept as a second line of defence
                raise RunRefused("extracted_inputs", [str(exc)]) from exc
            except EvalError as exc:  # a rule that cannot be evaluated is an authoring error, not a result
                raise RunRefused("rule_error", [f"{rid}: {exc}"]) from exc
            result = _result(subject, rule, evaluation)
            if len(parts) > 1:
                result["part"] = subject.part
            results.append(result)

    used_rules = [selected[rid] for rid in dict.fromkeys(r["rule_id"] for r in results)]
    unassigned = sorted(set(selected) - {r["rule_id"] for r in results})
    return build_report(
        project=project, decision=decision, override=override_record, used_rules=used_rules, results=results,
        unassigned_rules=unassigned, refused_by=list(refused_by))


__all__ = ["Outcome", "RunRefused", "canonical_classes", "run", "select_rules"]  # canonical_classes: re-exported
