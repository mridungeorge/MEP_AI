"""Reasoning trace: why a result is stale or changed, as a chain  cause -> fact -> rule -> result.

Every step is taken from data, never written by a model:

* the CAUSE is a difference between the two revisions (a field of a space, an input of a system) described from the two rows' values;
* the FACT and the RULE are the two ends of a graph edge (`diff.graph`), and the edge's `via` says how the rule reads the fact;
* the RESULT is the stored result (outcome before, outcome after, the rule's citation from its YAML).

`verify_trace` re-derives every FACT -> RULE link from the graph, so a trace that names a link the rules do not contain is refused.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mep.diff.graph import FACT, RULE, SCOPE, DependencyGraph
from mep.diff.revision import StaleResult


@dataclass(frozen=True)
class Step:
    kind: str                       # cause | fact | rule | result
    id: str
    text: str
    via: str = ""                   # for a fact: how the rule reads it (input | depends_on | applies_when | scope)
    path: str = ""                  # for a fact read through depends_on: the declared path

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id, "text": self.text, "via": self.via, "path": self.path}


@dataclass(frozen=True)
class Chain:
    steps: tuple[Step, ...]


@dataclass(frozen=True)
class Trace:
    subject_id: str
    rule_id: str
    chains: tuple[Chain, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"subject_id": self.subject_id, "rule_id": self.rule_id,
                "chains": [[s.as_dict() for s in c.steps] for c in self.chains]}

    def lines(self) -> list[str]:
        return [" -> ".join(s.text for s in c.steps) for c in self.chains]


def _outcome(result: Mapping[str, Any] | None) -> str:
    return "no result" if result is None else str(result.get("outcome") or result.get("result") or "no result")


def _citation(result: Mapping[str, Any] | None) -> str:
    c = (result or {}).get("citation") or {}
    return f"{c.get('document', '')} {c.get('clause', '')}".strip()


def trace_for(stale: StaleResult, before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> Trace:
    """The trace of one stale result, from the stored result before and (once re-run) the result after."""
    chains: list[Chain] = []
    ref = after if after is not None else before
    for cause in stale.causes:
        fact = cause.edge.source
        fact_label = fact[len(SCOPE):] + " (any field)" if fact.startswith(SCOPE) else fact[len(FACT):]
        verb = {"input": "is an input of", "depends_on": "is declared a dependency of", "applies_when": "selects",
                "scope": "is declared a dependency of"}.get(cause.edge.via, "feeds")
        rule = cause.edge.target[len(RULE):]
        chains.append(Chain((
            Step("cause", f"change:{cause.change.kind}:{cause.change.name}", cause.detail),
            Step("fact", fact, f"{fact_label} {verb} the rule", cause.edge.via, cause.edge.path),
            Step("rule", cause.edge.target, f"rule {rule}" + (f" ({_citation(ref)})" if _citation(ref) else "")),
            Step("result", f"result:{stale.subject_id}/{stale.rule_id}",
                 f"{stale.subject_id}: {_outcome(before)}" + (f" -> {_outcome(after)}" if after is not None else " (stale, not yet re-run)")),
        )))
    return Trace(stale.subject_id, stale.rule_id, tuple(chains))


def verify_trace(trace: Trace, graph: DependencyGraph) -> list[str]:
    """Problems found (empty when the trace holds): every fact -> rule link must be an edge of the graph, every chain must run
    cause -> fact -> rule -> result, and the rule/result must be the trace's own."""
    problems: list[str] = []
    for n, chain in enumerate(trace.chains):
        kinds = [s.kind for s in chain.steps]
        if kinds != ["cause", "fact", "rule", "result"]:
            problems.append(f"chain {n}: steps are {kinds}")
            continue
        _cause, fact, rule, result = chain.steps
        edge_ok = any(e.target == rule.id and e.via == fact.via and e.path == fact.path for e in graph.edges_from(fact.id))
        if not edge_ok:
            problems.append(f"chain {n}: {fact.id} -> {rule.id} ({fact.via}) is not an edge of the dependency graph")
        if rule.id != RULE + trace.rule_id or result.id != f"result:{trace.subject_id}/{trace.rule_id}":
            problems.append(f"chain {n}: names a rule or result other than {trace.subject_id}/{trace.rule_id}")
    return problems
