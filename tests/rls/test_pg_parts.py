"""Sprint 2.5: a system names the building part it serves. Mixed-use projects run per part when every part is allowed and
refuse wholesale when any part is refused. Needs the local Supabase (scripts/ci.sh does `supabase db reset`)."""
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls.conftest import DB_URL

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import e2e_lib


@pytest.fixture(scope="module")
def pack():
    return load_pack(ROOT / "rules")


@pytest.fixture(scope="module")
def client(pack):
    return TestClient(create_pg_app(DB_URL, h.SECRET, pack))


def put_parts(client, f, classes):
    parts = [{"building_class": c, "storeys": 2, "area_m2": 300.0 + 100 * i} for i, c in enumerate(classes)]
    r = client.put(f"{h.base(f)}/gate1/parts", json={"parts": parts}, headers=h.auth(f["designer"]))
    assert r.status_code == 200, r.text


def add_system(client, pack, f, tag, edition):
    r = client.post(f"{h.base(f)}/schedule/systems", headers=h.auth(f["designer"]),
                    json=e2e_lib.form_system(pack, edition, tag, h.AC))
    assert r.status_code == 200, r.text


def assign(client, f, tag, part):
    return client.put(f"{h.base(f)}/gate1/systems/{tag}/part", json={"part": part}, headers=h.auth(f["designer"]))


def add_space(client, f):
    r = client.post(f"{h.base(f)}/gate1/spaces", headers=h.auth(f["designer"]),
                    json={"name": "Office", "area_m2": 50, "storey": "L1"})
    assert r.status_code == 200


def run(client, f):
    return client.post(f"{h.base(f)}/run-rules", headers=h.auth(f["designer"]))


def ready(client, pack, admin, classes, *, state="VIC", edition="NCC2025", approval=date(2026, 10, 1), tags=("ahu-1", "ahu-2")):
    f = h.seed(admin, state=state, edition=edition, approval=approval)
    put_parts(client, f, classes)
    add_space(client, f)
    for tag in tags:
        add_system(client, pack, f, tag, edition)
    return f


def test_a_mixed_use_project_runs_per_part_when_every_part_is_allowed(admin, client, pack):
    f = ready(client, pack, admin, ["5", "6"])
    assert assign(client, f, "ahu-1", 0).status_code == 200
    assert assign(client, f, "ahu-2", 1).status_code == 200
    assert h.confirm(client, f, h.rows_to_confirm(client, f)).status_code == 200
    r = run(client, f)
    assert r.status_code == 200, r.text
    report = r.json()["report"]
    by_system = {x["subject_id"]: {y["part"] for y in report["results"] if y["subject_id"] == x["subject_id"]}
                 for x in report["results"]}
    assert by_system == {"ahu-1": {0}, "ahu-2": {1}}                        # each system's results are labelled with its part
    assert report["project"]["building_parts"] and len(report["project"]["building_parts"]) == 2


def test_the_whole_run_is_refused_when_any_one_part_is_refused(admin, client, pack):
    # NSW on NCC 2022: a class 5 part is allowed, a class 2 part follows another code (refused). One refused part refuses all.
    kw = {"state": "NSW", "edition": "NCC2022", "approval": date(2023, 11, 1)}
    alone = ready(client, pack, admin, ["5"], tags=("ahu-1",), **kw)
    assert h.confirm(client, alone, h.rows_to_confirm(client, alone)).status_code == 200
    assert run(client, alone).status_code == 200                              # control: the allowed part runs on its own

    f = ready(client, pack, admin, ["5", "2"], **kw)
    assert assign(client, f, "ahu-1", 0).status_code == 200                  # the system on the ALLOWED part
    assert assign(client, f, "ahu-2", 1).status_code == 200
    assert h.confirm(client, f, h.rows_to_confirm(client, f)).status_code == 200
    r = run(client, f)
    assert r.status_code == 409 and r.json()["code"] in ("applicability", "jurisdiction"), r.text
    assert any("part 1" in reason for reason in r.json()["reasons"])          # it names the refused part
    assert "report" not in r.json()                                           # nothing was produced for the allowed part


def test_every_system_must_name_its_part_in_a_mixed_use_project(admin, client, pack):
    f = ready(client, pack, admin, ["5", "6"])
    assert assign(client, f, "ahu-1", 0).status_code == 200                   # ahu-2 names none
    assert h.confirm(client, f, h.rows_to_confirm(client, f)).status_code == 200
    r = run(client, f)
    assert r.status_code == 409 and r.json()["code"] == "gate1_required"
    assert any("ahu-2" in reason and "building part" in reason for reason in r.json()["reasons"])


def test_a_part_that_does_not_exist_is_refused(admin, client, pack):
    f = ready(client, pack, admin, ["5", "6"], tags=("ahu-1",))
    r = assign(client, f, "ahu-1", 2)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_input"
    assert assign(client, f, "no-such-system", 0).status_code == 422
    g = h.seed(admin)                                                         # no parts entered yet
    add_system(client, pack, g, "ahu-1", "NCC2025")
    r = assign(client, g, "ahu-1", 0)
    assert r.status_code == 422 and "building parts first" in r.json()["detail"]["message"]


def test_changing_the_parts_withdraws_every_part_assignment(admin, client, pack):
    f = ready(client, pack, admin, ["5", "6"])
    assign(client, f, "ahu-1", 0)
    assign(client, f, "ahu-2", 1)
    assert h.confirm(client, f, h.rows_to_confirm(client, f)).status_code == 200
    assert run(client, f).status_code == 200
    put_parts(client, f, ["6", "5"])                                          # the same list in the other order
    v = client.get(f"{h.base(f)}/gate1", headers=h.auth(f["designer"])).json()
    parts_inputs = [i for i in v["inputs"] if i["name"] == "building_part"]
    assert len(parts_inputs) == 2 and {i["provenance"] for i in parts_inputs} == {"default"}
    r = run(client, f)
    assert r.status_code == 409 and r.json()["code"] == "gate1_required"


def test_a_one_part_project_needs_no_assignment(admin, client, pack):
    f = ready(client, pack, admin, ["5"], tags=("ahu-1",))
    assert h.confirm(client, f, h.rows_to_confirm(client, f)).status_code == 200
    assert run(client, f).status_code == 200


def test_only_a_designer_assigns_a_part_and_another_firm_cannot(admin, client, pack):
    f, other = ready(client, pack, admin, ["5", "6"], tags=("ahu-1",)), h.seed(admin)
    assert client.put(f"{h.base(f)}/gate1/systems/ahu-1/part", json={"part": 0},
                      headers=h.auth(f["checker"])).status_code == 403
    assert client.put(f"{h.base(f)}/gate1/systems/ahu-1/part", json={"part": 0},
                      headers=h.auth(other["designer"])).status_code == 404
    assert client.put(f"{h.base(f)}/gate1/systems/ahu-1/part", json={"part": "0"},
                      headers=h.auth(f["designer"])).status_code == 422
