"""Dependency graph of the rule pack: which facts each rule reads.

Built ONLY from the rules' own YAML, from three places (never from prose, never from a model):

* `inputs`       the values the rule is evaluated on (the precise list the engine uses),
* `depends_on`   the facts the rule's author declared, as dotted paths (`systems.controls.control_deadband`),
* `applies_when` the scalar keys that select the rule (`edition`, `state`, `building_class`, `system_type`, `climate_zone`, ...).

A fact is named by the LAST segment of its path (`control_deadband`); the scopes it was declared under (`systems`, `project`,
`spaces`, ...) are kept as attributes. Every fact node points at the rules that read it; a change names a fact (or, for a space,
the `spaces` scope, because space fields are only declared as a scope) and the graph answers which rules are affected, and through
which edge, so a reasoning trace can quote the edge instead of inventing a story.

No clause, threshold or system type is named in this file.
"""
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from mep.engine.loader import RulePack

Via = Literal["input", "depends_on", "applies_when", "scope"]
FACT = "fact:"
SCOPE = "scope:"
RULE = "rule:"

# `applies_when` keys that are not facts: lists of expressions (their inputs are already in `inputs`) and the pack selectors
# that every rule has (edition, state) which ARE facts of the project and are kept.
_EXPRESSION_KEYS = {"exempt_when", "needs_judgement_when"}
# facts that choose which rules apply at all, and the project-level changes that move them
SELECTORS = {"ncc_edition", "state"}
_ALIASES = {"edition": "ncc_edition", "approval_date": "ncc_edition", "building_part": "building_class"}


@dataclass(frozen=True, order=True)
class Edge:
    source: str      # "fact:<name>" or "scope:<scope>"
    target: str      # "rule:<id>"
    via: str         # input | depends_on | applies_when | scope
    path: str = ""   # the declared dotted path, when the edge came from depends_on


@dataclass(frozen=True)
class Change:
    """Something that changed between two revisions (or in a what-if)."""

    kind: Literal["input", "project", "space"]
    name: str                 # input name, project fact name, or space field


class DependencyGraph:
    def __init__(self, edges: set[Edge], rule_ids: list[str]) -> None:
        self.edges: tuple[Edge, ...] = tuple(sorted(edges))
        self.rule_ids: tuple[str, ...] = tuple(sorted(rule_ids))
        self._by_source: dict[str, list[Edge]] = defaultdict(list)
        self._by_rule: dict[str, list[Edge]] = defaultdict(list)
        for e in self.edges:
            self._by_source[e.source].append(e)
            self._by_rule[e.target[len(RULE):]].append(e)

    def facts(self) -> list[str]:
        return sorted({e.source for e in self.edges if e.source.startswith(FACT)})

    def edges_into_rule(self, rule_id: str) -> list[Edge]:
        return list(self._by_rule.get(rule_id, []))

    def edges_from(self, source: str) -> list[Edge]:
        return list(self._by_source.get(source, []))

    def rules_reading(self, fact: str) -> list[str]:
        return sorted({e.target[len(RULE):] for e in self._by_source.get(FACT + fact, [])})

    def affected(self, change: Change) -> list[Edge]:
        """The edges a change travels along: from the fact (or scope) it names to each rule that reads it."""
        name = _ALIASES.get(change.name, change.name)
        if change.kind == "space":
            sources = [SCOPE + "spaces"]
        elif change.kind == "project" and name in SELECTORS:
            sources = [FACT + n for n in sorted(SELECTORS)]            # the edition in force / the state picks the pack
        else:
            sources = [FACT + name]
        return sorted(e for s in sources for e in self._by_source.get(s, []))

    def rules_affected(self, change: Change) -> list[str]:
        return sorted({e.target[len(RULE):] for e in self.affected(change)})


def fact_name(path: str) -> str:
    return path.rsplit(".", 1)[-1]


def build_graph(pack: RulePack) -> DependencyGraph:
    edges: set[Edge] = set()
    for rid, rule in pack.rules.items():
        target = RULE + rid
        for name in rule.inputs:
            edges.add(Edge(FACT + _ALIASES.get(name, name), target, "input"))
        for path in rule.raw.get("depends_on", []):
            scope, _, _ = path.partition(".")
            edges.add(Edge(FACT + _ALIASES.get(fact_name(path), fact_name(path)), target, "depends_on", path))
            if scope == "spaces":     # space fields are not declared individually: any space change reaches the rule
                edges.add(Edge(SCOPE + "spaces", target, "scope", path))
        for key, value in rule.applies_when.items():
            if key in _EXPRESSION_KEYS or value is None:
                continue
            edges.add(Edge(FACT + _ALIASES.get(key, key), target, "applies_when"))
    return DependencyGraph(edges, list(pack.rules))
