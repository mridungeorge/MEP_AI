"""Drafting skills through the API: forms, firm defaults, the plain-language shortcut, running, the validator gate, downloads, use as a model.
Needs the local Supabase."""
import hashlib
import json
import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack
from mep.skills_runner import registry, runner

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL

SKILL_DIR = registry.SKILLS_DIR / "space-envelope"
SPEC = json.loads((SKILL_DIR / "examples" / "office_floor" / "spec.json").read_text(encoding="utf-8"))
DUCT = json.loads((registry.SKILLS_DIR / "duct-fab" / "examples" / "rect_to_round" / "spec.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pack():
    return load_pack(lin.ROOT / "rules")


@pytest.fixture(scope="module")
def client(pack):
    return TestClient(create_pg_app(DB_URL, h.SECRET, pack, supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def hd(f, role="designer"):
    return h.auth(f[role])


def run_url(f, skill="space-envelope"):
    return f"/revisions/{f['revision']}/skills/{skill}/run"


# ---- the list, the cards, the forms ---------------------------------------------------------------------------------

def test_both_skills_are_listed_and_their_cards_become_forms(admin, client):
    f = h.seed(admin)
    listed = {s["name"]: s for s in client.get("/skills", headers=hd(f)).json()}
    assert set(listed) == {"duct-fab", "space-envelope", "hvac-dxf", "ifc-mep"} and listed["space-envelope"]["available"]
    card = client.get("/skills/space-envelope/card", headers=hd(f)).json()
    by_name = {x["name"]: x for x in card["form"]}
    assert by_name["mark"]["required"] and by_name["storey"]["kind"] == "group" and by_name["rooms"]["kind"] == "list"
    storey = {x["name"]: x for x in by_name["storey"]["fields"]}
    assert storey["floor_to_floor_mm"]["unit"] == "mm" and storey["floor_to_floor_mm"]["required"] and not storey["floor_to_floor_mm"]["firm_default"]
    assert storey["elevation_mm"]["firm_default"] and not storey["elevation_mm"]["required"]
    room = {x["name"]: x for x in by_name["rooms"]["item"]}
    assert room["height_mm"]["required"] and room["height_mm"]["min"] == 1800 and room["outline"]["kind"] == "choice"
    assert set(room["outline"]["cases"]) == {"rectangle", "polygon"}
    assert "rooms[].use" in card["firm_default_paths"] and "storey.floor_to_floor_mm" not in card["firm_default_paths"]
    duct = client.get("/skills/duct-fab/card", headers=hd(f)).json()
    geometry = next(x for x in duct["form"] if x["name"] == "geometry")
    assert geometry["kind"] == "conditional" and set(geometry["cases"]) == {"rect_to_round", "rect_reducer", "rect_offset"}
    thickness = next(x for x in duct["form"] if x["name"] == "sheet_thickness_mm")
    assert thickness["required"] and thickness["unit"] == "mm" and not thickness["firm_default"]
    assert {"material", "notes", "geometry.circle_segments"} <= set(duct["firm_default_paths"])
    assert client.get("/skills/not-a-skill/card", headers=hd(f)).status_code == 404
    assert client.get("/skills/space-envelope/card").status_code == 401


# ---- firm defaults ---------------------------------------------------------------------------------------------------

def test_firm_defaults_only_for_fields_the_card_allows_and_only_for_a_designer(admin, client):
    f = h.seed(admin)
    ok = client.put("/skills/space-envelope/defaults", headers=hd(f), json={"defaults": {"rooms[].use": "Office", "notes": "Firm note"}})
    assert ok.status_code == 200
    bad = client.put("/skills/space-envelope/defaults", headers=hd(f), json={"defaults": {"storey.floor_to_floor_mm": 3000}})
    assert bad.status_code == 422 and "engineer inputs" in bad.text
    assert client.put("/skills/space-envelope/defaults", headers=hd(f, "checker"), json={"defaults": {}}).status_code == 403
    assert client.get("/skills/space-envelope/card", headers=hd(f, "checker")).json()["firm_defaults"]["notes"] == "Firm note"
    other = h.seed(admin)
    assert client.get("/skills/space-envelope/card", headers=hd(other)).json()["firm_defaults"] == {}          # another firm's are separate


def test_firm_defaults_fill_empty_fields_but_never_override_what_the_designer_set(admin, client):
    f = h.seed(admin)
    client.put("/skills/space-envelope/defaults", headers=hd(f), json={"defaults": {"rooms[].use": "Default use", "notes": "Firm note"}})
    spec = json.loads(json.dumps(SPEC))
    spec.pop("notes")
    for r in spec["rooms"]:
        r.pop("use", None)
    spec["rooms"][0]["use"] = "Chosen use"
    r = client.post(run_url(f), headers=hd(f), json={"spec": spec})
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    assert sorted(r.json()["firm_defaults_applied"]) == ["notes", "rooms[1].use", "rooms[2].use"]
    used = admin.execute("select spec -> 'rooms' -> 0 ->> 'use', spec -> 'rooms' -> 1 ->> 'use', spec ->> 'notes' from skill_run where id = %s",
                         (r.json()["run_id"],)).fetchone()
    assert used == ("Chosen use", "Default use", "Firm note")


# ---- the plain-language shortcut --------------------------------------------------------------------------------------

def test_the_shortcut_proposes_a_card_and_asks_for_what_it_did_not_hear(admin, client):
    f = h.seed(admin)
    r = client.post("/skills/duct-fab/shortcut", headers=hd(f), json={
        "text": "600x400 to 300 dia, 500 long, 0.8 mm sheet, pittsburgh seam 25, tdc 30, mark TR-01"})
    p = r.json()
    assert r.status_code == 200 and p["proposal"]["fitting"] == "rect_to_round"
    assert p["proposal"]["geometry"] == {"width_mm": 600, "height_mm": 400, "diameter_mm": 300, "length_mm": 500}
    assert p["proposal"]["sheet_thickness_mm"] == 0.8 and p["proposal"]["seam"] == {"type": "pittsburgh", "allowance_mm": 25}
    assert p["proposal"]["connection"] == {"type": "tdc", "allowance_mm": 30} and p["proposal"]["mark"] == "TR-01"
    assert all({"text", "field", "value"} <= set(u) for u in p["understood"])
    assert all("spec_version" not in m["field"] and m["field"] != "units" for m in p["missing"])          # fixed by the card, never asked
    thin = client.post("/skills/duct-fab/shortcut", headers=hd(f), json={"text": "600x400 to 300 dia"}).json()
    assert {"geometry.length_mm", "sheet_thickness_mm", "seam.type", "seam.allowance_mm", "connection.type", "connection.allowance_mm", "mark"} <= \
        {m["field"] for m in thin["missing"]}
    assert all("?" in m["question"] for m in thin["missing"])
    nothing = client.post("/skills/duct-fab/shortcut", headers=hd(f), json={"text": "make me a fitting please"}).json()
    assert nothing["understood"] == [] and any(m["field"] == "fitting" for m in nothing["missing"])
    rooms = client.post("/skills/space-envelope/shortcut", headers=hd(f), json={
        "text": "Level 2, 3.6 m floor to floor; Office 12 x 9 m, 2.7 m high, 0.6 m void; Plant room 6 x 4 m, 3.2 m high"}).json()
    spec = rooms["proposal"]
    assert spec["storey"] == {"name": "Level 2", "floor_to_floor_mm": 3600}
    assert [(x["name"], x["kind"], x["outline"]["width_mm"], x["height_mm"]) for x in spec["rooms"]] == \
        [("Office", "room", 12000, 2700), ("Plant room", "plant_room", 6000, 3200)]
    assert spec["rooms"][0]["ceiling_void_mm"] == 600 and any("left to right" in a for a in rooms["assumptions"])
    assert {"mark"} <= {m["field"] for m in rooms["missing"]}                      # the mark was never said: it is asked, not invented
    assert client.post("/skills/space-envelope/shortcut", headers=hd(f), json={"text": "x"}).status_code == 422


# ---- running ---------------------------------------------------------------------------------------------------------

def test_a_run_that_passes_releases_its_files_and_they_download_byte_for_byte(admin, client):
    f = h.seed(admin)
    r = client.post(run_url(f), headers=hd(f), json={"spec": SPEC})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok" and body["released"] and body["validation"]["passed"]
    assert {x["name"] for x in body["files"]} == {"OFFICE-L1.ifc", "OFFICE-L1.dxf", "manifest.json"} and all(x["released"] for x in body["files"])
    ifc = next(x for x in body["files"] if x["name"].endswith(".ifc"))
    got = client.get(f"/artifacts/{ifc['artifact_id']}/download", headers=hd(f))
    assert got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == ifc["sha256"] and got.content.startswith(b"ISO-10303-21")
    assert got.headers["x-content-type-options"] == "nosniff" and "attachment" in got.headers["content-disposition"]
    assert client.get(f"/artifacts/{ifc['artifact_id']}/download", headers=hd(f, "checker")).status_code == 200        # the firm's other roles read
    other = h.seed(admin)
    assert client.get(f"/artifacts/{ifc['artifact_id']}/download", headers=hd(other)).status_code == 404            # another firm: nothing
    assert client.get(f"/artifacts/{ifc['artifact_id']}/download").status_code == 401
    runs = client.get(f"/revisions/{f['revision']}/skill-runs", headers=hd(f)).json()
    assert runs[0]["status"] == "ok" and len(runs[0]["artifacts"]) == 3 and runs[0]["spec_sha256"]
    assert admin.execute("select count(*) from ledger_event where revision_id = %s and kind in ('skill_run_recorded', 'artifact_recorded')",
                         (f["revision"],)).fetchone()[0] == 4


@pytest.mark.skipif(not registry.availability("duct-fab")[0], reason="cadquery is not installed here")
def test_duct_fab_runs_through_the_same_door(admin, client):
    f = h.seed(admin)
    r = client.post(run_url(f, "duct-fab"), headers=hd(f), json={"spec": DUCT})
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    assert {x["role"] for x in r.json()["files"]} >= {"step", "dxf"}


def test_a_rejected_spec_is_recorded_and_produces_nothing(admin, client):
    f = h.seed(admin)
    bad = json.loads(json.dumps(SPEC))
    bad["rooms"][1]["outline"]["x_mm"] = 11000                           # overlaps the first room
    r = client.post(run_url(f), headers=hd(f), json={"spec": bad})
    assert r.status_code == 200 and r.json()["status"] == "spec_rejected" and "overlap" in r.json()["message"] and r.json()["files"] == []
    assert admin.execute("select count(*) from artifact where run_id = %s", (r.json()["run_id"],)).fetchone()[0] == 0


def test_only_a_designer_runs_on_an_open_revision_of_their_own_firm(admin, client):
    f, other = h.seed(admin), h.seed(admin)
    assert client.post(run_url(f), headers=hd(f, "checker"), json={"spec": SPEC}).status_code == 403
    assert client.post(run_url(f), headers=hd(other), json={"spec": SPEC}).status_code == 404
    assert client.post(f"/revisions/{f['revision']}/skills/evil/run", headers=hd(f), json={"spec": SPEC}).status_code == 404
    assert client.post(f"/revisions/{f['revision']}/skills/..%2Fduct-fab/run", headers=hd(f), json={"spec": SPEC}).status_code == 404
    admin.execute("update revision set frozen_at = now(), status = 'frozen' where id = %s", (f["revision"],))
    frozen = client.post(run_url(f), headers=hd(f), json={"spec": SPEC})
    assert frozen.status_code == 409 and frozen.json()["detail"]["code"] == "revision_frozen"


def test_a_skill_that_cannot_run_here_says_so(admin, client, monkeypatch):
    f = h.seed(admin)
    monkeypatch.setattr(registry, "availability", lambda name: (False, "not installed on this server: ifcopenshell"))
    monkeypatch.setattr(runner, "availability", registry.availability)
    r = client.post(run_url(f), headers=hd(f), json={"spec": SPEC})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "skill_unavailable"


# ---- the validator gate -----------------------------------------------------------------------------------------------

def test_files_changed_between_build_and_release_are_never_released(admin, client, monkeypatch):
    real = runner._child

    def tamper(args, **kw):
        if args[0] == "validate":                                   # after the build, before the release
            dxf = next(p for p in __import__("pathlib").Path(args[2]).iterdir() if p.suffix == ".dxf")
            dxf.write_bytes(dxf.read_bytes() + b"\n")
        return real(args, **kw)

    monkeypatch.setattr(runner, "_child", tamper)
    f = h.seed(admin)
    r = client.post(run_url(f), headers=hd(f), json={"spec": SPEC})
    body = r.json()
    assert body["status"] == "revalidation_failed" and not body["released"] and "checksum" in body["message"]
    assert body["files"] == [] or all(not x["released"] for x in body["files"])
    assert admin.execute("select count(*) from artifact_blob b join artifact a on a.id = b.artifact_id where a.run_id = %s", (body["run_id"],)).fetchone()[0] == 0
    for x in body["files"]:
        assert client.get(f"/artifacts/{x['artifact_id']}/download", headers=hd(f)).status_code == 404


def test_a_second_validator_failure_blocks_release_even_when_the_files_are_intact(admin, client, monkeypatch):
    real = runner._child

    def refuse(args, **kw):
        if args[0] == "validate":
            return __import__("subprocess").CompletedProcess(args, 0, json.dumps({"passed": False, "checks": [], "failed": ["dxf_labels"]}), "")
        return real(args, **kw)

    monkeypatch.setattr(runner, "_child", refuse)
    f = h.seed(admin)
    body = client.post(run_url(f), headers=hd(f), json={"spec": SPEC}).json()
    assert body["status"] == "revalidation_failed" and not body["released"] and "dxf_labels" in body["message"]
    rows = admin.execute("select released, validator ->> 'passed' from artifact where run_id = %s", (body["run_id"],)).fetchall()
    assert rows and all(r == (False, "false") for r in rows)
    assert admin.execute("select count(*) from artifact_blob b join artifact a on a.id = b.artifact_id where a.run_id = %s", (body["run_id"],)).fetchone()[0] == 0
    assert all(client.get(f"/artifacts/{x['artifact_id']}/download", headers=hd(f)).status_code == 404 for x in body["files"])


def test_the_database_itself_refuses_bytes_for_an_unreleased_artifact_and_edits_to_released_ones(admin, client):
    f = h.seed(admin)
    body = client.post(run_url(f), headers=hd(f), json={"spec": SPEC}).json()
    art = body["files"][0]["artifact_id"]
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        admin.execute("update artifact_blob set content = 'x'::bytea where artifact_id = %s", (art,))
    with pytest.raises(psycopg.errors.Error):
        admin.execute("update artifact set released = false where id = %s", (art,))                        # a released artifact is immutable
    unreleased = admin.execute("insert into artifact (firm_id, revision_id, kind, path, checksum, validator, released) values (%s, %s, 'dxf',"
                               " 'x', 'c', '{\"passed\": false}', false) returning id", (f["firm"], f["revision"])).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException, match="validator passed"):
        admin.execute("insert into artifact_blob (artifact_id, firm_id, content, media_type) values (%s, %s, 'abc'::bytea, 'x/y')", (unreleased, f["firm"]))
    with pytest.raises(psycopg.errors.Error):
        admin.execute("insert into artifact (firm_id, revision_id, kind, path, checksum, validator, released) values (%s, %s, 'dxf', 'y', 'c',"
                      " '{\"passed\": false}', true)", (f["firm"], f["revision"]))                          # released needs a passed validator


def test_a_hostile_spec_cannot_hang_or_crash_the_server(admin, client):
    f = h.seed(admin)
    huge = {"spec_version": "1", "pad": "x" * (1024 * 1024 + 10)}
    r = client.post(run_url(f), headers=hd(f), json={"spec": huge})
    assert r.status_code == 200 and r.json()["status"] == "spec_rejected" and "1 MB" in r.json()["message"]
    nested = {"a": {}}
    node = nested["a"]
    for _ in range(40):
        node["a"] = {}
        node = node["a"]
    assert client.post(run_url(f), headers=hd(f), json={"spec": nested}).json()["status"] == "spec_rejected"
    assert client.post(run_url(f), headers=hd(f), json={"spec": {"spec_version": "1", "mark": "../../etc/passwd"}}).json()["status"] == "spec_rejected"


# ---- use as the revision's model -------------------------------------------------------------------------------------

def test_a_built_ifc_can_become_the_revisions_model_and_still_needs_gate_1(admin, client):
    os.environ.setdefault("MEP_COOKIE_SECURE", "0")
    f = h.seed(admin)
    run = client.post(run_url(f), headers=hd(f), json={"spec": SPEC}).json()
    r = client.post(f"/revisions/{f['revision']}/skill-runs/{run['run_id']}/use-as-model?which=ifc", headers=hd(f))
    assert r.status_code == 200, r.text
    assert r.json()["spaces"] == 3 and r.json()["kind"] == "ifc"
    rows = admin.execute("select name, area_m2_provenance::text, confirmed_by is null from space where revision_id = %s order by name", (f["revision"],)).fetchall()
    assert rows == [("Meeting room 1", "extracted", True), ("Open plan office", "extracted", True), ("Server room", "extracted", True)]
    assert client.post(f"/revisions/{f['revision']}/skill-runs/{run['run_id']}/use-as-model?which=ifc", headers=hd(f, "checker")).status_code == 403
    assert client.post(f"/revisions/{f['revision']}/skill-runs/{run['run_id']}/use-as-model?which=step", headers=hd(f)).status_code == 422
    assert client.post(f"/revisions/{f['revision']}/skill-runs/00000000-0000-0000-0000-000000000000/use-as-model", headers=hd(f)).status_code == 404
