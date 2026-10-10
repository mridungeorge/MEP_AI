"""Clash-lite: IFC bounding boxes, duct boxes with insulation and clearance, BCF 2.1 export, warnings only. Needs the local Supabase."""
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mep import clash
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL

PACK = load_pack(lin.ROOT / "rules")
IFC = Path(lin.ROOT) / "tests" / "fixtures" / "ifc" / "bsi-hvac-ifc4.ifc"


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, PACK, supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def box(lo, hi):
    return clash.Box("g", "IfcPipeSegment", "p", lo, hi)


def test_duct_box_and_gap_arithmetic():
    run = {"id": "d", "tag": "D1", "shape": "rect", "width_mm": 600, "depth_mm": 300, "insulation_mm": 25, "x0": 0, "y0": 0, "z0": 3000, "x1": 4000, "y1": 0, "z1": 3000}
    b = clash.duct_box(run)
    assert b.lo == (-325, -325, 2825) and b.hi == (4325, 325, 3175)
    assert clash.duct_box({**run, "x0": None}) is None
    assert clash.gap(b, box((4325, 0, 3000), (5000, 100, 3100))) == 0                       # touching
    assert clash.gap(b, box((4425, 0, 3000), (5000, 100, 3100))) == 100
    assert clash.gap(b, box((4000, 0, 3000), (5000, 100, 3100))) == -175                     # least penetration over the three axes (the z overlap)
    found = clash.detect([{**run, "id": "d1"}], [{"discipline": "fire", "file_name": "f.ifc", "elements": [box((4425, 0, 3000), (5000, 100, 3100))]}], 150)
    assert found[0]["kind"] == "CLEARANCE" and found[0]["level"] == "WARNING"
    assert clash.detect([{**run, "id": "d1"}], [{"discipline": "fire", "file_name": "f.ifc", "elements": [box((4425, 0, 3000), (5000, 100, 3100))]}], 50) == []


def test_bcf_is_a_2_1_package_with_one_topic_per_clash():
    c = {"duct_id": "d1", "duct_tag": "D<1>", "discipline": "fire", "model": "f.ifc", "element_guid": "abc", "element_class": "IfcPipeSegment", "element_name": "p",
         "kind": "OVERLAP", "gap_mm": -10, "clearance_mm": 50, "point_mm": [1, 2, 3], "level": "WARNING"}
    z = zipfile.ZipFile(io.BytesIO(clash.bcf_zip([c], "P & Q", "me")))
    names = z.namelist()
    assert "bcf.version" in names and "project.bcfp" in names and sum(n.endswith("/markup.bcf") for n in names) == 1
    assert b'VersionId="2.1"' in z.read("bcf.version")
    markup = z.read(next(n for n in names if n.endswith("markup.bcf"))).decode()
    assert "D&lt;1&gt;" in markup and 'TopicStatus="Open"' in markup and "Warning only" in markup


def test_upload_detect_and_export(admin, client):
    f = h.seed(admin)
    d = h.auth(f["designer"])
    url = f"/revisions/{f['revision']}"
    boxes, _, _ = clash.read_boxes(IFC)
    assert boxes
    target = boxes[0]
    centre = [(target.lo[i] + target.hi[i]) / 2 for i in range(3)]
    data = IFC.read_bytes()
    assert client.post(f"{url}/clash/models", headers=h.auth(f["checker"]), data={"discipline": "fire"}, files={"file": ("fire.ifc", data)}).status_code == 403
    assert client.post(f"{url}/clash/models", headers=d, data={"discipline": "plumbing"}, files={"file": ("x.ifc", data)}).status_code == 422
    assert client.post(f"{url}/clash/models", headers=d, data={"discipline": "fire"}, files={"file": ("x.ifc", b"not an ifc")}).status_code == 422
    ok = client.post(f"{url}/clash/models", headers=d, data={"discipline": "fire"}, files={"file": ("fire.ifc", data)})
    assert ok.status_code == 200 and ok.json()["elements"] == len(boxes)
    assert client.post(f"{url}/clash/models", headers=d, data={"discipline": "fire"}, files={"file": ("fire.ifc", data)}).status_code == 409
    duct = {"kind": "duct", "tag": "D1", "shape": "rect", "width": 400, "depth": 300, "length": 2, "start_mm": centre, "end_mm": [centre[0] + 1000, centre[1], centre[2]]}
    far = {**duct, "tag": "D2", "start_mm": [centre[0] + 9e6, 0, 0], "end_mm": [centre[0] + 9e6 + 1000, 0, 0]}
    bare = {k: v for k, v in duct.items() if k not in ("start_mm", "end_mm")} | {"tag": "D3"}
    for body in (duct, far, bare):
        assert client.post(f"{url}/services", headers=d, json=body).status_code == 200
    view = client.get(f"{url}/clash", headers=d).json()
    assert view["ducts_checked"] == 2 and view["ducts_without_coordinates"] == 1
    assert {c["duct_tag"] for c in view["clashes"]} == {"D1"} and all(c["level"] == "WARNING" for c in view["clashes"])
    assert any(c["kind"] == "OVERLAP" for c in view["clashes"])
    assert "PASS" not in str(view) and "FAIL" not in str(view)
    z = zipfile.ZipFile(io.BytesIO(client.get(f"{url}/clash.bcfzip", headers=d).content))
    assert sum(n.endswith("/markup.bcf") for n in z.namelist()) == len(view["clashes"])
    assert client.get(f"{url}/clash", headers=h.auth(h.seed(admin)["designer"])).status_code == 404
    admin.execute("update revision set frozen_at = now(), status = 'frozen' where id = %s", (f["revision"],))
    assert client.post(f"{url}/clash/models", headers=d, data={"discipline": "electrical"}, files={"file": ("e.ifc", data + b"\n")}).status_code == 409
