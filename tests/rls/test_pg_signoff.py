"""Phase 4a in the database: Gate 2 review, the sign-off state machine, the ledger hash chain and its audit triggers, share links.
Needs the local Supabase (scripts/ci.sh does `supabase db reset`)."""
import contextlib
import hashlib
import json
import uuid
from collections.abc import Iterator
from datetime import date

import psycopg
import pytest

from tests.rls.conftest import DB_URL


def uid() -> str:
    return str(uuid.uuid4())


@contextlib.contextmanager
def act(user: str) -> Iterator[psycopg.Cursor]:
    """Act as a signed-in client and COMMIT (unlike conftest.as_user), so several steps build on each other."""
    conn = psycopg.connect(DB_URL, autocommit=False)
    try:
        cur = conn.cursor()
        cur.execute("set local role authenticated")
        cur.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": user, "role": "authenticated"}),))
        yield cur
        conn.commit()
    finally:
        conn.rollback()
        conn.close()


def call(user: str, sql: str, params: tuple = ()):
    with act(user) as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def seed(admin, results=6, classes=None):
    """A firm with a designer, two checkers... (one per role), a revision with `results` current results, frozen by the designer."""
    f = {k: uid() for k in ("firm", "project", "revision", "designer", "checker", "approver")}
    admin.execute("insert into firm (id, name) values (%s, 'signoff test firm')", (f["firm"],))
    for role in ("designer", "checker", "approver"):
        admin.execute("insert into auth.users (id, email) values (%s, %s)", (f[role], f"{role}-{f[role]}@test.invalid"))
        admin.execute("insert into app_user (id, firm_id, role, registration_no) values (%s, %s, %s, %s)",
                      (f[role], f["firm"], role, "RPEQ 12345" if role == "approver" else None))
    admin.execute("insert into project (id, firm_id, address, state, climate_zone, ncc_edition, approval_date)"
                  " values (%s, %s, '1 Test St', 'VIC', 6, 'NCC2025', %s)", (f["project"], f["firm"], date(2026, 10, 1)))
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'A')",
                  (f["revision"], f["firm"], f["project"]))
    f["results"] = []
    for n in range(results):
        rid = uid()
        klass = (classes or {}).get(n, "clean_pass")
        admin.execute(
            "insert into rule_result (id, firm_id, revision_id, rule_id, edition, result, inputs, citation, subject_id, current,"
            " review_class) values (%s, %s, %s, %s, 'NCC2025', %s, '{}', '{}', 'ahu-1', true, %s)",
            (rid, f["firm"], f["revision"], f"NCC2025-R{n}", "FAIL" if klass == "fail" else "PASS", klass))
        f["results"].append(rid)
    admin.execute("update rule_result set inputs_hash = live_inputs_hash(revision_id) where revision_id = %s", (f["revision"],))
    return f


@contextlib.contextmanager
def unguarded(admin):
    """Simulate the service touching a frozen revision's results (the guard would stop a real run)."""
    admin.execute("alter table rule_result disable trigger rule_result_frozen_guard")
    try:
        yield
    finally:
        admin.execute("alter table rule_result enable trigger rule_result_frozen_guard")


def frozen(admin, **kw):
    f = seed(admin, **kw)
    call(f["designer"], "select freeze_revision(%s)", (f["revision"],))
    return f


def refused(user, sql, params=(), match=""):
    with pytest.raises(psycopg.errors.Error) as e:
        call(user, sql, params)
    assert match in str(e.value), str(e.value)


def review_all(f, reason="checked against the clause"):
    for rid in f["results"]:
        call(f["checker"], "select gate2_review(%s, 'approve', %s)", (rid, reason))


def head(admin, firm):
    return admin.execute("select seq, row_hash from ledger_event where firm_id = %s order by seq desc limit 1", (firm,)).fetchone()


def verify(admin, firm):
    return admin.execute("select ok, checked, broken_seq, reason from verify_ledger(%s)", (firm,)).fetchone()


# ---- gate order, roles, frozen-before-sign ------------------------------------------------------------------------

def test_freezing_is_the_designers_gate1_signoff_and_is_anchored_in_the_ledger(admin):
    f = frozen(admin)
    row = admin.execute("select user_id, signer_role::text, anchor_seq from signoff where revision_id = %s and gate = 'gate1'",
                        (f["revision"],)).fetchone()
    assert str(row[0]) == f["designer"] and row[1] == "designer" and row[2] >= 1
    kinds = [r[0] for r in admin.execute("select kind from ledger_event where revision_id = %s order by seq", (f["revision"],))]
    assert kinds == ["revision_frozen", "signoff_recorded"]


def test_nothing_is_reviewed_or_signed_before_the_revision_is_frozen(admin):
    f = seed(admin)
    refused(f["checker"], "select gate2_review(%s, 'approve', 'fine by me')", (f["results"][0],), "must be frozen")
    refused(f["checker"], "select gate2_prepare_bulk(%s)", (f["revision"],), "must be frozen")
    refused(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],), "must be frozen")
    refused(f["approver"], "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],), "must be frozen")


def test_each_gate_belongs_to_its_role_and_gate1_cannot_be_signed_by_hand(admin):
    f = frozen(admin)
    refused(f["designer"], "select gate2_review(%s, 'approve', 'fine by me')", (f["results"][0],), "only a checker")
    refused(f["approver"], "select gate2_review(%s, 'approve', 'fine by me')", (f["results"][0],), "only a checker")
    refused(f["designer"], "select sign_gate(%s, 'gate2')", (f["revision"],), "only a checker signs")
    refused(f["checker"], "select sign_gate(%s, 'gate1')", (f["revision"],), "Gate 1 is signed by freezing")
    refused(f["approver"], "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],), "Gate 2 has not been signed")


def test_other_firms_see_and_touch_nothing(admin):
    f, g = frozen(admin), seed(admin)
    refused(g["checker"], "select gate2_review(%s, 'approve', 'not mine at all')", (f["results"][0],), "not found")
    refused(g["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],), "not found")
    refused(g["checker"], "select verify_ledger(%s)", (f["firm"],), "another firm")


# ---- Gate 2, line by line ------------------------------------------------------------------------------------------

def test_every_line_needs_a_reason_and_a_new_decision_supersedes_the_old(admin):
    f = frozen(admin, results=2)
    for reason in (None, "", "  ", "ok"):
        refused(f["checker"], "select gate2_review(%s, 'approve', %s)", (f["results"][0], reason), "reason")
    refused(f["checker"], "select gate2_review(%s, 'maybe', 'a perfectly good reason')", (f["results"][0],), "unknown decision")
    call(f["checker"], "select gate2_review(%s, 'reject', 'wrong airflow used')", (f["results"][0],))
    review_all(f)                                    # approving later supersedes the rejection
    latest = admin.execute("select decision, reason from review_latest where rule_result_id = %s", (f["results"][0],)).fetchone()
    assert latest == ("approve", "checked against the clause")
    assert admin.execute("select count(*) from review where rule_result_id = %s", (f["results"][0],)).fetchone()[0] == 2
    for sql in ("update review set decision = 'approve'", "delete from review"):
        with pytest.raises(psycopg.errors.RaiseException):
            admin.execute(sql + " where rule_result_id = %s", (f["results"][0],))


def test_gate2_cannot_be_signed_until_every_current_result_is_approved(admin):
    f = frozen(admin, results=3)
    refused(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],), "3 result(s)")
    review_all(f)
    call(f["checker"], "select gate2_review(%s, 'request_changes', 'airflow looks high for this zone')", (f["results"][1],))
    refused(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],), "1 result(s)")
    call(f["checker"], "select gate2_review(%s, 'approve', 'airflow confirmed with the designer')", (f["results"][1],))
    with unguarded(admin):
        admin.execute("update rule_result set stale = true where id = %s", (f["results"][2],))
    refused(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],), "1 result(s)")
    with unguarded(admin):
        admin.execute("update rule_result set stale = false where id = %s", (f["results"][2],))
    admin.execute("update rule_result set review_class = null where id = %s", (f["results"][2],))
    refused(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],), "unclassified")
    admin.execute("update rule_result set review_class = 'clean_pass' where id = %s", (f["results"][2],))
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))
    refused(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],), "already signed")           # one signer per gate


# ---- bulk approval of clean passes ---------------------------------------------------------------------------------

def test_bulk_approval_needs_a_random_spot_check_that_is_done_and_clean(admin):
    f = frozen(admin, results=12, classes={0: "fail", 1: "near_miss"})        # 10 clean passes, 2 exceptions
    sample = call(f["checker"], "select gate2_prepare_bulk(%s)", (f["revision"],))[0][0]
    row = admin.execute("select candidate_ids, sample_ids from review_sample where id = %s", (sample,)).fetchone()
    cand, picked = [str(x) for x in row[0]], [str(x) for x in row[1]]
    assert len(cand) == 10 and len(picked) == 3 and set(picked) <= set(cand)
    assert f["results"][0] not in cand and f["results"][1] not in cand              # exceptions are never bulk-approved
    refused(f["checker"], "select gate2_bulk_approve(%s)", (sample,), "3 sampled result(s) have not been examined")
    refused(f["checker"], "select gate2_review(%s, 'approve', 'looks fine to me', %s)", (next(r for r in cand if r not in picked), sample),
            "not part of an open spot-check")
    call(f["checker"], "select gate2_review(%s, 'approve', 'examined in the spot check', %s)", (picked[0], sample))
    call(f["checker"], "select gate2_review(%s, 'reject', 'this one is wrong', %s)", (picked[1], sample))
    call(f["checker"], "select gate2_review(%s, 'approve', 'examined in the spot check', %s)", (picked[2], sample))
    refused(f["checker"], "select gate2_bulk_approve(%s)", (sample,), "found 1 problem")     # a problem in the sample: no bulk at all
    assert admin.execute("select count(*) from review where bulk", ()).fetchone()[0] >= 0
    assert admin.execute("select count(*) from review where sample_id = %s and bulk", (sample,)).fetchone()[0] == 0


def test_bulk_approval_after_a_clean_spot_check_approves_the_rest_with_the_reason_recorded(admin):
    f = frozen(admin, results=12, classes={0: "fail"})
    sample = call(f["checker"], "select gate2_prepare_bulk(%s)", (f["revision"],))[0][0]
    picked = [str(x) for x in admin.execute("select unnest(sample_ids) from review_sample where id = %s", (sample,)).fetchall() and
              [r[0] for r in admin.execute("select unnest(sample_ids) from review_sample where id = %s", (sample,))]]
    for rid in picked:
        call(f["checker"], "select gate2_review(%s, 'approve', 'examined in the spot check', %s)", (rid, sample))
    done = call(f["checker"], "select gate2_bulk_approve(%s)", (sample,))[0][0]
    assert done == 11 - len(picked)
    bulk = admin.execute("select reason, bulk, user_id from review where sample_id = %s and bulk limit 1", (sample,)).fetchone()
    assert "bulk approval of a clean pass after a random spot-check" in bulk[0] and bulk[1] and str(bulk[2]) == f["checker"]
    refused(f["checker"], "select gate2_bulk_approve(%s)", (sample,), "already used")
    # the one exception is still open, so Gate 2 cannot be signed until it is reviewed line by line
    refused(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],), "1 result(s)")
    call(f["checker"], "select gate2_review(%s, 'approve', 'accepted: designer will lower the load', null)", (f["results"][0],))
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))


def test_the_bulk_door_closes_when_the_clean_set_changes_or_for_other_users(admin):
    f = frozen(admin, results=8)
    sample = call(f["checker"], "select gate2_prepare_bulk(%s)", (f["revision"],))[0][0]
    picked = [r[0] for r in admin.execute("select unnest(sample_ids) from review_sample where id = %s", (sample,))]
    for rid in picked:
        call(f["checker"], "select gate2_review(%s, 'approve', 'examined in the spot check', %s)", (rid, sample))
    admin.execute("update rule_result set review_class = 'near_miss' where id = %s",
                  (next(r for r in f["results"] if uuid.UUID(r) not in picked),))
    refused(f["checker"], "select gate2_bulk_approve(%s)", (sample,), "changed since the sample was drawn")
    refused(f["designer"], "select gate2_bulk_approve(%s)", (sample,), "no such spot-check sample")
    refused(f["designer"], "select gate2_prepare_bulk(%s)", (f["revision"],), "only a checker")


# ---- Gate 3 and immutability ---------------------------------------------------------------------------------------

def test_gate3_needs_gate2_and_the_approvers_registration_number(admin):
    f = frozen(admin, results=2)
    review_all(f)
    refused(f["approver"], "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],), "Gate 2 has not been signed")
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))
    refused(f["checker"], "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],), "only a approver")
    refused(f["approver"], "select sign_gate(%s, 'gate3')", (f["revision"],), "does not match")
    refused(f["approver"], "select sign_gate(%s, 'gate3', 'RPEQ 99999')", (f["revision"],), "does not match")
    admin.execute("update app_user set registration_no = null where id = %s", (f["approver"],))
    refused(f["approver"], "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],), "no registration number on file")
    admin.execute("update app_user set registration_no = 'RPEQ 12345' where id = %s", (f["approver"],))
    call(f["approver"], "select sign_gate(%s, 'gate3', ' rpeq 12345 ')", (f["revision"],))
    row = admin.execute("select registration_no, signer_role::text from signoff where revision_id = %s and gate = 'gate3'",
                        (f["revision"],)).fetchone()
    assert row == ("RPEQ 12345", "approver")
    refused(f["approver"], "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],), "already signed")


def test_a_signed_revision_is_immutable_and_sign_offs_cannot_be_rewritten(admin):
    f = frozen(admin, results=2)
    review_all(f)
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))
    for sql in ("update rule_result set review_class = 'fail' where id = %s", "update rule_result set stale = true where id = %s",
                "delete from rule_result where id = %s"):
        with pytest.raises(psycopg.errors.RaiseException, match="signed and immutable|is frozen"):
            admin.execute(sql, (f["results"][0],))
    with pytest.raises(psycopg.errors.RaiseException, match="signed and immutable|is frozen"):
        admin.execute("insert into rule_result (firm_id, revision_id, rule_id, edition, result, inputs, citation, subject_id)"
                      " values (%s, %s, 'NCC2025-RX', 'NCC2025', 'PASS', '{}', '{}', 'ahu-9')", (f["firm"], f["revision"]))
    with pytest.raises(psycopg.errors.RaiseException, match="signed and immutable"):
        admin.execute("insert into review (firm_id, rule_result_id, revision_id, gate, user_id, decision, reason)"
                      " values (%s, %s, %s, 'gate2', %s, 'reject', 'too late for this')",
                      (f["firm"], f["results"][0], f["revision"], f["checker"]))
    for sql in ("update signoff set user_id = %s where revision_id = %s", "delete from signoff where user_id = %s or revision_id = %s"):
        with pytest.raises(psycopg.errors.RaiseException):
            admin.execute(sql, (f["designer"], f["revision"]))
    # a client cannot forge a hash, a sequence number or a sign-off through the ledger either
    with pytest.raises(psycopg.errors.Error):
        call(f["designer"], "insert into ledger_event (firm_id, kind, payload) values (%s, 'x', '{}')", (f["firm"],))


# ---- the ledger hash chain -----------------------------------------------------------------------------------------

def test_the_chain_verifies_and_every_gate_action_is_in_it(admin):
    f = frozen(admin, results=2)
    review_all(f)
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))
    ok = verify(admin, f["firm"])
    assert ok[0] is True and ok[1] >= 5 and ok[2] is None
    kinds = {r[0] for r in admin.execute("select kind from ledger_event where firm_id = %s", (f["firm"],))}
    assert {"revision_frozen", "signoff_recorded", "review_recorded"} <= kinds
    assert call(f["checker"], "select ok, checked from verify_ledger(%s)", (f["firm"],))[0][0] is True     # a client may verify their own
    rows = admin.execute("select seq, prev_hash, row_hash from ledger_event where firm_id = %s order by seq", (f["firm"],)).fetchall()
    assert [r[0] for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0][1] == "0" * 64 and all(rows[i][1] == rows[i - 1][2] for i in range(1, len(rows)))


def test_a_caller_cannot_choose_the_sequence_or_the_hashes(admin):
    f = seed(admin, results=1)
    admin.execute("insert into ledger_event (firm_id, revision_id, kind, payload, seq, prev_hash, row_hash)"
                  " values (%s, %s, 'forged', '{}', 99, %s, %s)", (f["firm"], f["revision"], "a" * 64, "b" * 64))
    assert verify(admin, f["firm"])[0] is True
    assert admin.execute("select seq from ledger_event where firm_id = %s and kind = 'forged'", (f["firm"],)).fetchone()[0] == 1


@pytest.mark.parametrize("tamper", ["payload", "delete_middle", "reorder", "hash", "kind"])
def test_tampering_with_the_ledger_is_detected(admin, tamper):
    f = frozen(admin, results=2)
    review_all(f)
    assert verify(admin, f["firm"])[0] is True
    admin.execute("alter table ledger_event disable trigger ledger_event_no_update")
    try:
        mid = admin.execute("select seq, id from ledger_event where firm_id = %s order by seq offset 2 limit 1", (f["firm"],)).fetchone()
        if tamper == "payload":
            admin.execute("update ledger_event set payload = payload || '{\"edited\": true}' where id = %s", (mid[1],))
        elif tamper == "kind":
            admin.execute("update ledger_event set kind = 'nothing_happened' where id = %s", (mid[1],))
        elif tamper == "hash":
            admin.execute("update ledger_event set row_hash = %s where id = %s", ("c" * 64, mid[1]))
        elif tamper == "reorder":
            admin.execute("update ledger_event set seq = seq + 1000 where id = %s", (mid[1],))
        else:
            admin.execute("alter table ledger_event disable trigger ledger_event_no_update")
            admin.execute("delete from ledger_event where id = %s", (mid[1],))
        result = verify(admin, f["firm"])
    finally:
        admin.execute("alter table ledger_event enable trigger ledger_event_no_update")
    assert result[0] is False and result[3], tamper
    # (the tampered firm stays broken; it is a throwaway firm, and other firms have their own chains)
    other = seed(admin, results=1)
    assert verify(admin, other["firm"])[0] is True


def test_removing_the_tail_of_the_ledger_breaks_a_signoff_anchor(admin):
    f = frozen(admin, results=2)
    review_all(f)
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))
    assert verify(admin, f["firm"])[0] is True
    gate2_anchor = admin.execute("select anchor_seq from signoff where revision_id = %s and gate = 'gate2'", (f["revision"],)).fetchone()[0]
    admin.execute("alter table ledger_event disable trigger ledger_event_no_update")
    try:
        admin.execute("delete from ledger_event where firm_id = %s and seq >= %s", (f["firm"], gate2_anchor))
        result = verify(admin, f["firm"])
    finally:
        admin.execute("alter table ledger_event enable trigger ledger_event_no_update")
    assert result[0] is False and "anchor" in result[3]


def test_the_ledger_cannot_be_truncated_or_rewritten_by_a_client(admin):
    f = frozen(admin, results=1)
    for sql in ("update ledger_event set kind = 'x'", "delete from ledger_event", "truncate ledger_event"):
        with pytest.raises(psycopg.errors.Error):
            call(f["designer"], sql)
    with pytest.raises(psycopg.errors.RaiseException):
        admin.execute("truncate ledger_event")


def test_concurrent_writers_keep_one_unbroken_chain_per_firm(admin):
    import threading
    f = seed(admin, results=1)
    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            with psycopg.connect(DB_URL, autocommit=True) as c:
                for i in range(15):
                    c.execute("insert into ledger_event (firm_id, revision_id, kind, payload) values (%s, %s, 'load', %s::jsonb)",
                              (f["firm"], f["revision"], json.dumps({"w": n, "i": i})))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    ok = verify(admin, f["firm"])
    assert ok[0] is True and ok[1] == 90


# ---- share links ---------------------------------------------------------------------------------------------------

def sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def signed_all(admin):
    f = frozen(admin, results=2)
    review_all(f)
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))
    call(f["approver"], "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],))
    return f


def test_only_a_gate3_signed_revision_can_be_shared_by_a_designer_or_approver(admin):
    f = frozen(admin, results=2)
    refused(f["designer"], "select create_share_link(%s, %s, 7)", (f["revision"], sha("t1-" + f["revision"])), "signed at Gate 3")
    review_all(f)
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))
    call(f["approver"], "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],))
    refused(f["checker"], "select create_share_link(%s, %s, 7)", (f["revision"], sha("t1-" + f["revision"])), "only a designer or approver")
    for days in (0, 31, None):
        refused(f["approver"], "select create_share_link(%s, %s, %s)", (f["revision"], sha("t1-" + f["revision"]), days), "1 to 30 days")
    refused(f["approver"], "select create_share_link(%s, 'not-a-hash', 7)", (f["revision"],), "bad token hash")
    until = call(f["approver"], "select create_share_link(%s, %s, 7, 'the certifier')", (f["revision"], sha("t1-" + f["revision"])))[0][0]
    assert until is not None
    row = admin.execute("select token, label, created_by from ledger_link where revision_id = %s", (f["revision"],)).fetchone()
    assert row[0] == sha("t1-" + f["revision"]) and row[1] == "the certifier" and str(row[2]) == f["approver"]       # only the hash is stored
    refused(f["checker"], "select token from ledger_link", (), "permission denied")                    # clients never read tokens


def test_opening_a_link_is_logged_counted_and_stops_when_expired_or_revoked(admin):
    f = signed_all(admin)
    call(f["designer"], "select create_share_link(%s, %s, 7)", (f["revision"], sha("good-" + f["revision"])))
    call(f["designer"], "select create_share_link(%s, %s, 7)", (f["revision"], sha("old-" + f["revision"])))
    admin.execute("update ledger_link set created_at = now() - interval '9 days', expires_at = now() - interval '2 days' where token = %s",
                  (sha("old-" + f["revision"]),))
    opened = admin.execute("select * from open_share_link(%s, 'curl/8')", (sha("good-" + f["revision"]),)).fetchall()
    assert len(opened) == 1 and str(opened[0][1]) == f["revision"]
    admin.execute("select * from open_share_link(%s)", (sha("good-" + f["revision"]),))
    assert admin.execute("select views from ledger_link where token = %s", (sha("good-" + f["revision"]),)).fetchone()[0] == 2
    assert admin.execute("select * from open_share_link(%s)", (sha("old-" + f["revision"]),)).fetchall() == []               # expired
    assert admin.execute("select * from open_share_link(%s)", (sha("nope"),)).fetchall() == []              # unknown
    assert admin.execute("select count(*) from ledger_event where firm_id = %s and kind = 'share_link_viewed'",
                         (f["firm"],)).fetchone()[0] == 2
    call(f["designer"], "select revoke_share_link(%s, %s)", (f["revision"], sha("good-" + f["revision"])))
    assert admin.execute("select * from open_share_link(%s)", (sha("good-" + f["revision"]),)).fetchall() == []
    refused(f["checker"], "select revoke_share_link(%s, %s)", (f["revision"], sha("good-" + f["revision"])), "only a designer or approver")
    kinds = [r[0] for r in admin.execute("select kind from ledger_event where firm_id = %s", (f["firm"],))]
    assert "share_link_created" in kinds and "share_link_revoked" in kinds
    assert verify(admin, f["firm"])[0] is True
    refused(f["designer"], "select * from open_share_link(%s)", (sha("good-" + f["revision"]),), "permission denied")        # not a client door


# ---- Phase 3 review fixes (migration 0011) -------------------------------------------------------------------------

def test_a_frozen_revisions_results_cannot_be_replaced_even_by_the_service(admin):
    f = frozen(admin, results=2)
    for sql in ("update rule_result set current = false where id = %s", "delete from rule_result where id = %s"):
        with pytest.raises(psycopg.errors.RaiseException, match="frozen"):
            admin.execute(sql, (f["results"][0],))
    with pytest.raises(psycopg.errors.RaiseException, match="frozen"):
        admin.execute("insert into rule_result (firm_id, revision_id, rule_id, edition, result, inputs, citation, subject_id)"
                      " values (%s, %s, 'NCC2025-RX', 'NCC2025', 'PASS', '{}', '{}', 'ahu-9')", (f["firm"], f["revision"]))
    admin.execute("update rule_result set review_class = 'near_miss' where id = %s", (f["results"][0],))     # classification stays possible


def test_freezing_needs_a_run_that_read_exactly_what_is_there_now(admin):
    f = seed(admin, results=2)
    admin.execute("insert into space (firm_id, revision_id, name, area_m2_value, area_m2_provenance)"                # an input changed
                  " values (%s, %s, 'Added after the run', 12, 'default')", (f["firm"], f["revision"]))
    refused(f["designer"], "select freeze_revision(%s)", (f["revision"],), "inputs changed since the rules were last run")
    admin.execute("update rule_result set inputs_hash = live_inputs_hash(revision_id) where revision_id = %s", (f["revision"],))
    call(f["designer"], "select freeze_revision(%s)", (f["revision"],))


def test_a_client_cannot_make_a_child_revision(admin):
    f = frozen(admin, results=1)
    for role in ("designer", "checker"):
        refused(f[role], "insert into revision (firm_id, project_id, architect_rev, parent_revision_id) values (%s, %s, 'B', %s)",
                (f["firm"], f["project"], f["revision"]), "upload service")


# ---- Phase 3 review round 2 (migration 0012) -----------------------------------------------------------------------

def test_the_input_fingerprint_tells_apart_values_that_a_naive_concatenation_would_confuse(admin):
    f = seed(admin, results=1)
    admin.execute("insert into space (id, firm_id, revision_id, name, area_m2_value, area_m2_provenance)"
                  " values (%s, %s, %s, 'R', 50, 'default')", (f["results"][0], f["firm"], f["revision"]))
    before = admin.execute("select live_inputs_hash(%s)", (f["revision"],)).fetchone()[0]
    admin.execute("update space set area_m2_value = null, area_m2_provenance = null, ceiling_void_mm_value = 50,"
                  " ceiling_void_mm_provenance = 'default' where id = %s", (f["results"][0],))
    assert admin.execute("select live_inputs_hash(%s)", (f["revision"],)).fetchone()[0] != before
    other = seed(admin, results=1)
    assert call(other["designer"], "select live_inputs_hash(%s)", (f["revision"],))[0][0] is None          # another firm's: nothing


def test_the_diff_can_only_be_confirmed_through_the_service_and_created_from_is_not_writable(admin):
    f = frozen(admin, results=1)
    refused(f["designer"], "select confirm_revision_diff(%s, %s)", (f["revision"], "a" * 64), "permission denied")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="only a designer"):
        admin.execute("select confirm_revision_diff_as(%s, %s, %s)", (f["checker"], f["revision"], "a" * 64))
    o = seed(admin, results=1)                                                                         # an open revision
    refused(o["designer"], "update revision set created_from_sha256 = 'x' where id = %s", (o["revision"],), "created_from_sha256")
