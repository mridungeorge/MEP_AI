"""Phase 4a through the API: classify -> Gate 2 (line by line, bulk after a spot-check) -> Gate 3 with the registration number ->
signed package (JSON, PDF, artifact) -> certifier share link -> ledger check. Needs the local Supabase."""
import io

import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack
from pypdf import PdfReader

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL

ROOT = lin.ROOT


@pytest.fixture(scope="module")
def pack():
    return load_pack(ROOT / "rules")


@pytest.fixture(scope="module")
def client(pack):
    return TestClient(create_pg_app(DB_URL, h.SECRET, pack, supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def with_approver(admin, f, registration="RPEQ 12345"):
    f["approver"] = h.uid()
    admin.execute("insert into auth.users (id, email) values (%s, %s)", (f["approver"], f"approver-{f['approver']}@test.invalid"))
    admin.execute("insert into app_user (id, firm_id, role, registration_no) values (%s, %s, 'approver', %s)",
                  (f["approver"], f["firm"], registration))
    return f


def frozen(admin, client, pack):
    return with_approver(admin, lin.frozen_parent(admin, client, pack))


def auth(f, role):
    return h.auth(f[role])


def review_all(client, f, only_open=True):
    ws = client.get(f"/revisions/{f['revision']}/review", headers=auth(f, "checker")).json()
    for r in ws["results"]:
        if r["decision"] is None or not only_open:
            resp = client.post(f"/revisions/{f['revision']}/review/decisions", headers=auth(f, "checker"),
                               json={"result_id": r["id"], "decision": "approve", "reason": "checked against the clause and inputs"})
            assert resp.status_code == 200, resp.text
    return ws


def test_the_whole_chain_designer_checker_approver_package_and_share_link(admin, client, pack):
    f = frozen(admin, client, pack)
    rev = f["revision"]
    ck, ap, de = auth(f, "checker"), auth(f, "approver"), auth(f, "designer")

    ws = client.get(f"/revisions/{rev}/review", headers=ck).json()
    assert ws["total"] > 0 and "unclassified" not in ws["by_class"]
    assert ws["revision"]["frozen"] and [s["gate"] for s in ws["signoffs"]] == ["gate1"]

    # gate order and roles
    assert client.post(f"/revisions/{rev}/sign/gate2", headers=ck, json={}).status_code == 409           # nothing reviewed yet
    assert client.post(f"/revisions/{rev}/sign/gate2", headers=de, json={}).status_code == 403
    assert client.post(f"/revisions/{rev}/sign/gate3", headers=ap, json={"registration": "RPEQ 12345"}).status_code == 409
    one = ws["results"][0]
    assert client.post(f"/revisions/{rev}/review/decisions", headers=de,
                       json={"result_id": one["id"], "decision": "approve", "reason": "designer cannot"}).status_code == 403
    assert client.post(f"/revisions/{rev}/review/decisions", headers=ck,
                       json={"result_id": one["id"], "decision": "approve", "reason": "x"}).status_code == 422     # a reason is required

    review_all(client, f)
    assert client.post(f"/revisions/{rev}/sign/gate3", headers=ap, json={"registration": "RPEQ 12345"}).status_code == 409   # gate 2 first
    assert client.post(f"/revisions/{rev}/sign/gate2", headers=ck, json={}).status_code == 200
    assert client.post(f"/revisions/{rev}/sign/gate2", headers=ck, json={}).status_code == 409                # one signer per gate
    wrong = client.post(f"/revisions/{rev}/sign/gate3", headers=ap, json={"registration": "RPEQ 99999"})
    assert wrong.status_code == 409 and "does not match" in wrong.text
    assert client.post(f"/revisions/{rev}/sign/gate3", headers=ap, json={"registration": "RPEQ 12345"}).status_code == 200

    # a decision after signing is refused
    late = client.post(f"/revisions/{rev}/review/decisions", headers=ck,
                       json={"result_id": one["id"], "decision": "reject", "reason": "too late for this"})
    assert late.status_code in (403, 409)

    pkg = client.get(f"/revisions/{rev}/package", headers=de).json()
    assert pkg["status"]["complete"] and pkg["ledger"]["verified"]
    assert [s["gate"] for s in pkg["signoffs"]] == ["gate1", "gate2", "gate3"]
    assert pkg["signoffs"][2]["registration_no"] == "RPEQ 12345"
    assert pkg["banner"] == "DRAFT RULES: NOT ENGINEER-APPROVED"                                              # every rule is still a draft
    assert all(r["decision"] == "approve" and r["reason"] for r in pkg["results"])

    pdf = client.get(f"/revisions/{rev}/package.pdf", headers=de)
    assert pdf.status_code == 200 and pdf.headers["x-validator"] == "passed"
    text = " ".join("\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf.content)).pages).split())
    assert "SIGNED: Gate 1" in text and "DRAFT RULES: NOT ENGINEER-APPROVED" in text and "RPEQ 12345" in text
    assert client.get(f"/revisions/{rev}/package.pdf", headers=de).content == pdf.content                      # deterministic
    art = admin.execute("select released, validator ->> 'passed' from artifact where revision_id = %s and kind = 'compliance_report_pdf'",
                        (rev,)).fetchall()
    assert art == [(True, "true")]

    # the certifier's share link: shown once, read-only, time-limited, logged, revocable
    assert client.post(f"/revisions/{rev}/share-links", headers=ck, json={"days": 7}).status_code == 403
    assert client.post(f"/revisions/{rev}/share-links", headers=ap, json={"days": 99}).status_code == 422
    link = client.post(f"/revisions/{rev}/share-links", headers=ap, json={"days": 7, "label": "Certifier"}).json()
    token = link["token"]
    assert len(token) >= 40 and not admin.execute("select 1 from ledger_link where token = %s", (token,)).fetchall()
    public = TestClient(client.app)                                                                           # no Authorization header
    got = public.get(f"/share/{token}")
    assert got.status_code == 200 and got.json()["status"]["complete"] and got.headers["cache-control"] == "no-store"
    shared_pdf = public.get(f"/share/{token}/report.pdf")
    assert shared_pdf.status_code == 200 and shared_pdf.content[:5] == b"%PDF-"
    assert public.get("/share/not-a-real-token").status_code == 404
    assert public.post(f"/share/{token}").status_code == 405 and public.delete(f"/share/{token}").status_code == 405
    views = admin.execute("select count(*) from ledger_event where revision_id = %s and kind = 'share_link_viewed'", (rev,)).fetchone()[0]
    assert views == 2
    listed = client.get(f"/revisions/{rev}/share-links", headers=de).json()
    assert listed[0]["views"] == 2 and "token" not in listed[0]
    assert client.delete(f"/revisions/{rev}/share-links/{listed[0]['id']}", headers=ap).status_code == 200
    assert public.get(f"/share/{token}").status_code == 404

    ver = client.get("/ledger/verify", headers=ck).json()
    assert ver["ok"] is True and ver["checked"] > 10


def test_a_share_link_never_opens_another_revision_and_expired_links_are_dead(admin, client, pack):
    f, g = frozen(admin, client, pack), frozen(admin, client, pack)
    for x in (f, g):
        review_all(client, x)
        client.post(f"/revisions/{x['revision']}/sign/gate2", headers=auth(x, "checker"), json={})
        client.post(f"/revisions/{x['revision']}/sign/gate3", headers=auth(x, "approver"), json={"registration": "RPEQ 12345"})
    token = client.post(f"/revisions/{f['revision']}/share-links", headers=auth(f, "approver"), json={"days": 1}).json()["token"]
    public = TestClient(client.app)
    assert public.get(f"/share/{token}").json()["revision"]["id"] == str(f["revision"])
    assert client.get(f"/revisions/{f['revision']}/share-links", headers=auth(g, "approver")).json() == []   # another firm sees nothing
    assert client.post(f"/revisions/{f['revision']}/share-links", headers=auth(g, "approver"), json={}).status_code in (403, 404, 409)
    admin.execute("update ledger_link set created_at = now() - interval '3 days', expires_at = now() - interval '1 day'"
                  " where revision_id = %s", (f["revision"],))
    assert public.get(f"/share/{token}").status_code == 404


def test_an_unsigned_revision_cannot_be_shared_and_its_package_says_so(admin, client, pack):
    f = frozen(admin, client, pack)
    de, ap = auth(f, "designer"), auth(f, "approver")
    assert client.post(f"/revisions/{f['revision']}/share-links", headers=ap, json={"days": 3}).status_code == 409
    pkg = client.get(f"/revisions/{f['revision']}/package", headers=de).json()
    assert not pkg["status"]["complete"] and pkg["status"]["missing"] == ["gate2", "gate3"]
    pdf = client.get(f"/revisions/{f['revision']}/package.pdf", headers=de)
    text = " ".join("\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf.content)).pages).split())
    assert "NOT FULLY SIGNED" in text
    assert admin.execute("select released from artifact where revision_id = %s", (f["revision"],)).fetchone()[0] is False


def test_bulk_approval_through_the_api(admin, client, pack):
    f = frozen(admin, client, pack)
    rev, ck = f["revision"], auth(f, "checker")
    ws = client.get(f"/revisions/{rev}/review", headers=ck).json()
    clean = [r for r in ws["results"] if r["review_class"] == "clean_pass"]
    if len(clean) < 2:
        pytest.skip("the run produced fewer than two clean passes")
    sid = client.post(f"/revisions/{rev}/review/bulk/prepare", headers=ck).json()["sample_id"]
    assert client.post(f"/revisions/{rev}/review/bulk/approve", headers=ck, json={"sample_id": sid}).status_code == 409   # not examined yet
    sample = client.get(f"/revisions/{rev}/review", headers=ck).json()["sample"]
    assert sample["id"] == sid and 1 <= sample["size"] <= sample["of"]
    for rid in sample["result_ids"]:
        r = client.post(f"/revisions/{rev}/review/decisions", headers=ck, json={
            "result_id": rid, "decision": "approve", "reason": "examined in the spot check", "sample_id": sid})
        assert r.status_code == 200, r.text
    done = client.post(f"/revisions/{rev}/review/bulk/approve", headers=ck, json={"sample_id": sid})
    assert done.status_code == 200 and done.json()["approved"] == len(clean) - sample["size"]
    ws2 = client.get(f"/revisions/{rev}/review", headers=ck).json()
    assert ws2["open_clean"] == 0
    assert any(r["bulk"] for r in ws2["results"]) == (sample["of"] > sample["size"])
