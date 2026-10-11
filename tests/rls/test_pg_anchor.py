"""Ledger anchoring: export to a write-once store and verify; every kind of tampering is noticed. Needs the local Supabase."""
import importlib.util
import json
import os
from pathlib import Path

import pytest

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL

SPEC = importlib.util.spec_from_file_location("ledger_anchor", Path(lin.ROOT) / "scripts" / "ledger_anchor.py")
anchor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(anchor)


def firm_with_events(admin, n=3):
    f = h.seed(admin)
    for i in range(n):
        admin.execute("insert into ledger_event (firm_id, revision_id, kind, payload) values (%s, %s, 'test_event', %s::jsonb)", (f["firm"], f["revision"], json.dumps({"n": i})))
    return f


def mine(problems, firm):
    return [p for p in problems if str(firm) in p]


def test_export_is_write_once_incremental_and_verifies(admin, tmp_path):
    f = firm_with_events(admin)
    first = [p for p in anchor.export_anchors(DB_URL, tmp_path) if str(f["firm"]) in p]
    assert len(first) == 1 and not os.access(first[0], os.W_OK) or os.geteuid() == 0
    assert [p for p in anchor.export_anchors(DB_URL, tmp_path) if str(f["firm"]) in p] == []                 # nothing new: nothing written
    with pytest.raises(FileExistsError):
        anchor.write_once(Path(first[0]), b"overwrite")
    assert mine(anchor.verify_anchors(DB_URL, tmp_path), f["firm"]) == []
    admin.execute("insert into ledger_event (firm_id, revision_id, kind, payload) values (%s, %s, 'more', '{}'::jsonb)", (f["firm"], f["revision"]))
    second = [p for p in anchor.export_anchors(DB_URL, tmp_path) if str(f["firm"]) in p]
    assert len(second) == 1 and mine(anchor.verify_anchors(DB_URL, tmp_path), f["firm"]) == []
    assert len(anchor.anchor_files(tmp_path, str(f["firm"]))) == 2


def test_a_rewritten_chain_is_caught_by_the_anchor_even_when_the_database_chain_is_made_consistent(admin, tmp_path):
    f = firm_with_events(admin)
    anchor.export_anchors(DB_URL, tmp_path)
    seq = admin.execute("select max(seq) from ledger_event where firm_id = %s", (f["firm"],)).fetchone()[0]
    # an insider rewrites the last row, recomputes its hash and the head: the database alone sees a consistent chain
    admin.execute("set session_replication_role = replica")
    admin.execute("update ledger_event set payload = '{\"n\": 999}'::jsonb where firm_id = %s and seq = %s", (f["firm"], seq))
    admin.execute("update ledger_event set row_hash = ledger_row_hash(prev_hash, seq, id, firm_id, revision_id, kind, payload, created_at) where firm_id = %s and seq = %s", (f["firm"], seq))
    new_hash = admin.execute("select row_hash from ledger_event where firm_id = %s and seq = %s", (f["firm"], seq)).fetchone()[0]
    admin.execute("update ledger_head set row_hash = %s where firm_id = %s", (new_hash, f["firm"]))
    admin.execute("set session_replication_role = origin")
    assert admin.execute("select ok from verify_ledger(%s)", (f["firm"],)).fetchone()[0] is True               # the database cannot see it
    problems = mine(anchor.verify_anchors(DB_URL, tmp_path), f["firm"])
    assert any("no longer has the hash anchored" in p for p in problems)


def test_edited_removed_or_missing_anchors_are_caught(admin, tmp_path):
    f = firm_with_events(admin)
    anchor.export_anchors(DB_URL, tmp_path)
    admin.execute("insert into ledger_event (firm_id, revision_id, kind, payload) values (%s, %s, 'more', '{}'::jsonb)", (f["firm"], f["revision"]))
    anchor.export_anchors(DB_URL, tmp_path)
    files = anchor.anchor_files(tmp_path, str(f["firm"]))
    assert len(files) == 2
    os.chmod(files[0], 0o644)
    a = json.loads(files[0].read_text())
    a["exported_at"] = "2000-01-01T00:00:00Z"
    files[0].write_text(json.dumps(a, sort_keys=True, indent=1) + "\n")
    assert any("does not follow the anchor before it" in p for p in mine(anchor.verify_anchors(DB_URL, tmp_path), f["firm"]))
    files[0].unlink()
    assert any("does not follow" in p for p in mine(anchor.verify_anchors(DB_URL, tmp_path), f["firm"]))           # the second anchor now points at a missing one
    other = firm_with_events(admin)
    assert any("no anchor in the store" in p for p in mine(anchor.verify_anchors(DB_URL, tmp_path), other["firm"]))


def test_a_firm_whose_chain_does_not_verify_is_not_anchored(admin, tmp_path, capsys):
    f = firm_with_events(admin)
    admin.execute("set session_replication_role = replica")
    admin.execute("update ledger_event set payload = '{\"x\": 1}'::jsonb where firm_id = %s and seq = 1", (f["firm"],))
    admin.execute("set session_replication_role = origin")
    assert [p for p in anchor.export_anchors(DB_URL, tmp_path) if str(f["firm"]) in p] == []
    assert str(f["firm"]) in capsys.readouterr().err
