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


def test_a_firms_templates_give_every_run_a_firm_sheet(admin, client, tmp_path):
    f = h.seed(admin)
    admin.execute("update app_user set is_admin = true where id = %s", (f["designer"],))
    d = h.auth(f["designer"])
    url = f"/revisions/{f['revision']}/skills/hvac-dxf/run"
    plain = client.post(url, headers=d, json={"spec": HVAC, "use_firm_defaults": False}).json()
    assert plain["released"] and [x["name"] for x in plain["files"] if x["role"] == "dxf_firm"] == []
    std = {**STD, "map": {**STD["map"], "NOPE": "A-NOPE"}}
    assert client.post("/admin/templates", headers=d, data={"kind": "layer_standard", "name": "Std"}, files={"file": ("t", json.dumps(std).encode())}).status_code == 200
    assert client.post("/admin/templates", headers=d, data={"kind": "layer_standard", "name": "Bad"}, files={"file": ("t", json.dumps({"layers": {"A": {"color": 3}}, "map": {"X": "X"}}).encode())}).status_code == 422
    assert client.post("/admin/templates", headers=d, data={"kind": "title_block", "name": "TB"}, files={"file": ("t", title_block_dxf())}).status_code == 200
    got = client.post(url, headers=d, json={"spec": HVAC, "use_firm_defaults": False}).json()
    assert got["released"] and "L1-SA-LAYOUT.firm.dxf" in [x["name"] for x in got["files"]]
    art = next(x for x in got["files"] if x["name"].endswith(".firm.dxf"))
    body = client.get(f"/artifacts/{art['artifact_id']}/download", headers=d)
    doc = ezdxf.read(io.StringIO(body.text))
    assert "A-DUCT" in {layer.dxf.name for layer in doc.layers} and "PROJECT: Synthetic demo office" in " ".join(t for _, t in firm_sheet._texts(doc.modelspace()))
    assert got["validation"]["firm_sheet"][0]["applied"] is True
    # the original is untouched and another firm gets no firm sheet
    assert next(x for x in got["files"] if x["name"] == "L1-SA-LAYOUT.dxf")["sha256"] == next(x for x in plain["files"] if x["name"] == "L1-SA-LAYOUT.dxf")["sha256"]
    g = h.seed(admin)
    other = client.post(f"/revisions/{g['revision']}/skills/hvac-dxf/run", headers=h.auth(g["designer"]), json={"spec": HVAC, "use_firm_defaults": False}).json()
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
    first = sorted(layers)[0]
    t = firm_sheet.FirmTemplates(title_block=title_block_dxf(), layer_standard={"layers": {"FIRM-ONE": {"color": 5}}, "map": {first: "FIRM-ONE"}})
    values = firm_sheet.values_for(skill, card)
    out = firm_sheet.stamp(src, t, values)
    assert all(c["passed"] for c in firm_sheet.verify(src, out, t, values))
    assert "FIRM-ONE" in {layer.dxf.name for layer in ezdxf.read(io.StringIO(out.decode())).layers}
