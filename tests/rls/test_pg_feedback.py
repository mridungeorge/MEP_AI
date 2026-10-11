"""In-app feedback and the public pricing read. Needs the local Supabase."""
import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(lin.ROOT / "rules"), supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def test_members_send_feedback_and_only_administrators_read_the_firms(admin, client):
    f = h.seed(admin)
    admin.execute("update app_user set is_admin = true where id = %s", (f["designer"],))
    d, c = h.auth(f["designer"]), h.auth(f["checker"])
    assert client.post("/feedback", json={"kind": "bug", "page": "/projects", "message": "The table is hard to read"}, headers=c).status_code == 200
    assert client.post("/feedback", json={"message": "ok"}, headers=c).status_code == 422                                   # too short
    assert client.post("/feedback", json={"message": "bad\x01text"}, headers=c).status_code == 422
    assert client.post("/feedback", json={"message": "fine message", "extra": 1}, headers=c).status_code == 422
    assert client.post("/feedback", json={"message": "no token"}).status_code in (401, 403)
    listed = client.get("/admin/feedback", headers=d).json()["feedback"]
    assert [x["message"] for x in listed] == ["The table is hard to read"] and listed[0]["kind"] == "bug"
    assert client.get("/admin/feedback", headers=c).status_code == 403
    other = h.seed(admin)
    admin.execute("update app_user set is_admin = true where id = %s", (other["designer"],))
    assert client.get("/admin/feedback", headers=h.auth(other["designer"])).json()["feedback"] == []                         # another firm sees nothing
    import psycopg
    with pytest.raises(psycopg.errors.Error):                                                                                # append-only
        admin.execute("update feedback set message = 'edited' where firm_id = %s", (f["firm"],))


def test_the_hourly_limit_and_the_public_pricing(admin, client):
    f = h.seed(admin)
    c = h.auth(f["checker"])
    codes = [client.post("/feedback", json={"message": f"message number {i}"}, headers=c).status_code for i in range(22)]
    assert codes[:20] == [200] * 20 and set(codes[20:]) == {429}
    p = client.get("/public/pricing")
    assert p.status_code == 200 and p.json()["banner"].startswith("DRAFT COPY") and {x["id"] for x in p.json()["plans"]} == {"starter", "studio"}
    assert "price_env_seat" not in str(p.json())
