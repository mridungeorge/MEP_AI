"""Phase 3: revision lineage through the API. A frozen revision is never changed: a new architect upload becomes a child revision,
the designer confirms the diff, the run is refused until then, and results/trace come from stored data. Needs the local Supabase."""
import os
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls.conftest import DB_URL

ROOT = Path(__file__).resolve().parents[2]
SUPABASE_URL = os.environ.get("MEP_TEST_SUPABASE_URL", "http://127.0.0.1:54321")
ANON = jwt.encode({"iss": "supabase-demo", "role": "anon", "exp": 1983812996}, h.SECRET, algorithm="HS256")


def dxf(rooms):
    import tempfile

    import ezdxf
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    for x, w, d in rooms:
        msp.add_lwpolyline([(x, 0), (x + w, 0), (x + w, d), (x, d)], close=True, dxfattribs={"layer": "A-SPACE"})
        msp.add_text(f"Room {int(x)}", dxfattribs={"insert": (x + 0.5, 1.0), "height": 0.25})      # the label names the space
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "p.dxf"
        doc.saveas(path)
        return path.read_bytes()


@pytest.fixture(scope="module")
def pack():
    return load_pack(ROOT / "rules")


@pytest.fixture(scope="module")
def client(pack):
    return TestClient(create_pg_app(DB_URL, h.SECRET, pack, supabase_url=SUPABASE_URL, anon_key=ANON))


def code(response):
    body = response.json()
    return body.get("code") or body["detail"]["code"]


def up(client, f, rev, data, name="p.dxf", **form):
    return client.post(f"/revisions/{rev}/uploads", headers=h.auth(f["designer"]), files={"file": (name, data)}, data=form)


def confirm_all(client, f, rev):
    g = f"/revisions/{rev}/gate1"
    v = client.get(g, headers=h.auth(f["designer"])).json()
    rows = ([{"kind": "project", "id": v["project"]["id"], "etag": v["project"]["etag"]}]
            + [{"kind": "building_part", "id": p["id"], "etag": p["etag"]} for p in v["parts"]]
            + [{"kind": "space", "id": x["id"], "etag": x["etag"]} for x in v["spaces"]]
            + [{"kind": "system_input", "id": i["id"], "etag": i["etag"]} for i in v["inputs"]])
    pending = {x["id"] for x in [v["project"], *v["parts"], *v["spaces"], *v["inputs"]] if not x.get("confirmed")}
    rows = [r for r in rows if r["id"] in pending]               # only what still needs confirming (a confirmed row is not re-touched)
    return client.post(f"{g}/confirm", json={"rows": rows}, headers=h.auth(f["designer"]))


def frozen_parent(admin, client, pack):
    """A project with a system, two DXF spaces, everything confirmed, rules run and the revision frozen."""
    f = h.seed(admin)
    h.populate(client, pack, f)
    d = h.auth(f["designer"])
    for sp in client.get(f"{h.base(f)}/gate1", headers=d).json()["spaces"]:       # the form space is not part of the DXF story
        admin.execute("delete from space where id = %s", (sp["id"],))
    assert up(client, f, f["revision"], dxf([(0, 6, 5), (10, 4, 4)])).status_code == 200
    assert confirm_all(client, f, f["revision"]).status_code == 200
    run = client.post(f"{h.base(f)}/run-rules", headers=d)
    assert run.status_code == 200, run.text
    assert client.post(f"{h.base(f)}/freeze", headers=d).status_code == 200
    return f


def test_a_new_upload_on_a_frozen_revision_makes_a_child_with_the_diff_to_confirm(admin, client, pack):
    f = frozen_parent(admin, client, pack)
    d = h.auth(f["designer"])
    # the architect moved a wall: room 1 gets bigger; room 2 is untouched; a third room is added
    r = up(client, f, f["revision"], dxf([(0, 8, 5), (10, 4, 4), (20, 3, 3)]), architect_rev="B")
    assert r.status_code == 200, r.text
    child = r.json()["new_revision"]
    cid = child["id"]
    assert child["architect_rev"] == "B" and child["spaces_carried_unchanged"] == 1

    # the parent is untouched
    assert admin.execute("select count(*) from space where revision_id = %s", (f["revision"],)).fetchone()[0] == 2
    assert admin.execute("select frozen_at is not null from revision where id = %s", (f["revision"],)).fetchone()[0]

    lin = client.get(f"/revisions/{cid}/lineage", headers=d).json()
    assert [a["id"] for a in lin["ancestors"]] == [str(f["revision"])] and lin["ancestors"][0]["frozen"]

    diff = client.get(f"/revisions/{cid}/diff", headers=d).json()
    kinds = sorted(c["change"] for c in diff["spaces"] if c["change"] != "unchanged")
    assert kinds == ["added", "changed"]
    assert diff["confirmed"] is False and diff["needs_confirmation"]
    assert diff["stale"], "a changed area makes the dependent results stale"
    assert diff["traces"] and all(t["chains"] and t["lines"] for t in diff["traces"])

    # nothing runs, and the diff cannot be confirmed, until the changed spaces are confirmed at Gate 1
    assert client.post(f"/revisions/{cid}/run-rules", headers=d).status_code == 409
    stale_hash = client.get(f"/revisions/{cid}/diff", headers=d).json()["hash"]
    assert code(client.post(f"/revisions/{cid}/diff/confirm", headers=d, json={"hash": stale_hash})) == "gate1_required"
    assert client.post(f"/revisions/{cid}/diff/confirm", headers=d).status_code == 422                 # the hash is required
    c = confirm_all(client, f, cid)
    assert c.status_code == 200, c.text
    live = client.get(f"/revisions/{cid}/diff", headers=d).json()["hash"]
    assert code(client.post(f"/revisions/{cid}/diff/confirm", headers=d, json={"hash": "0" * 64})) == "diff_changed"
    ok = client.post(f"/revisions/{cid}/diff/confirm", headers=d, json={"hash": live})
    assert ok.status_code == 200 and ok.json()["hash"] == client.get(f"/revisions/{cid}/diff", headers=d).json()["hash"]
    assert client.get(f"/revisions/{cid}/diff", headers=d).json()["confirmed"] is True

    before = client.get(f"/revisions/{cid}/results", headers=d).json()
    assert before["source"] == "carried_from_parent" and any(x["stale"] for x in before["results"])
    run = client.post(f"/revisions/{cid}/run-rules", headers=d)
    assert run.status_code == 200, run.text
    after = client.get(f"/revisions/{cid}/results", headers=d).json()
    assert after["source"] == "own" and after["results"]


def test_the_checker_cannot_confirm_or_freeze_and_other_firms_see_nothing(admin, client, pack):
    f = frozen_parent(admin, client, pack)
    other = h.seed(admin)
    r = up(client, f, f["revision"], dxf([(0, 7, 5), (10, 4, 4)]))
    cid = r.json()["new_revision"]["id"]
    ck = h.auth(f["checker"])
    assert client.post(f"/revisions/{cid}/diff/confirm", headers=ck, json={"hash": "0" * 64}).status_code == 403
    assert client.post(f"/revisions/{cid}/freeze", headers=ck).status_code == 403
    assert client.get(f"/revisions/{cid}/diff", headers=h.auth(other["designer"])).status_code == 404
    assert client.get(f"/revisions/{cid}/lineage", headers=h.auth(other["designer"])).status_code == 404
    assert client.post(f"/revisions/{cid}/freeze", headers=h.auth(f["designer"])).status_code == 409   # diff not confirmed


def test_a_frozen_revision_cannot_be_re_run_and_only_a_designer_runs(admin, client, pack):
    f = frozen_parent(admin, client, pack)
    before = admin.execute("select run_id from rule_result where revision_id = %s and current limit 1", (f["revision"],)).fetchone()
    again = client.post(f"/revisions/{f['revision']}/run-rules", headers=h.auth(f["designer"]))
    assert again.status_code == 409 and code(again) == "revision_frozen"
    assert client.post(f"/revisions/{f['revision']}/run-rules", headers=h.auth(f["checker"])).status_code == 403
    after = admin.execute("select run_id from rule_result where revision_id = %s and current limit 1", (f["revision"],)).fetchone()
    assert before == after


def test_the_same_file_twice_makes_one_child_not_two(admin, client, pack):
    f = frozen_parent(admin, client, pack)
    data = dxf([(0, 8, 5), (10, 4, 4), (20, 3, 3)])
    assert up(client, f, f["revision"], data).status_code == 200
    second = up(client, f, f["revision"], data)
    assert second.status_code == 409 and code(second) == "already_uploaded"
    assert admin.execute("select count(*) from revision where parent_revision_id = %s", (f["revision"],)).fetchone()[0] == 1
