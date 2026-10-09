"""Revision lineage, stored results, the revision diff and freezing, on Postgres (a mixin of PgRepository).

Reads run as the signed-in user (row-level security). Run results are written on the service connection (a client may never write a
compliance result); the diff confirmation and the freeze go through security-definer functions that check the designer role.
"""
import json
import uuid
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from mep.api.gate1 import ConfirmRefused
from mep.api.schedule import RevisionFrozenError


def _num(v: Decimal | float | None) -> float | int | None:
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() and abs(f) < 1e15 else f


class RevisionMethods:
    # supplied by PgRepository
    _user: Any
    _pack: Any

    def _as_user(self) -> Any: ...
    def _as_service(self) -> Any: ...

    # ---- lineage ----------------------------------------------------------------------------------------------------
    def revision_info(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any] | None:
        with self._as_user() as conn:
            r = conn.execute(
                "select r.id, r.project_id, r.parent_revision_id, r.architect_rev, r.status, r.frozen_at is not null as frozen,"
                " p.ncc_edition, p.state from revision r join project p on p.id = r.project_id and p.firm_id = r.firm_id"
                " where r.id = %s and r.firm_id = %s", (revision_id, firm_id)).fetchone()
        if r is None:
            return None
        return {"id": str(r["id"]), "project_id": str(r["project_id"]), "architect_rev": r["architect_rev"],
                "status": r["status"], "frozen": r["frozen"], "ncc_edition": r["ncc_edition"], "state": r["state"],
                "parent_revision_id": None if r["parent_revision_id"] is None else str(r["parent_revision_id"])}

    def lineage(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any] | None:
        """{revision, ancestors (nearest first), children}: only revisions of the user's firm exist for them."""
        with self._as_user() as conn:
            if conn.execute("select 1 from revision where id = %s and firm_id = %s", (revision_id, firm_id)).fetchone() is None:
                return None
            ancestors = conn.execute(
                "with recursive up as (select id, parent_revision_id, architect_rev, status, frozen_at is not null as frozen, 1 as depth from revision"
                " where id = (select parent_revision_id from revision where id = %s and firm_id = %s) and firm_id = %s"
                " union all select r.id, r.parent_revision_id, r.architect_rev, r.status, r.frozen_at is not null, up.depth + 1 from revision r"
                " join up on r.id = up.parent_revision_id where r.firm_id = %s and up.depth < 100)"
                " select * from up order by depth", (revision_id, firm_id, firm_id, firm_id)).fetchall()
            children = conn.execute(
                "select id, architect_rev, status, frozen_at is not null as frozen from revision"
                " where parent_revision_id = %s and firm_id = %s order by architect_rev, id", (revision_id, firm_id)).fetchall()
            me = conn.execute("select id, architect_rev, status, frozen_at is not null as frozen from revision"
                              " where id = %s and firm_id = %s", (revision_id, firm_id)).fetchone()

        def brief(r: dict[str, Any]) -> dict[str, Any]:
            return {"id": str(r["id"]), "architect_rev": r["architect_rev"], "status": r["status"], "frozen": r["frozen"]}

        return {"revision": brief(me or {}), "ancestors": [brief(r) for r in ancestors], "children": [brief(r) for r in children]}

    # ---- stored results ---------------------------------------------------------------------------------------------
    def current_results(self, revision_id: UUID, firm_id: UUID) -> list[dict[str, Any]]:
        with self._as_user() as conn:
            rows = conn.execute(
                "select subject_id, rule_id, result::text as outcome, part, run_id, citation, causes, inputs, near_miss,"
                " fix_hypotheses, stale from rule_result where revision_id = %s and firm_id = %s and current"
                " order by subject_id, rule_id", (revision_id, firm_id)).fetchall()
        return [{"subject_id": r["subject_id"], "rule_id": r["rule_id"], "outcome": r["outcome"], "part": r["part"],
                 "run_id": None if r["run_id"] is None else str(r["run_id"]), "citation": r["citation"], "causes": r["causes"],
                 "inputs_used": r["inputs"], "near_miss": r["near_miss"], "fix_hypotheses": r["fix_hypotheses"],
                 "stale": r["stale"]} for r in rows]

    def save_results(self, revision_id: UUID, firm_id: UUID, report: dict[str, Any]) -> str:
        """Store a run's results as the revision's current results (the previous run's become history). Service connection:
        no client can write a compliance result."""
        run_id = str(uuid.uuid4())
        edition = report["project"]["ncc_edition"]
        try:
            with self._as_service() as conn:
                conn.execute("update rule_result set current = false where revision_id = %s and firm_id = %s and current",
                             (revision_id, firm_id))
                for r in report["results"]:
                    conn.execute(
                        "insert into rule_result (firm_id, revision_id, rule_id, edition, result, inputs, citation, fix_hypotheses,"
                        " stale, subject_id, part, run_id, causes, near_miss, current) values (%s, %s, %s, %s, %s, %s::jsonb,"
                        " %s::jsonb, %s::jsonb, false, %s, %s, %s, %s::jsonb, %s::jsonb, true)",
                        (firm_id, revision_id, r["rule_id"], edition, r["outcome"], json.dumps(r["inputs_used"], default=str),
                         json.dumps(r["citation"]), json.dumps(r["fix_hypotheses"]), r["subject_id"], r.get("part"), run_id,
                         json.dumps(r["causes"], default=str),
                         None if r["near_miss"] is None else json.dumps(r["near_miss"], default=str)))
        except psycopg.errors.RaiseException as exc:
            if "frozen" in str(exc):
                raise RevisionFrozenError from None
            raise
        return run_id

    # ---- the diff ---------------------------------------------------------------------------------------------------
    def _spaces(self, conn: Any, revision_id: UUID, firm_id: UUID) -> list[dict[str, Any]]:
        rows = conn.execute(
            "select id, ifc_guid, name, use, storey, area_m2_value, ceiling_void_mm_value, centroid_x_m, centroid_y_m,"
            " confirmed_by is not null as confirmed from space where revision_id = %s and firm_id = %s order by name, id",
            (revision_id, firm_id)).fetchall()
        return [{"id": str(r["id"]), "ifc_guid": r["ifc_guid"], "name": r["name"], "use": r["use"], "storey": r["storey"],
                 "area_m2": _num(r["area_m2_value"]), "ceiling_void_mm": _num(r["ceiling_void_mm_value"]),
                 "centroid_x_m": _num(r["centroid_x_m"]), "centroid_y_m": _num(r["centroid_y_m"]),
                 "confirmed": r["confirmed"]} for r in rows]

    def _inputs(self, conn: Any, revision_id: UUID, firm_id: UUID) -> dict[str, dict[str, tuple[Any, str | None]]]:
        out: dict[str, dict[str, tuple[Any, str | None]]] = {}
        for r in conn.execute(
                "select s.tag, i.name, i.value_number, i.value_text, i.value_bool, i.unit from system_input i join system s"
                " on s.id = i.system_id where s.revision_id = %s and i.firm_id = %s and s.tag is not null",
                (revision_id, firm_id)).fetchall():
            value = r["value_number"] if r["value_number"] is not None else (
                r["value_text"] if r["value_text"] is not None else r["value_bool"])
            out.setdefault(r["tag"], {})[r["name"]] = (_num(value) if isinstance(value, Decimal) else value, r["unit"])
        return out

    def diff_data(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any] | None:
        """Everything the pure diff needs: this revision and its parent (spaces, system inputs, the parent's current results) and
        the hash of the diff the designer last confirmed. None when the revision is not in the user's firm."""
        info = self.revision_info(revision_id, firm_id)
        if info is None:
            return None
        parent_id = info["parent_revision_id"]
        with self._as_user() as conn:
            data: dict[str, Any] = {"revision": info, "child_spaces": self._spaces(conn, revision_id, firm_id),
                                    "child_inputs": self._inputs(conn, revision_id, firm_id), "parent": None,
                                    "parent_spaces": [], "parent_inputs": {}, "confirmed_hash": None}
            confirmed = conn.execute("select diff_hash from revision_diff where revision_id = %s and firm_id = %s",
                                     (revision_id, firm_id)).fetchone()
            data["confirmed_hash"] = None if confirmed is None else confirmed["diff_hash"]
            if parent_id is not None:
                data["parent_spaces"] = self._spaces(conn, UUID(parent_id), firm_id)
                data["parent_inputs"] = self._inputs(conn, UUID(parent_id), firm_id)
        if parent_id is not None:
            data["parent"] = self.revision_info(UUID(parent_id), firm_id)
            data["parent_results"] = self.current_results(UUID(parent_id), firm_id)
        data["child_results"] = self.current_results(revision_id, firm_id)
        return data

    def confirm_diff(self, revision_id: UUID, diff_hash: str) -> None:
        try:
            with self._as_user() as conn:
                conn.execute("select confirm_revision_diff(%s, %s)", (revision_id, diff_hash))
        except psycopg.errors.InsufficientPrivilege as exc:
            raise ConfirmRefused(str(exc).splitlines()[0]) from None
        except psycopg.errors.RaiseException as exc:
            if "frozen" in str(exc):
                raise RevisionFrozenError from None
            raise ConfirmRefused(str(exc).splitlines()[0]) from None

    def freeze(self, revision_id: UUID) -> None:
        try:
            with self._as_user() as conn:
                conn.execute("select freeze_revision(%s)", (revision_id,))
        except psycopg.errors.InsufficientPrivilege as exc:
            raise ConfirmRefused(str(exc).splitlines()[0]) from None
        except psycopg.errors.RaiseException as exc:
            raise ConfirmRefused(str(exc).splitlines()[0]) from None
