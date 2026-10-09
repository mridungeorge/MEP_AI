"""Making a child revision when the architect sends a new model for a frozen revision (service connection).

* the child is a new `revision` row whose parent is the frozen one (migration 0008 allows that only for a frozen parent);
* the systems and their inputs are COPIED with their confirmations: nothing about the services changed because the architect re-issued
  the model, so nothing needs confirming again; the building parts and project facts belong to the project and are shared;
* the new file's spaces arrive as 'extracted' (unconfirmed); a space that matches a parent space with NO change beyond the tolerances
  takes the parent's confirmed values and confirmation (the designer confirms only what changed, as the diff lists it).
"""
import json
import re
import uuid
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from mep.diff.revision import diff_spaces

LABEL_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,19}$")


def next_label(label: str) -> str:
    """The architect revision after `label`: A -> B, Rev 7 -> Rev 8, anything else gets a '-2' style suffix."""
    label = (label or "").strip()
    if re.fullmatch(r"[A-Ya-y]", label):
        return chr(ord(label) + 1)
    m = re.fullmatch(r"(.*?)(\d+)", label)
    if m:
        return f"{m.group(1)}{int(m.group(2)) + 1}"
    return f"{label}-2"[:20] if label else "B"


def create_child(svc: psycopg.Connection[Any], firm_id: UUID, parent_id: UUID, label: str) -> UUID:
    """A child revision with the parent's systems and inputs copied (confirmations included)."""
    child = uuid.uuid4()
    with svc.transaction():
        project = svc.execute("select project_id from revision where id = %s and firm_id = %s", (parent_id, firm_id)).fetchone()
        if project is None:
            raise LookupError("parent revision not found")
        svc.execute("insert into revision (id, firm_id, project_id, architect_rev, status, parent_revision_id)"
                    " values (%s, %s, %s, %s, 'open', %s)", (child, firm_id, project[0], label, parent_id))
        cur = svc.cursor(row_factory=dict_row)
        for s in cur.execute("select id, type, tag, controls from system where revision_id = %s and firm_id = %s",
                             (parent_id, firm_id)).fetchall():
            new = uuid.uuid4()
            svc.execute("insert into system (id, firm_id, revision_id, type, tag, controls) values (%s, %s, %s, %s, %s, %s::jsonb)",
                        (new, firm_id, child, s["type"], s["tag"], json.dumps(s["controls"])))
            svc.execute(
                "insert into system_input (firm_id, system_id, name, value_number, value_text, value_bool, unit, provenance,"
                " source, confirmed_by, confirmed_at) select firm_id, %s, name, value_number, value_text, value_bool, unit,"
                " provenance, source, confirmed_by, confirmed_at from system_input where system_id = %s", (new, s["id"]))
    return child


def drop_empty_child(svc: psycopg.Connection[Any], firm_id: UUID, child: UUID) -> None:
    """Undo create_child when the upload failed before anything was ingested into the child."""
    with svc.transaction():
        svc.execute("delete from system_input where firm_id = %s and system_id in (select id from system where revision_id = %s)",
                    (firm_id, child))
        svc.execute("delete from system where firm_id = %s and revision_id = %s", (firm_id, child))
        svc.execute("delete from revision where firm_id = %s and id = %s", (firm_id, child))


_SPACE_COLS = ("id, ifc_guid, name, use, storey, area_m2_value, area_m2_provenance, ceiling_void_mm_value,"
               " ceiling_void_mm_provenance, centroid_x_m, centroid_y_m, confirmed_by, confirmed_at")


def _rows(svc: psycopg.Connection[Any], firm_id: UUID, revision_id: UUID) -> list[dict[str, Any]]:
    cur = svc.cursor(row_factory=dict_row)
    rows = cur.execute(f"select {_SPACE_COLS} from space where revision_id = %s and firm_id = %s", (revision_id, firm_id)).fetchall()
    for r in rows:
        r["area_m2"], r["ceiling_void_mm"] = r["area_m2_value"], r["ceiling_void_mm_value"]
    return rows


def carry_confirmations(svc: psycopg.Connection[Any], firm_id: UUID, parent_id: UUID, child_id: UUID) -> int:
    """For each child space that matches a CONFIRMED parent space with no change, take the parent's values and confirmation."""
    parent, child = _rows(svc, firm_id, parent_id), _rows(svc, firm_id, child_id)
    carried = 0
    with svc.transaction():
        for item in diff_spaces(parent, child):
            if item.change != "unchanged" or item.old is None or item.new is None or item.old["confirmed_by"] is None:
                continue
            o = item.old
            svc.execute(
                "update space set name = %s, use = %s, storey = %s, area_m2_value = %s, area_m2_provenance = %s,"
                " ceiling_void_mm_value = %s, ceiling_void_mm_provenance = %s, confirmed_by = %s, confirmed_at = %s"
                " where id = %s and firm_id = %s",
                (o["name"], o["use"], o["storey"], o["area_m2_value"], o["area_m2_provenance"], o["ceiling_void_mm_value"],
                 o["ceiling_void_mm_provenance"], o["confirmed_by"], o["confirmed_at"], item.new["id"], firm_id))
            carried += 1
    return carried


def ledger_created(svc: psycopg.Connection[Any], firm_id: UUID, child: UUID, parent: UUID, user_id: UUID, label: str,
                   sha: str, carried: int) -> None:
    svc.execute("insert into ledger_event (firm_id, revision_id, kind, payload) values (%s, %s, 'revision_created', %s::jsonb)",
                (firm_id, child, json.dumps({"parent": str(parent), "architect_rev": label, "source_sha256": sha,
                                              "created_by": str(user_id), "spaces_carried_unchanged": carried})))
