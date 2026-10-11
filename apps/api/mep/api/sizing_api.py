"""Duct sizing: firm settings, the sizing schedule for a revision, the airflow balance per system and duct-fab spec card DRAFTS.

Arithmetic only (see mep/sizing.py): nothing here is a compliance result. Velocity notes compare against limits the FIRM sets; with none set they say NO LIMIT SET.
Spec card drafts carry the geometry the sizing implies and list the engineer inputs still missing (thickness, seam, connection, length): the card is never complete
until an engineer fills them, because duct-fab holds no gauge or allowance tables.
"""
import itertools
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from mep import sizing
from mep.api.revisions import Repo, _err
from mep.api.revisions import User as RevUser
from mep.api.services import Dsn, _as_user, runs_of

router = APIRouter()


def firm_settings(dsn: str, user: Any) -> dict[str, float | None]:
    with _as_user(dsn, user) as conn:
        row = conn.execute("select sizing_settings from firm where id = %s", (user.firm_id,)).fetchone()
    try:
        return sizing.clean_settings(row["sizing_settings"] if row else {})
    except ValueError:
        return dict(sizing.DEFAULTS)                      # a stored value that is no longer valid is ignored, not trusted


@router.get("/sizing/settings")
def get_settings(user: RevUser, dsn: Dsn) -> dict[str, Any]:
    return {"settings": firm_settings(dsn, user), "limits": {k: list(v) for k, v in sizing.SETTING_LIMITS.items()}, "defaults": sizing.DEFAULTS,
            "note": "Velocity limits have no default: they are your firm's choice. Physical constants: air density 1.2 kg/m3, viscosity 1.81e-5 Pa s."}


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: dict[str, float | None]


@router.put("/sizing/settings")
def put_settings(body: SettingsBody, user: RevUser, dsn: Dsn) -> dict[str, Any]:
    try:
        clean = sizing.clean_settings(body.settings)
    except ValueError as exc:
        raise _err(422, "bad_setting", str(exc)) from None
    try:
        with _as_user(dsn, user) as conn:
            conn.execute("select admin_set_sizing(%s::jsonb)", (psycopg.types.json.Jsonb({k: v for k, v in clean.items() if v is not None}),))
    except psycopg.errors.InsufficientPrivilege as exc:
        raise _err(403, "forbidden", str(exc).splitlines()[0]) from None
    return {"saved": True, "settings": clean}


def spec_card_drafts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A concentric rect_reducer draft wherever consecutive sizes along a system's trunk (largest airflow first) differ."""
    drafts = []
    by_system: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["recommended"] and r["status"] == "SIZED" and r["recommended"]["shape"] == "rect" and r["system_tag"]:
            by_system.setdefault(r["system_tag"], []).append(r)
    for tag, items in sorted(by_system.items()):
        items.sort(key=lambda r: (-r["airflow_ls"], r["tag"]))
        for n, (a, b) in enumerate(itertools.pairwise(items), start=1):
            ra, rb = a["recommended"], b["recommended"]
            if (ra["width_mm"], ra["depth_mm"]) == (rb["width_mm"], rb["depth_mm"]):
                continue
            drafts.append({"fitting": "rect_reducer", "mark": f"{tag}-R{n}"[:40], "units": "mm", "from_duct": a["tag"], "to_duct": b["tag"],
                           "geometry": {"width_in_mm": ra["width_mm"], "height_in_mm": ra["depth_mm"], "width_out_mm": rb["width_mm"], "height_out_mm": rb["depth_mm"],
                                        "alignment": "concentric"},
                           "missing_engineer_inputs": ["sheet_thickness_mm", "seam", "connection", "geometry.length_mm"], "complete": False,
                           "note": "Draft from the sizing schedule. Not buildable until an engineer supplies the missing inputs."})
    return drafts


@router.get("/revisions/{revision_id}/sizing")
def sizing_schedule(revision_id: UUID, user: RevUser, repo: Repo, dsn: Dsn) -> dict[str, Any]:
    if repo.revision_info(revision_id, user.firm_id) is None:
        raise _err(404, "not_found", "revision not found")
    return compute_schedule(dsn, user, revision_id)


def schedule_entries(dsn: str, user: Any, revision_id: UUID) -> list[dict[str, Any]]:
    """The revision's sizing schedule as hvac-dxf takes it (tag, size, airflow), SOUND results only. The server builds this; a card's own copy is never trusted."""
    out = []
    for row in compute_schedule(dsn, user, revision_id)["ducts"]:
        rec = row["recommended"]
        if rec is None or row["status"] != "SIZED":
            continue
        size = {"shape": "rect", "width_mm": rec["width_mm"], "depth_mm": rec["depth_mm"]} if rec["shape"] == "rect" else {"shape": "round", "diameter_mm": rec["diameter_mm"]}
        out.append({"tag": row["tag"], "size": size, "airflow_ls": row["airflow_ls"]})
    return sorted(out, key=lambda e: e["tag"])


def compute_schedule(dsn: str, user: Any, revision_id: UUID) -> dict[str, Any]:
    s = firm_settings(dsn, user)
    runs = runs_of(dsn, user, revision_id)
    with _as_user(dsn, user) as conn:
        clearance = float(conn.execute("select void_clearance_mm from firm where id = %s", (user.firm_id,)).fetchone()["void_clearance_mm"])
        voids = {str(r["id"]): (None if r["v"] is None or not r["c"] else float(r["v"])) for r in conn.execute(
            "select id, ceiling_void_mm_value as v, confirmed_by is not null as c from space where revision_id = %s and firm_id = %s", (revision_id, user.firm_id)).fetchall()}
    top: dict[str, float] = {}
    for r in runs:
        if r["kind"] == "duct" and r["airflow_ls"] and r["system_tag"]:
            top[r["system_tag"]] = max(top.get(r["system_tag"], 0.0), float(r["airflow_ls"]))
    rows = []
    for r in runs:
        if r["kind"] != "duct":
            continue
        row: dict[str, Any] = {"id": r["id"], "tag": r["tag"], "system_tag": r["system_tag"], "airflow_ls": r["airflow_ls"], "entered": {
            "shape": r["shape"], "width_mm": r["width_mm"], "depth_mm": r["depth_mm"], "diameter_mm": r["diameter_mm"]}, "recommended": None, "status": "NO DATA",
            "reason": None}
        if not r["airflow_ls"] or r["airflow_ls"] <= 0:
            row["reason"] = "no airflow entered"
        else:
            cap = None
            void = voids.get(r["space_id"] or "")
            if r["shape"] == "rect" and void is not None:
                cap = void - 2 * float(r["insulation_mm"]) - clearance
            if cap is not None and cap <= 0:
                row["reason"] = "the confirmed ceiling void is used up by the insulation and the clearance"
                rows.append(row)
                continue
            sized = sizing.size_duct(float(r["airflow_ls"]), r["shape"] or "rect", s, depth_cap_mm=cap)
            role = "main" if r["system_tag"] and float(r["airflow_ls"]) == top.get(r["system_tag"]) else "branch"
            row.update({"recommended": sized.as_dict(), "status": "UNSOUND" if sized.unsound else "SIZED", "role": role,
                        "velocity_note": sizing.velocity_note(sized.velocity_ms, role, s)})
            if sized.unsound:
                row["reason"] = "; ".join(sized.notes)
            if r["shape"] == "rect" and r["width_mm"] and r["depth_mm"]:
                row["entered_velocity_ms"] = round(float(r["airflow_ls"]) / 1000.0 / (float(r["width_mm"]) * float(r["depth_mm"]) / 1e6), 3)
                row["entered_matches"] = (float(r["width_mm"]), float(r["depth_mm"])) == (sized.width_mm, sized.depth_mm)
        rows.append(row)
    return {"note": "Sizing arithmetic only; velocity notes read against limits your firm sets. Nothing here is a compliance result.", "settings": s,
            "constants": {"air_density_kg_m3": sizing.RHO, "air_viscosity_pa_s": sizing.MU}, "ducts": rows,
            "balance": sizing.balance(runs, float(s["balance_tolerance_pct"] or 0.0)), "spec_card_drafts": spec_card_drafts(rows)}


@router.get("/revisions/{revision_id}/sizing/hvac-dxf-draft")
def hvac_dxf_draft(revision_id: UUID, user: RevUser, repo: Repo, dsn: Dsn) -> dict[str, Any]:
    """An hvac-dxf spec card DRAFT from the sized ducts that have plan coordinates: the drawn size IS the recommended size, so the tags match the sizing schedule.
    Title block fields and layers are left for the firm template or the designer; the card is not buildable until they are filled."""
    sched = sizing_schedule(revision_id, user, repo, dsn)
    runs = {r["id"]: r for r in runs_of(dsn, user, revision_id)}
    ducts, schedule, skipped = [], [], []
    for row in sched["ducts"]:
        run = runs[row["id"]]
        rec = row["recommended"]
        if rec is None or row["status"] != "SIZED" or run["x0"] is None or not run["system_tag"]:
            skipped.append({"tag": row["tag"], "reason": "no airflow, no coordinates or no system tag"})
            continue
        size = ({"shape": "rect", "width_mm": rec["width_mm"], "depth_mm": rec["depth_mm"]} if rec["shape"] == "rect" else {"shape": "round", "diameter_mm": rec["diameter_mm"]})
        ducts.append({"tag": run["tag"], "system": run["system_tag"], "size": size, "airflow_ls": run["airflow_ls"], "start_mm": [run["x0"], run["y0"]], "end_mm": [run["x1"], run["y1"]]})
        schedule.append({"tag": run["tag"], "size": size, "airflow_ls": run["airflow_ls"]})
    terminals = [{"tag": r["tag"], "system": r["system_tag"], "airflow_ls": r["airflow_ls"] * int(r["quantity"] or 1), "at_mm": [r["x0"], r["y0"]]} for r in runs.values()
                 if r["kind"] == "terminal" and r["x0"] is not None and r["system_tag"] and r["airflow_ls"]]
    return {"spec": {"spec_version": "1", "units": "mm", "mode": "layout", "ducts": ducts, "terminals": terminals, "sizing_schedule": schedule},
            "missing": ["mark", "title_block"], "skipped": skipped, "complete": False,
            "note": "Draft from the sizing schedule. Fill the mark and title block (or let the firm template do it) before building."}
