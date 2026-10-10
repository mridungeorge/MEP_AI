"""Postgres-backed repositories for Gate 1 and the schedule.

Every read and every client-driven write runs as the signed-in user: the connection does `set local role authenticated`
with that user's id in the JWT claims, so row-level security, the provenance triggers and `gate1_confirm()` apply exactly
as they do for a Supabase client. Provenance is never taken from request JSON:

* a form or Gate 1 edit is written as the user and the database only accepts provenance 'default' from that role;
* an Excel import is 'extracted'. A client may not write that, so it is written on a service connection, after the router
  has checked the user's role, and always scoped to the user's firm and a revision of that firm;
* 'engineer_confirmed' exists only through gate1_confirm() (designer, own firm), which also writes the ledger row in the
  same transaction.

The tag is a label. The system type is stored both on `system.type` (closed domain) and as a `system_type` input row, so
it is confirmed at Gate 1 like every other input; rules are assigned from the CONFIRMED input, never from the label.
"""
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from mep.api.gate1 import ConfirmRefused, EvidenceLockedError, InvalidInputError, UnknownSystemError
from mep.api.pg_revisions import RevisionMethods
from mep.api.schedule import CurrentUser, RevisionFrozenError, SystemModel
from mep.engine.assignment import rules_for_system_type
from mep.engine.ledger import PostgresLedger
from mep.engine.loader import RulePack
from mep.ingest.schedule import CellError, normalise_value, schedule_inputs, system_types

# ifc_guid is never client-writable: it comes from the IFC ingest only
SPACE_FIELDS = ("name", "use", "storey", "area_m2", "ceiling_void_mm")


def _frozen(exc: Exception) -> bool:
    text = str(exc)
    return "revision is frozen" in text or "cannot change once results exist" in text


def _num(v: Decimal | float | None) -> float | int | None:
    if v is None:
        return None
    f = float(v)
    return int(f) if f.is_integer() and abs(f) < 1e15 else f


def _part_index(row: dict[str, Any]) -> int:
    """The building part a system names, read STRICTLY: a whole number with the unit 'dimensionless'. Anything else (a fraction,
    a flag, text, another unit: possible through a direct database write) is -1, which the run refuses as a part that does not exist."""
    v = row["value"]
    return v if type(v) is int and row["unit"] == "dimensionless" and v >= 0 else -1


def _row_provenance(confirmed_by: Any, *provs: str | None) -> str:
    if confirmed_by is not None:
        return "engineer_confirmed"
    return "extracted" if "extracted" in provs else "default"


_GRAPHS: dict[int, tuple[Any, Any]] = {}


def health_view(h: dict[str, Any]) -> dict[str, Any]:
    """The stored ingest health in the shape the UI reads."""
    return {"score_percent": h["score_percent"], "threshold_percent": h["minimum_percent"],
            "below_threshold": h["below_threshold"], "fixes": list(h.get("fixes", []))}


class PgLedger:
    """Service-role ledger writer: one short connection per event, so the write commits immediately."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def write(self, kind: str, payload: dict[str, Any], *, firm_id: str, revision_id: str) -> None:
        with psycopg.connect(self._dsn, autocommit=True) as conn:
            PostgresLedger(conn).write(kind, payload, firm_id=firm_id, revision_id=revision_id)


class PgRepository(RevisionMethods):
    """Implements both Gate1Repository and ScheduleRepository for one signed-in user."""

    def __init__(self, dsn: str, user: CurrentUser, pack: RulePack | None = None) -> None:
        self._dsn, self._user, self._pack = dsn, user, pack

    # ---- sessions ---------------------------------------------------------------------------------------------
    @contextmanager
    def _as_user(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(self._dsn, autocommit=False, row_factory=dict_row) as conn:
            conn.execute("set local role authenticated")
            conn.execute("select set_config('request.jwt.claims', %s, true)",
                         (self._user.claims_json(),))
            yield conn            # the connection context commits on a clean exit and rolls back on an exception

    @contextmanager
    def _as_service(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(self._dsn, autocommit=False, row_factory=dict_row) as conn:
            yield conn

    def _revision(self, conn: psycopg.Connection[dict[str, Any]], revision_id: UUID, firm_id: UUID) -> dict[str, Any] | None:
        return conn.execute("select id, project_id, frozen_at is not null as frozen from revision"
                            " where id = %s and firm_id = %s", (revision_id, firm_id)).fetchone()

    # ---- row shapes -------------------------------------------------------------------------------------------
    @staticmethod
    def _part(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": str(r["id"]), "building_class": r["building_class"], "storeys": r["storeys"],
                "area_m2": _num(r["area_m2_value"]), "confirmed": r["confirmed_by"] is not None, "etag": r["etag"]}

    @staticmethod
    def _space(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": str(r["id"]), "ifc_guid": r["ifc_guid"], "name": r["name"], "area_m2": _num(r["area_m2_value"]),
                "use": r["use"], "storey": r["storey"], "ceiling_void_mm": _num(r["ceiling_void_mm_value"]),
                "provenance": _row_provenance(r["confirmed_by"], r["area_m2_provenance"],
                                              r["ceiling_void_mm_provenance"]),
                "manual_trace": r["ifc_guid"] is None and r["evidence_area_id"] is None, "etag": r["etag"],
                "evidence": None if r["evidence_area_id"] is None and r["evidence_void_id"] is None else {
                    "source_sha256": r["evidence_source_sha256"], "page": r["evidence_page"], "area_unit": r["evidence_area_unit"],
                    "void_unit": r["evidence_void_unit"], "area_extraction": str(r["evidence_area_id"]) if r["evidence_area_id"] else None}}

    @staticmethod
    def _input(r: dict[str, Any]) -> dict[str, Any]:
        value = r["value_number"] if r["value_number"] is not None else (
            r["value_text"] if r["value_text"] is not None else r["value_bool"])
        return {"id": str(r["id"]), "system": r["tag"], "name": r["name"],
                "value": _num(value) if isinstance(value, Decimal) else value, "unit": r["unit"],
                "provenance": _row_provenance(r["confirmed_by"], r["provenance"]), "etag": r["etag"]}

    # `etag` fingerprints the values a designer confirms: a confirmation names the etag it was shown, and the store
    # refuses it if the row has changed since (a stale screen can never confirm a value nobody looked at)
    # (jsonb_build_array keeps NULL positions and quotes strings, so no two different rows share an etag)
    _SPACE_ETAG = ("md5(jsonb_build_array(ifc_guid, name, use, storey, area_m2_value, area_m2_unit,"
                   " ceiling_void_mm_value, ceiling_void_mm_unit)::text)")
    _SPACE_SQL = ("select id, ifc_guid, name, use, storey, area_m2_value, area_m2_provenance, ceiling_void_mm_value,"
                  " ceiling_void_mm_provenance, confirmed_by, evidence_area_id, evidence_void_id, evidence_source_sha256, evidence_page,"
                  f" evidence_area_unit, evidence_void_unit, {_SPACE_ETAG} as etag from space")
    _INPUT_ETAG = "md5(jsonb_build_array(i.name, i.value_number, i.value_text, i.value_bool, i.unit)::text)"
    _INPUT_SQL = ("select i.id, s.tag, i.name, i.value_number, i.value_text, i.value_bool, i.unit, i.provenance::text"
                  f" as provenance, i.confirmed_by, {_INPUT_ETAG} as etag"
                  " from system_input i join system s on s.id = i.system_id")
    _PART_ETAG = "md5(jsonb_build_array(position, building_class, storeys, area_m2_value, area_m2_unit)::text)"
    _PROJECT_ETAG = "md5(jsonb_build_array(state, ncc_edition, climate_zone, approval_date)::text)"

    # ---- Gate1Repository --------------------------------------------------------------------------------------
    def get_gate1_view(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any] | None:
        with self._as_user() as conn:
            rev = self._revision(conn, revision_id, firm_id)
            if rev is None:
                return None
            proj = conn.execute(f"select id, state, ncc_edition, climate_zone, approval_date, confirmed_by is not null"
                                f" as confirmed, {self._PROJECT_ETAG} as etag from project where id = %s and firm_id = %s",
                                (rev["project_id"], firm_id)).fetchone()
            if proj is None:
                return None
            parts = conn.execute(f"select id, building_class, storeys, area_m2_value, confirmed_by, {self._PART_ETAG} as etag"
                                 " from building_part where project_id = %s and firm_id = %s order by position",
                                 (rev["project_id"], firm_id)).fetchall()
            spaces = conn.execute(self._SPACE_SQL + " where revision_id = %s and firm_id = %s order by name, id",
                                  (revision_id, firm_id)).fetchall()
            inputs = conn.execute(self._INPUT_SQL + " where s.revision_id = %s and i.firm_id = %s"
                                  " order by s.tag, i.name", (revision_id, firm_id)).fetchall()
            run = conn.execute("select health from ingest_run where revision_id = %s and firm_id = %s"
                               " and health is not null order by created_at desc, id desc limit 1",
                               (revision_id, firm_id)).fetchone()
        health = None
        if run is not None:
            health = health_view(run["health"])
        return {"project": {"id": str(proj["id"]), "state": proj["state"], "ncc_edition": proj["ncc_edition"],
                            "climate_zone": proj["climate_zone"],
                            "approval_date": None if proj["approval_date"] is None else proj["approval_date"].isoformat(),
                            "confirmed": proj["confirmed"], "etag": proj["etag"]},
                "parts": [self._part(p) for p in parts], "spaces": [self._space(s) for s in spaces],
                "inputs": [self._input(i) for i in inputs], "health": health, "ncc_edition": proj["ncc_edition"]}

    def revision_frozen(self, revision_id: UUID, firm_id: UUID) -> bool:
        with self._as_user() as conn:
            rev = self._revision(conn, revision_id, firm_id)
        return bool(rev and rev["frozen"])

    def replace_parts(self, revision_id: UUID, firm_id: UUID, parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            with self._as_user() as conn:
                rev = self._revision(conn, revision_id, firm_id)
                if rev is None:
                    raise LookupError("revision not found")
                conn.execute("delete from building_part where project_id = %s and firm_id = %s",
                             (rev["project_id"], firm_id))
                rows = []
                for pos, p in enumerate(parts):
                    rows.append(conn.execute(
                        "insert into building_part (firm_id, project_id, position, building_class, storeys,"
                        " area_m2_value, area_m2_provenance) values (%s, %s, %s, %s, %s, %s, 'default')"
                        " returning id, building_class, storeys, area_m2_value, confirmed_by,"
                        " md5(jsonb_build_array(position, building_class, storeys, area_m2_value, area_m2_unit)::text) as etag",
                        (firm_id, rev["project_id"], pos, p["building_class"], p["storeys"], p["area_m2"])).fetchone())
        except psycopg.errors.RaiseException as exc:
            if _frozen(exc):
                raise RevisionFrozenError from None
            raise
        return [self._part(r) for r in rows if r is not None]

    def upsert_space(self, revision_id: UUID, firm_id: UUID, space_id: str | None, data: dict[str, Any],
                     manual_trace: bool) -> dict[str, Any] | None:
        cols: dict[str, Any] = {}
        for key in SPACE_FIELDS:
            if key not in data:
                continue
            v = data[key]
            if key == "storey":
                cols["storey"] = None if v is None else str(v)
            elif key == "area_m2":
                cols["area_m2_value"], cols["area_m2_provenance"] = v, None if v is None else "default"
            elif key == "ceiling_void_mm":
                cols["ceiling_void_mm_value"], cols["ceiling_void_mm_provenance"] = v, None if v is None else "default"
            else:
                cols[key] = v
        try:
            with self._as_user() as conn:
                if self._revision(conn, revision_id, firm_id) is None:
                    return None
                if space_id is None:
                    names = ["firm_id", "revision_id", *cols]
                    row = conn.execute(
                        f"insert into space ({', '.join(names)}) values ({', '.join(['%s'] * len(names))})"
                        " returning id", (firm_id, revision_id, *cols.values())).fetchone()
                    sid = None if row is None else row["id"]
                else:
                    sid = UUID(space_id)
                    # any edit resets EVERY provenance on the row to 'default' (a client may write nothing else), and
                    # the database trigger withdraws the confirmation
                    # an untouched value keeps 'extracted' (it was never looked at); anything else becomes 'default'
                    linked = {"area_m2": "evidence_area_id", "ceiling_void_mm": "evidence_void_id"}
                    for f in ("area_m2", "ceiling_void_mm"):        # a resend of an unchanged evidence value is not an edit
                        if f"{f}_value" in cols and conn.execute(
                                f"select 1 from space where id = %s and revision_id = %s and firm_id = %s and {linked[f]} is not null"
                                f" and {f}_value is not distinct from %s", (sid, revision_id, firm_id, cols[f"{f}_value"])).fetchone():
                            cols.pop(f"{f}_value")
                            cols.pop(f"{f}_provenance", None)
                    if any(k in cols for k in ("area_m2_value", "ceiling_void_mm_value")) and conn.execute(
                            "select 1 from space where id = %s and revision_id = %s and firm_id = %s and ("
                            + " or ".join(f"({linked[f]} is not null and {f}_value is distinct from %s)"
                                          for f in ("area_m2", "ceiling_void_mm") if f"{f}_value" in cols) + ")",
                            (sid, revision_id, firm_id, *[cols[f"{f}_value"] for f in ("area_m2", "ceiling_void_mm") if f"{f}_value" in cols])).fetchone():
                        raise EvidenceLockedError("this value was read from a drawing and cannot be edited or relabelled: delete the space and add it again")
                    # a value that came from evidence keeps its provenance through an edit of anything else
                    resets = {
                        f"{f}_provenance": (f"case when {f}_value is null then null when {f}_provenance = 'extracted' or {linked[f]} is not null"
                                            f" then {f}_provenance else 'default'::provenance end")
                        for f in ("area_m2", "ceiling_void_mm")}
                    sets = ", ".join([f"{c} = %s" for c in cols]
                                     + [f"{c} = {sql}" for c, sql in resets.items() if c not in cols])
                    done = conn.execute(f"update space set {sets} where id = %s and revision_id = %s"
                                        " and firm_id = %s returning id",
                                        (*cols.values(), sid, revision_id, firm_id)).fetchone()
                    if done is None:
                        return None
                out = conn.execute(self._SPACE_SQL + " where id = %s and revision_id = %s and firm_id = %s",
                                   (sid, revision_id, firm_id)).fetchone()
        except psycopg.errors.RaiseException as exc:
            if _frozen(exc):
                raise RevisionFrozenError from None
            raise
        return None if out is None else self._space(out)

    @staticmethod
    def _value_columns(value: Any) -> tuple[Any, Any, Any]:
        if isinstance(value, bool):
            return None, None, value
        if isinstance(value, int | float):
            return value, None, None
        return None, value, None

    def _checked_value(self, conn: psycopg.Connection[dict[str, Any]], revision_id: UUID, firm_id: UUID,
                       data: dict[str, Any]) -> tuple[Any, str | None]:
        """The same checks as the schedule: the name is an input of the project's edition, the unit is converted to the
        rule's declared unit (pint), and a system type is one the rules know."""
        if data["name"] == "building_part":            # which part of the building the system serves (not a rule input)
            count = conn.execute(
                "select count(*) as n from building_part bp join revision r on r.project_id = bp.project_id and"
                " r.firm_id = bp.firm_id where r.id = %s and r.firm_id = %s", (revision_id, firm_id)).fetchone()["n"]
            value = data.get("value")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < count:
                raise InvalidInputError("enter the building parts first, then name one of them" if count == 0 else
                                        f"building_part must be a whole number from 0 to {count - 1}")
            return value, "dimensionless"
        if self._pack is None:
            return data.get("value"), data.get("unit")
        row = conn.execute("select p.ncc_edition from revision r join project p on p.id = r.project_id and"
                           " p.firm_id = r.firm_id where r.id = %s and r.firm_id = %s", (revision_id, firm_id)).fetchone()
        edition = str(row["ncc_edition"]) if row else ""
        spec = schedule_inputs(self._pack, edition).get(data["name"])
        if spec is None:
            raise InvalidInputError(f"{data['name']} is not an input of {edition}")
        try:
            value, unit = normalise_value(spec, data.get("value"), data.get("unit"))
        except CellError as exc:
            raise InvalidInputError(f"{data['name']}: {exc}") from None
        types = system_types(self._pack, edition)
        if data["name"] == "system_type" and types is not None and value not in types:
            raise InvalidInputError(f"system_type must be one of {list(types)}")
        return value, unit

    def upsert_input(self, revision_id: UUID, firm_id: UUID, input_id: str | None,
                     data: dict[str, Any]) -> dict[str, Any] | None:
        try:
            with self._as_user() as conn:
                if self._revision(conn, revision_id, firm_id) is None:
                    return None
                value, unit = self._checked_value(conn, revision_id, firm_id, data)
                number, text, boolean = self._value_columns(value)
                data = {**data, "unit": unit}
                if input_id is None:
                    tag = data.get("system")
                    system = None if not tag else conn.execute(
                        "select id from system where revision_id = %s and firm_id = %s and tag = %s",
                        (revision_id, firm_id, tag)).fetchone()
                    if system is None:
                        raise UnknownSystemError("name the schedule tag of an existing system in this revision")
                    row = conn.execute(
                        "insert into system_input (firm_id, system_id, name, value_number, value_text, value_bool, unit,"
                        " provenance, source) values (%s, %s, %s, %s, %s, %s, %s, 'default', 'api')"
                        " on conflict (system_id, name) do update set value_number = excluded.value_number,"
                        " value_text = excluded.value_text, value_bool = excluded.value_bool, unit = excluded.unit,"
                        " provenance = 'default', source = 'api' returning id",
                        (firm_id, system["id"], data["name"], number, text, boolean, data.get("unit"))).fetchone()
                else:
                    row = conn.execute(
                        "update system_input set name = %s, value_number = %s, value_text = %s, value_bool = %s,"
                        " unit = %s, provenance = 'default', source = 'api' where id = %s and firm_id = %s"
                        " and system_id in (select id from system where revision_id = %s and firm_id = %s)"
                        " returning id", (data["name"], number, text, boolean, data.get("unit"), UUID(input_id),
                                          firm_id, revision_id, firm_id)).fetchone()
                if row is None:
                    return None
                if data["name"] == "system_type" and isinstance(value, str):
                    # keep the label column in step with the input the rules are assigned from
                    conn.execute("update system set type = %s where firm_id = %s and id = (select system_id from"
                                 " system_input where id = %s)", (value, firm_id, row["id"]))
                out = conn.execute(self._INPUT_SQL + " where i.id = %s and i.firm_id = %s",
                                   (row["id"], firm_id)).fetchone()
        except psycopg.errors.RaiseException as exc:
            if _frozen(exc):
                raise RevisionFrozenError from None
            raise
        return None if out is None else self._input(out)

    _ETAG_SQL: ClassVar[dict[str, str]] = {
        "space": f"select id::text as id, {_SPACE_ETAG} as etag from space where id = any(%s) and firm_id = %s for update",
        "system_input": (f"select i.id::text as id, {_INPUT_ETAG} as etag from system_input i"
                         " where i.id = any(%s) and i.firm_id = %s for update"),
        "building_part": (f"select id::text as id, {_PART_ETAG} as etag from building_part"
                          " where id = any(%s) and firm_id = %s for update"),
        "project": f"select id::text as id, {_PROJECT_ETAG} as etag from project where id = any(%s) and firm_id = %s for update",
    }

    def confirm(self, groups: dict[str, list[str]], user_id: UUID, revision_id: UUID | None = None,
                etags: dict[str, str | None] | None = None) -> None:
        """Confirm every kind in ONE transaction (all or nothing). Each row must name the etag the designer was shown;
        a row changed since then is refused, so a stale screen cannot confirm a value nobody looked at."""
        if user_id != self._user.user_id:           # the database confirms as the JWT subject: never as someone else
            raise ConfirmRefused("a confirmation is recorded for the signed-in user only")
        seen = etags or {}
        try:
            with self._as_user() as conn:
                for kind, ids in groups.items():
                    uuids = [UUID(i) for i in ids]
                    current = {r["id"]: r["etag"] for r in conn.execute(
                        self._ETAG_SQL[kind], (uuids, self._user.firm_id)).fetchall()}
                    stale = [i for i in ids if seen.get(f"{kind}:{i}") is None or current.get(i) != seen[f"{kind}:{i}"]]
                    if stale:
                        raise ConfirmRefused(f"{len(stale)} {kind} row(s) changed since you loaded the screen"
                                             " (or were not shown): reload and review before confirming")
                    conn.execute("select gate1_confirm(%s, %s::uuid[], %s)", (kind, uuids, revision_id))
        except psycopg.errors.InsufficientPrivilege as exc:
            raise ConfirmRefused(str(exc).splitlines()[0]) from None
        except (psycopg.errors.DeadlockDetected, psycopg.errors.SerializationFailure):
            raise ConfirmRefused("another confirmation is running on the same rows: retry") from None
        except psycopg.errors.RaiseException as exc:
            if _frozen(exc):
                raise RevisionFrozenError from None
            raise ConfirmRefused(str(exc).splitlines()[0]) from None

    def load_run_inputs(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any]:
        with self._as_user() as conn:
            rev = self._revision(conn, revision_id, firm_id)
            if rev is None:
                raise LookupError("revision not found")
            # the fingerprint is taken BEFORE the reads: an edit that lands during them changes the live hash, so the save is refused
            inputs_hash = conn.execute("select live_inputs_hash(%s) as h", (revision_id,)).fetchone()["h"]
            proj = conn.execute("select state, ncc_edition, climate_zone, building_class, approval_date, confirmed_by"
                                " from project where id = %s and firm_id = %s", (rev["project_id"], firm_id)).fetchone()
            if proj is None:
                raise LookupError("project not found")
            parts = conn.execute("select building_class, storeys, area_m2_value, confirmed_by from building_part"
                                 " where project_id = %s and firm_id = %s order by position",
                                 (rev["project_id"], firm_id)).fetchall()
            spaces = conn.execute(self._SPACE_SQL + " where revision_id = %s and firm_id = %s order by name, id",
                                  (revision_id, firm_id)).fetchall()
            systems = conn.execute("select id, tag, type from system where revision_id = %s and firm_id = %s"
                                   " order by tag, id", (revision_id, firm_id)).fetchall()
            inputs = conn.execute(
                "select i.system_id, i.name, i.value_number, i.value_text, i.value_bool, i.unit,"
                " i.provenance::text as provenance, i.confirmed_by from system_input i join system s on s.id ="
                " i.system_id where s.revision_id = %s and i.firm_id = %s order by i.name",
                (revision_id, firm_id)).fetchall()
        by_system: dict[Any, list[dict[str, Any]]] = {}
        for i in inputs:
            value = i["value_number"] if i["value_number"] is not None else (
                i["value_text"] if i["value_text"] is not None else i["value_bool"])
            by_system.setdefault(i["system_id"], []).append({
                "name": i["name"], "value": _num(value) if isinstance(value, Decimal) else value, "unit": i["unit"],
                "provenance": i["provenance"], "confirmed_by": i["confirmed_by"]})
        run_systems = []
        for s in systems:
            rows = by_system.get(s["id"], [])
            typed = next((r["value"] for r in rows if r["name"] == "system_type" and r["confirmed_by"] is not None
                          and r["provenance"] == "engineer_confirmed"), None)
            rules = [] if self._pack is None or typed is None else rules_for_system_type(
                self._pack, proj["ncc_edition"], proj["state"], str(typed))
            part = next((_part_index(r) for r in rows if r["name"] == "building_part" and r["confirmed_by"] is not None
                         and r["provenance"] == "engineer_confirmed"), None)
            run_systems.append({"id": s["tag"] or str(s["id"]), "rules": rules, "part": part, "inputs": rows})
        building_class: Any = [{"building_class": p["building_class"], "storeys": p["storeys"],
                                "area_m2": _num(p["area_m2_value"]), "confirmed_by": p["confirmed_by"]} for p in parts]
        return {"frozen": bool(rev["frozen"]), "inputs_hash": inputs_hash,
                "project": {"state": proj["state"], "ncc_edition": proj["ncc_edition"],
                            "climate_zone": proj["climate_zone"],
                            "building_class": building_class or (proj["building_class"] or ""),
                            "approval_date": proj["approval_date"], "confirmed_by": proj["confirmed_by"]},
                "spaces": [{"id": str(s["id"]), "provenance": self._space(s)["provenance"],
                            "confirmed_by": s["confirmed_by"]} for s in spaces],
                "systems": run_systems}

    # ---- the run: gate on the diff, extras after it -----------------------------------------------------------------
    def _graph(self) -> Any:
        from mep.diff.graph import build_graph
        if self._pack is None:
            raise LookupError("no rule pack")
        cached = _GRAPHS.get(id(self._pack))
        if cached is None or cached[0] is not self._pack:
            cached = _GRAPHS[id(self._pack)] = (self._pack, build_graph(self._pack))
        return cached[1]

    def diff_reasons(self, revision_id: UUID, firm_id: UUID) -> list[str]:
        """Why a CHILD revision may not run yet: its differences from the parent must be confirmed, as they stand."""
        from mep.diff.service import build_diff
        data = self.diff_data(revision_id, firm_id)
        if data is None or data["parent"] is None:
            return []
        diff = build_diff(data, self._graph())
        if diff["has_changes"] and not diff["confirmed"]:
            return ["this revision differs from its parent and the diff is not confirmed (or it changed since it was confirmed)"]
        return []

    def run_extras(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any]:
        """After a run of a child revision: the stale results with their traces (now with the new outcomes) and the conflicts."""
        from mep.api.revisions import conflicts_of
        from mep.diff.service import build_diff
        data = self.diff_data(revision_id, firm_id)
        if data is None or data["parent"] is None:
            return {}
        diff = build_diff(data, self._graph())
        conflicts = conflicts_of(self, self._pack, self._graph(), firm_id, revision_id, UUID(data["parent"]["id"]), diff["inputs"])
        return {"revision": {"parent": data["parent"]["id"], "architect_rev": data["revision"]["architect_rev"]},
                "stale": diff["stale"], "traces": diff["traces"], "conflicts": conflicts}

    # ---- MeRepository -----------------------------------------------------------------------------------------
    def me(self, user: CurrentUser) -> dict[str, Any]:
        with self._as_user() as conn:
            firm = conn.execute("select name, signer_mode from firm where id = %s", (user.firm_id,)).fetchone()
            me = conn.execute("select role::text as role, also_roles::text[] as also, is_admin, email from app_user where id = %s",
                              (user.user_id,)).fetchone()
        small = firm is not None and firm["signer_mode"] == "small_firm"
        return {"user_id": str(user.user_id), "role": user.role, "firm_id": str(user.firm_id),
                "firm_name": None if firm is None else firm["name"],
                "signer_mode": None if firm is None else firm["signer_mode"],
                "own_role": None if me is None else me["role"],
                "is_admin": bool(me and me["is_admin"]), "email": None if me is None else me["email"],
                "available_roles": [me["role"], *(me["also"] or [])] if (small and me is not None) else
                                   ([me["role"]] if me is not None else []),
                "independence_notice": "NOT INDEPENDENTLY CHECKED" if small else None}

    def list_revisions(self, user: CurrentUser) -> list[dict[str, Any]]:
        with self._as_user() as conn:
            rows = conn.execute(
                "select r.id, r.project_id, p.address, p.state, p.ncc_edition, r.architect_rev, r.status,"
                " r.frozen_at is not null as frozen, r.parent_revision_id from revision r join project p"
                " on p.id = r.project_id and p.firm_id = r.firm_id where r.firm_id = %s order by p.address, r.id",
                (user.firm_id,)).fetchall()
        return [{"id": str(r["id"]), "project_id": str(r["project_id"]), "address": r["address"], "state": r["state"],
                 "ncc_edition": r["ncc_edition"], "architect_rev": r["architect_rev"], "status": r["status"],
                 "frozen": r["frozen"],
                 "parent_revision_id": None if r["parent_revision_id"] is None else str(r["parent_revision_id"])}
                for r in rows]

    # ---- ScheduleRepository -----------------------------------------------------------------------------------
    def revision_edition(self, revision_id: UUID, firm_id: UUID) -> str | None:
        with self._as_user() as conn:
            row = conn.execute("select p.ncc_edition from revision r join project p on p.id = r.project_id"
                               " and p.firm_id = r.firm_id where r.id = %s and r.firm_id = %s",
                               (revision_id, firm_id)).fetchone()
        return None if row is None else str(row["ncc_edition"])

    def upsert_systems(self, revision_id: UUID, firm_id: UUID, systems: Sequence[SystemModel], source: str) -> int:
        """A form entry is written as the user ('default'). An Excel import is 'extracted': a client may not write that,
        so it goes through the service connection, after the router's role check and scoped to the user's firm."""
        extracted = source == "xlsx"
        session = self._as_service if extracted else self._as_user
        provenance = "extracted" if extracted else "default"
        try:
            with session() as conn:
                if self._revision(conn, revision_id, firm_id) is None:
                    raise LookupError("revision not found")
                for s in systems:
                    sysrow = conn.execute(
                        "insert into system (firm_id, revision_id, type, tag) values (%s, %s, %s, %s)"
                        " on conflict (revision_id, tag) where tag is not null do update set type = excluded.type"
                        " returning id", (firm_id, revision_id, s.system_type, s.tag)).fetchone()
                    if sysrow is None:
                        raise LookupError("system not stored")
                    rows = [("system_type", s.system_type, None), *[(i.name, i.value, i.unit) for i in s.inputs
                                                                    if i.name != "system_type"]]
                    for name, value, unit in rows:
                        number, text, boolean = self._value_columns(value)
                        conn.execute(
                            "insert into system_input (firm_id, system_id, name, value_number, value_text, value_bool,"
                            " unit, provenance, source) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                            " on conflict (system_id, name) do update set value_number = excluded.value_number,"
                            " value_text = excluded.value_text, value_bool = excluded.value_bool, unit = excluded.unit,"
                            " provenance = excluded.provenance, source = excluded.source",
                            (firm_id, sysrow["id"], name, number, text, boolean, unit, provenance, source))
        except psycopg.errors.RaiseException as exc:
            if _frozen(exc):
                raise RevisionFrozenError from None
            raise
        return len(systems)
