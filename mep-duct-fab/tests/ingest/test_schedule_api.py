"""Schedule API: injected repository and current_user, no DB, no auth bypass."""
import io
import uuid
from pathlib import Path

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mep.api import schedule as api
from mep.engine.loader import load_pack
from mep.ingest.schedule import build_template

ROOT = Path(__file__).resolve().parents[2]
REV = uuid.uuid4()
FIRM = uuid.uuid4()
AC = "air_conditioning"


class FakeRepo:
    def __init__(self, edition="NCC2022", frozen=False):
        self.edition, self.frozen, self.saved = edition, frozen, []

    def revision_edition(self, revision_id, firm_id):
        return self.edition if revision_id == REV and firm_id == FIRM else None

    def upsert_systems(self, revision_id, firm_id, systems, source):
        if self.frozen:
            raise api.RevisionFrozenError
        self.saved.append((revision_id, firm_id, list(systems), source))
        return len(systems)


@pytest.fixture(scope="module")
def pack():
    return load_pack(ROOT / "rules")


def client(pack, repo, role="designer", firm=FIRM):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_repository] = lambda: repo
    app.dependency_overrides[api.get_pack] = lambda: pack
    app.dependency_overrides[api.current_user] = lambda: api.CurrentUser(uuid.uuid4(), firm, role)
    return TestClient(app)


def xlsx(rows, headers=("system tag", "system type", "supply_airflow [m^3/h]")):
    wb = openpyxl.Workbook()
    wb.active.title = "Systems"
    wb.active.append(list(headers))
    for r in rows:
        wb.active.append(r)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def upload(c, data, name="s.xlsx"):
    return c.post(f"/revisions/{REV}/schedule/import", files={"file": (name, data, "application/octet-stream")})


def body(**over):
    s = {"tag": "AHU-1", "system_type": AC, "inputs": [{"name": "supply_airflow", "value": 3600, "unit": "m^3/h"}]}
    s.update(over)
    return {"systems": [s]}


def test_unauthenticated_default_is_refused(pack):
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.get_repository] = lambda: FakeRepo()
    app.dependency_overrides[api.get_pack] = lambda: pack
    assert TestClient(app).post(f"/revisions/{REV}/schedule/systems", json=body()).status_code == 401


def test_import_saves_extracted_xlsx(pack):
    repo = FakeRepo()
    r = upload(client(pack, repo), xlsx([["AHU-1", AC, 3600]]))
    assert r.status_code == 200 and r.json()["systems"] == 1
    ((_, firm, systems, source),) = repo.saved
    assert firm == FIRM and source == "xlsx"
    (i,) = systems[0].inputs
    assert (i.provenance, i.unit) == ("extracted", "L/s") and i.value == pytest.approx(1000)


def test_import_errors_save_nothing(pack):
    repo = FakeRepo()
    r = upload(client(pack, repo), xlsx([["AHU-1", AC, 3600], ["AHU-2", AC, "x"]]))
    assert r.status_code == 422 and r.json()["detail"]["errors"] and repo.saved == []


def test_import_rejects_non_xlsx_name_and_big_upload(pack):
    c = client(pack, FakeRepo())
    assert upload(c, xlsx([]), "s.xlsm").status_code == 422
    assert upload(c, b"x" * (api.MAX_UPLOAD_BYTES + 1)).status_code == 413


@pytest.mark.parametrize("role", ["client", "checker"])
def test_import_designer_only(pack, role):
    assert upload(client(pack, FakeRepo(), role), xlsx([])).status_code == 403


def test_other_firm_revision_is_404(pack):
    assert upload(client(pack, FakeRepo(), firm=uuid.uuid4()), xlsx([])).status_code == 404


def test_frozen_revision_409(pack):
    assert upload(client(pack, FakeRepo(frozen=True)), xlsx([["A", AC, 1]])).status_code == 409


def test_form_converts_and_forces_default(pack):
    repo = FakeRepo()
    r = client(pack, repo, "designer").post(f"/revisions/{REV}/schedule/systems", json=body())
    assert r.status_code == 200
    ((_, _, systems, source),) = repo.saved
    (i,) = systems[0].inputs
    assert source == "form" and i.provenance == "default" and i.unit == "L/s"


@pytest.mark.parametrize("inp", [
    {"name": "supply_airflow", "value": 5},
    {"name": "supply_airflow", "value": 5, "unit": "kW"},
    {"name": "supply_airflow", "value": "5", "unit": "L/s"},
    {"name": "Supply-Airflow", "value": 5, "unit": "L/s"},
    {"name": "nonexistent_input", "value": 5, "unit": "L/s"},
    {"name": "supply_airflow", "value": 5, "unit": "L/s", "provenance": "extracted"},
    {"name": "supply_airflow", "value": 5, "unit": "L/s", "provenance": "engineer_confirmed"},
])
def test_form_rejects(pack, inp):
    repo = FakeRepo()
    r = client(pack, repo).post(f"/revisions/{REV}/schedule/systems", json=body(inputs=[inp]))
    assert r.status_code == 422 and repo.saved == []


def test_form_duplicate_input_names_and_bad_system_type(pack):
    i = {"name": "supply_airflow", "value": 5, "unit": "L/s"}
    c = client(pack, FakeRepo())
    assert c.post(f"/revisions/{REV}/schedule/systems", json=body(inputs=[i, i])).status_code == 422
    assert c.post(f"/revisions/{REV}/schedule/systems", json=body(system_type="nonsense")).status_code == 422


def test_models_validate_directly():
    with pytest.raises(ValueError):
        api.ScheduleInputModel(name="x", value=1.0, unit=None)
    with pytest.raises(ValueError):
        api.ScheduleInputModel(name="x", value=float("inf"), unit="kW")
    assert api.ScheduleInputModel(name="x", value=True).unit is None
    assert api.ScheduleInputModel(name="x", value="a").provenance == "default"


def test_template_import_is_empty_ok(pack):
    r = upload(client(pack, FakeRepo()), build_template(pack, "NCC2022"))
    assert r.status_code == 200 and r.json()["systems"] == 0


def test_form_rejects_infinity(pack):
    raw = '{"systems":[{"tag":"A","system_type":"air_conditioning","inputs":[{"name":"supply_airflow","value":Infinity,"unit":"L/s"}]}]}'
    r = client(pack, FakeRepo()).post(f"/revisions/{REV}/schedule/systems", content=raw,
                                      headers={"content-type": "application/json"})
    assert r.status_code == 422


def test_a_checker_cannot_use_the_form(pack):
    assert client(pack, FakeRepo(), "checker").post(f"/revisions/{REV}/schedule/systems", json=body()).status_code == 403
