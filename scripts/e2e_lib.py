"""Shared fixture builders for the Postgres API tests and the Playwright e2e seed. Test data only: nothing here is a
compliance value. Inputs are generated from the rule pack's own declarations (name, type, unit, enum values).
"""
import io
from typing import Any

import openpyxl
from mep.engine.loader import RulePack
from mep.ingest.schedule import column_header, declared_unit, schedule_inputs, system_types


def sample_inputs(pack: RulePack, edition: str, system_type: str) -> dict[str, tuple[Any, str | None]]:
    """A complete, valid set of schedule inputs: {name: (value, unit)} for every input of the edition except system_type."""
    out: dict[str, tuple[Any, str | None]] = {}
    for name, spec in schedule_inputs(pack, edition).items():
        if name == "system_type":
            continue
        if spec.type in ("number", "int"):
            out[name] = (2, declared_unit(spec))
        elif spec.type == "bool":
            out[name] = (False, None)
        elif spec.type == "enum" and spec.values:
            out[name] = (spec.values[0], None)
        else:
            out[name] = ("test value", None)
    assert system_type in (system_types(pack, edition) or (system_type,))
    return out


def schedule_workbook(pack: RulePack, edition: str, tag: str, system_type: str) -> bytes:
    """An Excel schedule (the published layout) with one fully filled system row."""
    inputs = schedule_inputs(pack, edition)
    values = sample_inputs(pack, edition, system_type)
    names = [n for n in inputs if n != "system_type"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Systems"
    ws.append(["system tag", "system type", *[column_header(n, inputs[n]) for n in names]])
    ws.append([tag, system_type, *[values[n][0] for n in names]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def form_system(pack: RulePack, edition: str, tag: str, system_type: str) -> dict[str, Any]:
    """The JSON body of POST /schedule/systems for one fully filled system."""
    return {"systems": [{"tag": tag, "system_type": system_type, "inputs": [
        {"name": n, "value": v, "unit": u} if u else {"name": n, "value": v}
        for n, (v, u) in sample_inputs(pack, edition, system_type).items()]}]}
