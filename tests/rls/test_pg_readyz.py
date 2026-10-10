"""/readyz: ready when the database answers and the schema is there; 503 (no details) when it is not. /healthz never touches the database."""
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL


def test_ready_when_the_database_answers():
    c = TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(lin.ROOT / "rules")))
    r = c.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ready"}
    assert c.get("/healthz").json() == {"status": "ok"}


def test_not_ready_without_a_database_and_the_answer_leaks_nothing():
    c = TestClient(create_pg_app("postgresql://nobody:pw@127.0.0.1:9/none", h.SECRET, load_pack(lin.ROOT / "rules")))
    r = c.get("/readyz")
    assert r.status_code == 503 and r.json()["status"] == "not_ready" and "nobody" not in r.text and "127.0.0.1" not in r.text
    assert c.get("/healthz").status_code == 200                      # liveness does not depend on the database
