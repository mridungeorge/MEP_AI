"""The firm's drawing standard on released DXF files: the unit behaviour of stamp/verify, and the whole path through an administrator's upload and a skill run.
Needs the local Supabase for the second half."""
import io
import json
from pathlib import Path

import ezdxf
import ezdxf.bbox
import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack
from mep.skills_runner import firm_sheet
from mep.skills_runner.runner import RunFile, SkillRunResult

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL

ROOT = Path(lin.ROOT)
HVAC = json.loads((ROOT / "skills" / "hvac-dxf" / "examples" / "office_layout" / "spec.json").read_text(encoding="utf-8"))


def title_block_dxf(extra_text: str = "PROJECT: {{PROJECT}}") -> bytes:
    doc = ezdxf.new("R2010")
    doc.layers.add("TB-FRAME", color=7)
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (4000, 0), (4000, 1500), (0, 1500)], close=True, dxfattribs={"layer": "TB-FRAME"})
    msp.add_text(extra_text, dxfattribs={"layer": "TB-FRAME", "height": 100, "insert": (100, 1200)})
    msp.add_mtext("REV {{REVISION}} / {{MARK}} / {{SKILL}}", dxfattribs={"layer": "TB-FRAME", "insert": (100, 800), "char_height": 100})
    out = io.StringIO()
    doc.write(out)
    return out.getvalue().encode()


def built_dxf(tmp_path) -> bytes:
    import importlib.util
    import sys
    for p in (str(ROOT), str(ROOT / "skills" / "hvac-dxf")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location("hvac_build_fs", ROOT / "skills" / "hvac-dxf" / "scripts" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hvac_build_fs"] = mod
    spec.loader.exec_module(mod)
    mod.build(HVAC, tmp_path)
    return (tmp_path / "L1-SA-LAYOUT.dxf").read_bytes()


STD = {"layers": {"A-DUCT": {"color": 3, "linetype": "CONTINUOUS"}, "A-TERM": {"color": 6, "linetype": "DASHED"}}, "map": {"M-DUCT-RECT": "A-DUCT", "M-DUCT-ROUND": "A-DUCT", "M-TERMINAL": "A-TERM"}}


def test_stamp_renames_layers_styles_them_and_fills_the_title_block(tmp_path):
    src = built_dxf(tmp_path)
    t = firm_sheet.FirmTemplates(title_block=title_block_dxf(), layer_standard=STD)
    values = firm_sheet.values_for("hvac-dxf", HVAC)
    out = firm_sheet.stamp(src, t, values)
    assert all(c["passed"] for c in firm_sheet.verify(src, out, t, values))
    doc = ezdxf.read(io.StringIO(out.decode()))
    names = {layer.dxf.name for layer in doc.layers}
    assert {"A-DUCT", "A-TERM", "TB-FRAME"} <= names and not ({"M-DUCT-RECT", "M-DUCT-ROUND", "M-TERMINAL"} & names)
    assert doc.layers.get("A-TERM").dxf.color == 6 and str(doc.layers.get("A-TERM").dxf.linetype).upper() == "DASHED"
    text = " ".join(t_ for _, t_ in firm_sheet._texts(doc.modelspace()))
    assert "PROJECT: Synthetic demo office" in text and "REV A / L1-SA-LAYOUT / hvac-dxf" in text and "{{" not in text
    # the title block sits below the drawing, at its right edge
    box = ezdxf.bbox.extents([e for e in doc.modelspace() if e.dxf.layer == "TB-FRAME"])
    drawing = ezdxf.bbox.extents([e for e in doc.modelspace() if e.dxf.layer != "TB-FRAME"])
    assert box.extmax.y < drawing.extmin.y and abs(box.extmax.x - drawing.extmax.x) < 1


def test_verify_catches_a_stamp_that_lost_something(tmp_path):
    src = built_dxf(tmp_path)
    t = firm_sheet.FirmTemplates(title_block=title_block_dxf(), layer_standard=STD)
    values = firm_sheet.values_for("hvac-dxf", HVAC)
    doc = ezdxf.read(io.StringIO(firm_sheet.stamp(src, t, values).decode()))
    next(iter(doc.modelspace().query("LWPOLYLINE[layer=='A-DUCT']"))).destroy()
    buf = io.StringIO()
    doc.write(buf)
    failed = [c["name"] for c in firm_sheet.verify(src, buf.getvalue().encode(), t, values) if not c["passed"]]
    assert "firm_sheet_original_entities_kept" in failed


def test_a_title_block_that_cannot_be_placed_costs_the_firm_sheet_not_the_release(tmp_path):
    src = built_dxf(tmp_path)
    empty = ezdxf.new("R2010")
    buf = io.StringIO()
    empty.write(buf)
    result = SkillRunResult("ok", validation={"passed": True}, files=[RunFile("x.dxf", "dxf", "image/vnd.dxf", len(src), "0" * 64, src)])
    got = firm_sheet.apply_to_result(result, "hvac-dxf", HVAC, firm_sheet.FirmTemplates(title_block=buf.getvalue().encode()))
    assert [f.name for f in got.files] == ["x.dxf"] and got.validation["firm_sheet"][0]["applied"] is False and got.released
    same = firm_sheet.apply_to_result(result, "hvac-dxf", HVAC, None)
    assert same is result


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(lin.ROOT / "rules"), supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def revision_with_sizing(admin, client):
    f = h.seed(admin)
    d = h.auth(f["designer"])
    url = f"/revisions/{f['revision']}/services"
    for tag, airflow, start, end in (("D1", 600, [0, 0, 0], [6000, 0, 0]), ("D2", 300, [6000, 0, 0], [6000, 4000, 0])):
        assert client.post(url, headers=d, json={"kind": "duct", "tag": tag, "shape": "rect", "width": 300, "depth": 300, "length": 6, "system_tag": "S1", "airflow_ls": airflow,
                                                 "start": start, "end": end}).status_code == 200
    assert client.post(url, headers=d, json={"kind": "terminal", "tag": "T1", "system_tag": "S1", "airflow_ls": 300, "quantity": 2, "start": [6000, 4000, 0], "end": [6000, 4000, 0]}).status_code == 200
    draft = client.get(f"/revisions/{f['revision']}/sizing/hvac-dxf-draft", headers=d).json()["spec"]
    card = {**draft, "mark": "L1-SA-LAYOUT", "title_block": HVAC["title_block"]}
    return f, d, card


def test_the_schedule_comes_from_the_server_not_the_card(admin, client):
    f, d, card = revision_with_sizing(admin, client)
    url = f"/revisions/{f['revision']}/skills/hvac-dxf"
    forged = {**card, "sizing_schedule": [{**e, "size": {"shape": "rect", "width_mm": 50, "depth_mm": 50}} for e in card["sizing_schedule"]],
              "ducts": [{**x, "size": {"shape": "rect", "width_mm": 50, "depth_mm": 50}} for x in card["ducts"]]}
    refused = client.post(f"{url}/run", headers=d, json={"spec": forged, "use_firm_defaults": False})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "schedule_mismatch"
    assert client.post(f"{url}/card-preview", headers=d, json={"spec": forged}).status_code == 409
    assert client.post(f"/revisions/{f['revision']}/services", headers=d, json={"kind": "duct", "tag": "D3", "shape": "rect", "width": 300, "depth": 300, "length": 2,
                                                                                "system_tag": "S9", "airflow_ls": 100}).status_code == 200      # sized, not placed on any plan
    no_copy = {k: v for k, v in card.items() if k != "sizing_schedule"}
    ok = client.post(f"{url}/run", headers=d, json={"spec": no_copy, "use_firm_defaults": False}).json()
    assert ok["released"] is True                                                         # the server filled the schedule in
    wrong_draw = {**no_copy, "ducts": [{**x, "size": {"shape": "rect", "width_mm": 900, "depth_mm": 900}} if x["tag"] == "D1" else x for x in card["ducts"]]}
    bad = client.post(f"{url}/run", headers=d, json={"spec": wrong_draw, "use_firm_defaults": False}).json()
    assert bad["released"] is False and bad["status"] == "validator_rejected"            # drawn size differs from the server's schedule
    unknown = {**no_copy, "ducts": [*card["ducts"], {**card["ducts"][0], "tag": "D77"}]}
    refused77 = client.post(f"{url}/run", headers=d, json={"spec": unknown, "use_firm_defaults": False})
    assert refused77.status_code == 409 and refused77.json()["detail"]["code"] == "duct_not_sized"           # a duct the revision never sized
    empty = h.seed(admin)
    assert client.post(f"/revisions/{empty['revision']}/skills/hvac-dxf/run", headers=h.auth(empty["designer"]), json={"spec": card, "use_firm_defaults": False}).status_code == 409


def test_a_firms_templates_give_every_run_a_firm_sheet(admin, client, tmp_path):
    f, d, card = revision_with_sizing(admin, client)
    admin.execute("update app_user set is_admin = true where id = %s", (f["designer"],))
    url = f"/revisions/{f['revision']}/skills/hvac-dxf/run"
    run = {"spec": card, "use_firm_defaults": False}
    plain = client.post(url, headers=d, json=run).json()
    assert plain["released"] and [x["name"] for x in plain["files"] if x["role"] == "dxf_firm"] == []
    std = {**STD, "map": {**STD["map"], "NOPE": "A-NOPE"}}
    assert client.post("/admin/templates", headers=d, data={"kind": "layer_standard", "name": "Std"}, files={"file": ("t", json.dumps(std).encode())}).status_code == 200
    assert client.post("/admin/templates", headers=d, data={"kind": "layer_standard", "name": "Bad"}, files={"file": ("t", json.dumps({"layers": {"A": {"color": 3}}, "map": {"X": "X"}}).encode())}).status_code == 422
    assert client.post("/admin/templates", headers=d, data={"kind": "layer_standard", "name": "Bad"}, files={"file": ("t", json.dumps({"layers": {"A": {"color": 3}}, "map": {"X": "DEFPOINTS"}}).encode())}).status_code == 422
    assert client.post("/admin/templates", headers=d, data={"kind": "title_block", "name": "TB"}, files={"file": ("t", title_block_dxf())}).status_code == 200
    got = client.post(url, headers=d, json=run).json()
    assert got["released"] and "L1-SA-LAYOUT.firm.dxf" in [x["name"] for x in got["files"]]
    art = next(x for x in got["files"] if x["name"].endswith(".firm.dxf"))
    body = client.get(f"/artifacts/{art['artifact_id']}/download", headers=d)
    doc = ezdxf.read(io.StringIO(body.text))
    assert "A-DUCT" in {layer.dxf.name for layer in doc.layers} and "PROJECT: Synthetic demo office" in " ".join(t for _, t in firm_sheet._texts(doc.modelspace()))
    assert got["validation"]["firm_sheet"][0]["applied"] is True
    # the original is untouched and another firm gets no firm sheet
    assert next(x for x in got["files"] if x["name"] == "L1-SA-LAYOUT.dxf")["sha256"] == next(x for x in plain["files"] if x["name"] == "L1-SA-LAYOUT.dxf")["sha256"]
    g, gd, gcard = revision_with_sizing(admin, client)
    other = client.post(f"/revisions/{g['revision']}/skills/hvac-dxf/run", headers=gd, json={"spec": gcard, "use_firm_defaults": False}).json()
    assert not [x for x in other["files"] if x["role"] == "dxf_firm"]


@pytest.mark.parametrize("skill,example", [("space-envelope", "office_floor"), ("duct-fab", "rect_to_round")])
def test_the_other_drafting_skills_dxf_files_take_the_firm_standard_too(tmp_path, skill, example):
    import importlib.util
    import sys
    sk = ROOT / "skills" / skill
    for p in (str(ROOT), str(sk)):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(f"build_fs_{skill}", sk / "scripts" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    card = json.loads((sk / "examples" / example / "spec.json").read_text(encoding="utf-8"))
    mod.build(card, tmp_path)
    src = next(tmp_path.glob("*.dxf")).read_bytes()
    layers = {layer.dxf.name for layer in ezdxf.read(io.StringIO(src.decode())).layers if layer.dxf.name not in ("0", "Defpoints")}
    first = min(layers)
    t = firm_sheet.FirmTemplates(title_block=title_block_dxf(), layer_standard={"layers": {"FIRM-ONE": {"color": 5}}, "map": {first: "FIRM-ONE"}})
    values = firm_sheet.values_for(skill, card)
    out = firm_sheet.stamp(src, t, values)
    assert all(c["passed"] for c in firm_sheet.verify(src, out, t, values))
    assert "FIRM-ONE" in {layer.dxf.name for layer in ezdxf.read(io.StringIO(out.decode())).layers}


def test_the_firm_sheet_is_byte_stable_refuses_format_codes_and_notices_edited_geometry(tmp_path):
    src = built_dxf(tmp_path)
    t = firm_sheet.FirmTemplates(title_block=title_block_dxf(), layer_standard=STD)
    values = firm_sheet.values_for("hvac-dxf", HVAC)
    first = firm_sheet.stamp(src, t, values)
    import time
    time.sleep(1.1)
    assert firm_sheet.stamp(src, t, values) == first                                            # no clock, no random guid in the file
    hostile = firm_sheet.values_for("hvac-dxf", {**HVAC, "title_block": {**HVAC["title_block"], "drawing_title": r"\H9000;HUGE\PCOMPLIANT {x} %%uUNDER"}})
    assert "\\" not in hostile["TITLE"] and "{" not in hostile["TITLE"] and "%%" not in hostile["TITLE"]
    # a stamped file whose duct was moved or whose tag text was edited is not accepted
    doc = ezdxf.read(io.StringIO(first.decode()))
    next(iter(doc.modelspace().query("LWPOLYLINE[layer=='A-DUCT']"))).translate(9000, 0, 0)
    buf = io.StringIO()
    doc.write(buf)
    assert "firm_sheet_original_entities_kept" in [c["name"] for c in firm_sheet.verify(src, buf.getvalue().encode(), t, values) if not c["passed"]]
    doc = ezdxf.read(io.StringIO(first.decode()))
    next(e for e in doc.modelspace().query("TEXT") if e.dxf.text.startswith("D1 ")).dxf.text = "D1 50x50 1 L/s"
    buf = io.StringIO()
    doc.write(buf)
    assert "firm_sheet_original_entities_kept" in [c["name"] for c in firm_sheet.verify(src, buf.getvalue().encode(), t, values) if not c["passed"]]
