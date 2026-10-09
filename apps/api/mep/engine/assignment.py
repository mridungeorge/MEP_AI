"""Which selected rules run on a scheduled system.

The rule YAML decides: a rule is assigned to a system only when its own `applies_when.system_type` names the system's
type. Rules that declare no system type (duct and pipe runs) are never assigned to a system; the run report lists them
as unassigned. Nothing here knows a clause, a threshold or a system type by name.
"""
from mep.engine.loader import RulePack
from mep.engine.runner import select_rules


def rules_for_system_type(pack: RulePack, edition: str, state: str, system_type: str) -> list[str]:
    out: list[str] = []
    for rid, rule in select_rules(pack, edition, state).items():
        declared = rule.applies_when.get("system_type")
        types = declared if isinstance(declared, list) else [] if declared is None else [declared]
        if system_type in [str(t) for t in types]:
            out.append(rid)
    return out
