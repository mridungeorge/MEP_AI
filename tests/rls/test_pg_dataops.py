"""Backup and restore check, per-firm export, firm retirement and purge. Needs the local Supabase and Docker (the Postgres tools run in its container)."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import psycopg
import pytest

from tests.rls import test_pg_api as h
from tests.rls.conftest import DB_URL

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import dataops
import review_db

SHARED = "supabase_db_mep-copilot-kit"


def ns(**kw):
    return argparse.Namespace(**kw)


@pytest.fixture
def scratch():
    """A throw-away Postgres with the migrations applied and a known superuser password; always removed."""
    info = review_db.up()
    name = str(info["name"])
    review_db.docker("exec", name, "psql", "-U", "supabase_admin", "-d", "postgres", "-c", "alter role postgres password 'scratchpw'")
    try:
        yield {"name": name, "dsn": f"postgresql://postgres:scratchpw@127.0.0.1:{info['port']}/postgres"}
    finally:
        review_db.down(name)


def seeded_firm(admin):
    f = h.seed(admin)
    admin.execute("update app_user set is_admin = true, email = %s where id = %s", (f"admin-{f['firm']}@test.invalid", f["designer"]))
    admin.execute("insert into signoff (firm_id, revision_id, gate, user_id) values (%s, %s, 'gate1', %s)", (f["firm"], f["revision"], f["designer"]))
    admin.execute("update firm set name = %s where id = %s", (f"Export Test {f['firm']}", f["firm"]))
    return f, f"Export Test {f['firm']}"


def test_a_backup_restores_into_a_scratch_database_and_matches(admin, tmp_path, scratch):
    seeded_firm(admin)
    out = tmp_path / "bk"
    assert dataops.cmd_backup(ns(dsn=DB_URL, out=str(out), docker=SHARED)) == 0
    m = json.loads((out / "manifest.json").read_text())
    assert m["counts"]["firm"] >= 1 and set(m["files"]) == {"public.dump", "auth_users.json"}
    assert dataops.cmd_restore_check(ns(dir=str(out), target_dsn=scratch["dsn"], docker=scratch["name"], docker_user="supabase_admin", allow_nonempty=False)) == 0
    with psycopg.connect(scratch["dsn"], autocommit=True) as c:
        assert c.execute("select count(*) from firm").fetchone()[0] == m["counts"]["firm"]
    # a second restore into the now-full database is refused, and a damaged backup is caught
    assert dataops.cmd_restore_check(ns(dir=str(out), target_dsn=scratch["dsn"], docker=scratch["name"], docker_user="supabase_admin", allow_nonempty=False)) == 1
    (out / "public.dump").write_bytes((out / "public.dump").read_bytes()[:-50] + b"x" * 50)
    assert dataops.cmd_restore_check(ns(dir=str(out), target_dsn=scratch["dsn"], docker=scratch["name"], docker_user="supabase_admin", allow_nonempty=False)) == 1


def test_a_tampered_restore_is_detected(admin, tmp_path, scratch):
    seeded_firm(admin)
    out = tmp_path / "bk"
    dataops.cmd_backup(ns(dsn=DB_URL, out=str(out), docker=SHARED))
    m = json.loads((out / "manifest.json").read_text())
    m["counts"]["space"] += 1                                                       # the manifest claims a row the dump does not hold
    (out / "manifest.json").write_text(json.dumps(m))
    assert dataops.cmd_restore_check(ns(dir=str(out), target_dsn=scratch["dsn"], docker=scratch["name"], docker_user="supabase_admin", allow_nonempty=False)) == 1


def test_a_firm_export_holds_every_row_and_file_and_no_tokens(admin, tmp_path):
    f, name = seeded_firm(admin)
    out = tmp_path / "ex"
    assert dataops.cmd_export(ns(dsn=DB_URL, firm=name, out=str(out))) == 0
    m = json.loads((out / "manifest.json").read_text())
    assert m["firm_id"] == str(f["firm"]) and m["tables"]["revision"]["rows"] == 1 and m["tables"]["signoff"]["rows"] == 1
    assert "ledger_link" in m["tables"] and "token" not in (out / "data" / "ledger_link.jsonl").read_text()
    assert {p["email"] for p in json.loads((out / "data" / "people.json").read_text())} >= {f"admin-{f['firm']}@test.invalid"}
    other, _ = seeded_firm(admin)
    assert str(other["revision"]) not in (out / "data" / "revision.jsonl").read_text()          # nothing of another firm
    with pytest.raises(SystemExit):
        dataops.cmd_export(ns(dsn=DB_URL, firm="No Such Firm", out=str(tmp_path / "x")))


def test_retiring_a_firm_erases_personal_data_keeps_the_signed_record_and_needs_the_export(admin, tmp_path):
    f, name = seeded_firm(admin)
    admin.execute("insert into notification (firm_id, user_id, kind, subject, body) values (%s, %s, 'signed', 's', 'b')", (f["firm"], f["designer"]))
    out = tmp_path / "ex"
    with pytest.raises(SystemExit):                                                              # no export yet
        dataops.cmd_retire(ns(dsn=DB_URL, firm=name, export=str(out), confirm=name))
    dataops.cmd_export(ns(dsn=DB_URL, firm=name, out=str(out)))
    with pytest.raises(SystemExit):                                                              # the confirmation must repeat the name
        dataops.cmd_retire(ns(dsn=DB_URL, firm=name, export=str(out), confirm="wrong"))
    assert dataops.cmd_retire(ns(dsn=DB_URL, firm=name, export=str(out), confirm=name)) == 0
    assert admin.execute("select count(*) from notification where firm_id = %s", (f["firm"],)).fetchone()[0] == 0
    assert admin.execute("select bool_or(active), bool_or(email is not null) from app_user where firm_id = %s", (f["firm"],)).fetchone() == (False, False)
    assert admin.execute("select count(*) from auth.users where id = %s and email like 'retired+%%' and banned_until = 'infinity'", (f["designer"],)).fetchone()[0] == 1
    assert admin.execute("select count(*) from signoff where firm_id = %s", (f["firm"],)).fetchone()[0] == 1        # the signed record stays
    assert admin.execute("select ok from verify_ledger(%s)", (f["firm"],)).fetchone()[0] is True
    assert admin.execute("select count(*) from ledger_event where firm_id = %s and kind = 'firm_retired'", (f["firm"],)).fetchone()[0] == 1
    assert admin.execute("select deleted_at is not null from firm where id = %s", (f["firm"],)).fetchone()[0] is True


def test_purge_waits_for_the_retention_period_and_then_erases_everything(admin, tmp_path):
    f, name = seeded_firm(admin)
    with pytest.raises(SystemExit):                                                              # not retired yet
        dataops.cmd_purge(ns(dsn=DB_URL, firm=name, confirm=name, retention_years=7))
    out = tmp_path / "ex"
    dataops.cmd_export(ns(dsn=DB_URL, firm=name, out=str(out)))
    dataops.cmd_retire(ns(dsn=DB_URL, firm=name, export=str(out), confirm=name))
    with pytest.raises(SystemExit):                                                              # retired, but the period has not passed
        dataops.cmd_purge(ns(dsn=DB_URL, firm=name, confirm=name, retention_years=7))
    with pytest.raises(SystemExit):                                                              # a shorter period than the minimum is not accepted
        dataops.cmd_purge(ns(dsn=DB_URL, firm=name, confirm=name, retention_years=1))
    admin.execute("update firm set deleted_at = now() - interval '8 years' where id = %s", (f["firm"],))
    assert dataops.cmd_purge(ns(dsn=DB_URL, firm=name, confirm=name, retention_years=7)) == 0
    assert admin.execute("select count(*) from firm where id = %s", (f["firm"],)).fetchone()[0] == 0
    assert admin.execute("select count(*) from ledger_event where firm_id = %s", (f["firm"],)).fetchone()[0] == 0
    assert time.time() > 0 and subprocess.run is not None
