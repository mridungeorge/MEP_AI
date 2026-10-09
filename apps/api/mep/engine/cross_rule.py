"""Cross-rule re-run: what else moves when an input changes.

A change to one input (a revision's new value, or a fix someone proposes) can satisfy one rule and break another that reads the same
input. This module re-evaluates EVERY rule of the subject that depends on the changed inputs (the rules come from the dependency
graph, not from a list written here), with the rule engine's own `evaluate_rule`, before and after the change, and reports:

* the moves (rule, outcome before, outcome after);
* the conflicts: a rule that got better and a rule that got worse because of the SAME input, naming both rules and the input
  (or, when the change is meant to fix one rule, every other rule it breaks);
* `withdrawn`: a fix is withdrawn when it creates any conflict or does not make its target pass.

Nothing here decides compliance on its own: outcomes are the engine's, and fix hypotheses stay the rules' own text.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field

from mep.diff.graph import Change, DependencyGraph
from mep.engine.loader import RulePack
from mep.engine.model import InputValue, Outcome, ProjectFacts, Subject
from mep.engine.rule_eval import evaluate_rule
from mep.engine.runner import _inputs_for

SEVERITY = {Outcome.PASS: 0, Outcome.NOT_APPLICABLE: 0, Outcome.NEEDS_JUDGEMENT: 1, Outcome.FAIL: 2}


@dataclass(frozen=True)
class Move:
    rule_id: str
    before: Outcome
    after: Outcome

    @property
    def worse(self) -> bool:
        return SEVERITY[self.after] > SEVERITY[self.before]

    @property
    def better(self) -> bool:
        return SEVERITY[self.after] < SEVERITY[self.before]


@dataclass(frozen=True)
class Conflict:
    subject_id: str
    input_name: str
    better: Move                 # the rule the change helps (the fix's target, or any rule that improved)
    worse: Move                  # the rule the change breaks

    def describe(self) -> str:
        return (f"{self.subject_id}: changing {self.input_name} moves {self.better.rule_id} {self.better.before.value} -> "
                f"{self.better.after.value} but breaks {self.worse.rule_id} {self.worse.before.value} -> {self.worse.after.value}")


@dataclass
class CrossRuleResult:
    subject_id: str
    changed_inputs: list[str]
    dependents: list[str]
    moves: list[Move] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    withdrawn: bool = False


def _outcomes(subject: Subject, project: ProjectFacts, pack: RulePack, rule_ids: list[str],
              overrides: Mapping[str, InputValue]) -> dict[str, Outcome]:
    shifted = Subject(subject.id, list(subject.rules), {**subject.inputs, **overrides}, subject.part)
    return {rid: evaluate_rule(pack.rules[rid], _inputs_for(shifted, project, pack.rules[rid])).outcome for rid in rule_ids}


def cross_rule_rerun(*, subject: Subject, project: ProjectFacts, changes: Mapping[str, InputValue], pack: RulePack,
                     graph: DependencyGraph, target_rule: str | None = None) -> CrossRuleResult:
    """Re-run the subject's dependent rules with `changes` applied. `target_rule` names the rule a fix is meant to satisfy."""
    changed = sorted(changes)
    reach = {name: set(graph.rules_affected(Change("input", name))) & set(subject.rules) for name in changed}
    dependents = sorted(set().union(*reach.values())) if reach else []
    result = CrossRuleResult(subject.id, changed, dependents)
    if not dependents:
        result.withdrawn = target_rule is not None
        return result
    before = _outcomes(subject, project, pack, dependents, {})
    after = _outcomes(subject, project, pack, dependents, changes)
    result.moves = [Move(rid, before[rid], after[rid]) for rid in dependents if before[rid] != after[rid]]
    better = [m for m in result.moves if m.better]
    # A conflict is named after the ONE input whose change, applied alone, moves one rule up and another down. Blaming every changed
    # input that both rules read would name inputs that caused nothing when several inputs change together.
    single: dict[str, list[Move]] = {}
    for name in changed:
        rules_n = sorted(reach[name])
        if not rules_n:
            continue
        b4, af = _outcomes(subject, project, pack, rules_n, {}), _outcomes(subject, project, pack, rules_n, {name: changes[name]})
        single[name] = [Move(rid, b4[rid], af[rid]) for rid in rules_n if b4[rid] != af[rid]]
    if target_rule is not None:
        target = next((m for m in better if m.rule_id == target_rule), None)
        for name, moves in single.items():
            if target_rule not in reach[name]:
                continue
            for w in (m for m in moves if m.worse and m.rule_id != target_rule):
                result.conflicts.append(Conflict(subject.id, name, target or Move(
                    target_rule, before.get(target_rule, Outcome.FAIL), after.get(target_rule, Outcome.FAIL)), w))
        result.withdrawn = bool(result.conflicts) or after.get(target_rule) != Outcome.PASS
    else:
        for name, moves in single.items():
            for b in (m for m in moves if m.better):
                for w in (m for m in moves if m.worse):
                    result.conflicts.append(Conflict(subject.id, name, b, w))
        result.withdrawn = bool(result.conflicts)
    return result


def vet_fix_hypotheses(fixes: list[str], cross: CrossRuleResult) -> list[str]:
    """A rule's fix hypotheses are returned only when the proposed change passes every dependent rule."""
    return [] if cross.withdrawn else list(fixes)
