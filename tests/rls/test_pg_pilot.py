"""Phase 4a.1 (pilot readiness) in the database: signer modes, accepted FAILs, acknowledgements, and the freeze/edit/run races with
two live sessions. Needs the local Supabase."""
import json
import threading
import time
import uuid

import psycopg
import pytest
from mep.review import package as pkg

from tests.rls.conftest import DB_URL
from tests.rls.test_pg_signoff import (  # noqa: F401
    call,
    frozen,
    refused,
    review_all,
    seed,
    unguarded,
    verify,
)


def as_conn(user: str, acting: str | None = None) -> psycopg.Connection:
    """An open (uncommitted) client transaction, optionally ACTING in another role (small_firm only)."""
    conn = psycopg.connect(DB_URL, autocommit=False)
    claims = {"sub": user, "role": "authenticated", **({"acting_role": acting} if acting else {})}
    conn.execute("set local role authenticated")
    conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps(claims),))
    return conn


def run_as(user: str, sql: str, params: tuple = (), acting: str | None = None):
    conn = as_conn(user, acting)
    try:
        cur = conn.execute(sql, params)
        out = cur.fetchall() if cur.description else None
        conn.commit()
        return out
    finally:
        conn.close()


def refused_as(user: str, sql: str, params: tuple, match: str, acting: str | None = None) -> None:
    with pytest.raises(psycopg.errors.Error) as e:
        run_as(user, sql, params, acting)
    assert match in str(e.value), str(e.value)


# ---- 1. signer independence as a firm setting ----------------------------------------------------------------------

def small_firm(admin, **kw):
    """One PERSON (the 'approver' account) who may act as designer and checker too, in a small_firm firm."""
    f = seed(admin, **kw)
    admin.execute("update firm set signer_mode = 'small_firm' where id = %s", (f["firm"],))
    admin.execute("update app_user set also_roles = '{designer,checker}' where id = %s", (f["approver"],))
    return f


def test_strict_is_the_default_and_acting_in_another_role_is_ignored(admin):
    f = seed(admin, results=1)
    assert admin.execute("select signer_mode from firm where id = %s", (f["firm"],)).fetchone()[0] == "strict"
    admin.execute("update app_user set also_roles = '{designer,checker}' where id = %s", (f["approver"],))       # given, but the firm is strict
    refused_as(f["approver"], "select freeze_revision(%s)", (f["revision"],), "only a designer", acting="designer")
    assert run_as(f["approver"], "select current_user_role()::text", acting="checker") == [("approver",)]


def test_small_firm_one_person_holds_every_gate_and_everything_says_so(admin):
    f = small_firm(admin, results=3, classes={0: "fail"})
    me = f["approver"]
    # not allowed to act in a role they were not given, or in anyone else's name
    assert run_as(me, "select current_user_role()::text", acting="designer") == [("designer",)]
    other = seed(admin, results=1)
    admin.execute("update app_user set also_roles = '{designer}' where id = %s", (other["approver"],))     # strict firm: no effect
    assert run_as(other["approver"], "select current_user_role()::text", acting="designer") == [("approver",)]
    admin.execute("update app_user set also_roles = '{designer}' where id = %s", (f["approver"],))         # checker taken away again
    assert run_as(me, "select current_user_role()::text", acting="checker") == [("approver",)]
    admin.execute("update app_user set also_roles = '{designer,checker}' where id = %s", (f["approver"],))

    run_as(me, "select freeze_revision(%s)", (f["revision"],), acting="designer")                          # Gate 1 by the person
    for rid in f["results"]:
        failing = rid == f["results"][0]
        run_as(me, "select gate2_review(%s, 'approve', %s, null, %s, %s)", (
            rid, "accepted by the sole engineer, see the file note" if failing else "checked against the clause",
            "performance_solution" if failing else None, "PS-1 in the design file" if failing else None), acting="checker")
    run_as(me, "select sign_gate(%s, 'gate2')", (f["revision"],), acting="checker")                         # Gate 2 by the same person
    run_as(me, "select gate3_acknowledge_fail(%s, 'acknowledged as the approving engineer')", (f["results"][0],))
    run_as(me, "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],))                            # Gate 3 by the same person

    gates = admin.execute("select gate::text, user_id::text, signer_role::text, signer_mode from signoff where revision_id = %s order by gate",
                          (f["revision"],)).fetchall()
    assert [g[0] for g in gates] == ["gate1", "gate2", "gate3"] and {g[1] for g in gates} == {me}
    assert [g[2] for g in gates] == ["designer", "checker", "approver"] and {g[3] for g in gates} == {"small_firm"}

    # EVERY ledger entry of the firm carries the notice (inside the hash), and the chain still verifies
    missing = admin.execute("select count(*) from ledger_event where firm_id = %s and payload ->> 'independence_notice' is distinct from"
                            " 'NOT INDEPENDENTLY CHECKED' and created_at > (select max(created_at) from ledger_event where kind ="
                            " 'revision_frozen' and firm_id = %s) - interval '1 hour'", (f["firm"], f["firm"])).fetchone()[0]
    assert missing == 0 or all_entries_after_mode_change(admin, f)
    assert verify(admin, f["firm"])[0] is True

    with admin.cursor() as cur:
        package = pkg.assemble(admin, uuid.UUID(f["firm"]), uuid.UUID(f["revision"]))
    assert package is not None and package["independence_notice"] == "NOT INDEPENDENTLY CHECKED"
    assert package["status"]["complete"]
    pdf = pkg.to_pdf(package)
    validator = pkg.validate_pdf(pdf, package)
    assert validator["passed"], validator
    from io import BytesIO

    from pypdf import PdfReader
    text = " ".join("\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf)).pages).split())
    assert text.count("NOT INDEPENDENTLY CHECKED") >= 2                                                      # on the pages, not once
    assert cur is not None


def all_entries_after_mode_change(admin, f) -> bool:
    """Entries written before the firm was switched to small_firm (the seed's own user rows) cannot carry the notice."""
    switched = admin.execute("select min(seq) from ledger_event where firm_id = %s and kind = 'firm_signer_mode_changed'", (f["firm"],)).fetchone()[0]
    bad = admin.execute("select count(*) from ledger_event where firm_id = %s and seq > %s and payload ->> 'independence_notice' is distinct from"
                        " 'NOT INDEPENDENTLY CHECKED'", (f["firm"], switched)).fetchone()[0]
    return bad == 0


def test_the_signer_mode_change_and_registration_changes_are_ledgered(admin):
    f = seed(admin, results=1)
    admin.execute("update firm set signer_mode = 'small_firm' where id = %s", (f["firm"],))
    admin.execute("update app_user set registration_no = 'RPEQ 54321' where id = %s", (f["approver"],))
    rows = admin.execute("select kind, payload from ledger_event where firm_id = %s and kind in ('firm_signer_mode_changed',"
                         " 'app_user_changed') order by seq desc limit 2", (f["firm"],)).fetchall()
    assert rows[0][0] == "app_user_changed" and rows[0][1]["registration_no"] == "RPEQ 54321"
    assert rows[0][1]["previous"]["registration_no"] == "RPEQ 12345"
    assert rows[1][0] == "firm_signer_mode_changed" and rows[1][1]["from"] == "strict" and rows[1][1]["to"] == "small_firm"
    refused(f["designer"], "update firm set signer_mode = 'small_firm' where id = %s", (f["firm"],), "permission denied")  # not a client setting
    refused(f["approver"], "update app_user set registration_no = 'X-1234' where id = %s", (f["approver"],), "permission denied")


def test_strict_mode_still_needs_three_different_people_and_the_gate1_signer_cannot_review(admin):
    f = frozen(admin, results=2)
    admin.execute("update app_user set role = 'checker' where id = %s", (f["designer"],))
    refused(f["designer"], "select gate2_review(%s, 'approve', 'my own work is fine')", (f["results"][0],), "signed Gate 1")


# ---- 2. accepted FAIL ----------------------------------------------------------------------------------------------

def test_approving_a_fail_needs_a_category_an_explanation_and_never_a_bulk_sample(admin):
    f = frozen(admin, results=12, classes={0: "fail"})
    r0 = f["results"][0]
    ck = f["checker"]
    refused(ck, "select gate2_review(%s, 'approve', 'the checker accepts this failure')", (r0,), "needs a reason category")
    refused(ck, "select gate2_review(%s, 'approve', 'short', null, 'out_of_scope')", (r0,), "at least 10 characters")
    refused(ck, "select gate2_review(%s, 'approve', 'the checker accepts this failure', null, 'whatever')", (r0,), "violates check constraint")      # closed set
    refused(ck, "select gate2_review(%s, 'approve', 'the checker accepts this failure', null, 'performance_solution')", (r0,), "review_performance_solution_reference")
    refused(ck, "select gate2_review(%s, 'approve', 'a long enough explanation', null, 'out_of_scope')",
            (f["results"][1],), "only applies to approving a FAIL")                                                   # a PASS has none
    refused(ck, "select gate2_review(%s, 'reject', 'a long enough explanation', null, 'out_of_scope')", (r0,), "only applies")
    call(ck, "select gate2_review(%s, 'reject', 'the airflow is wrong, rework it')", (r0,))                            # rejecting needs none
    # a FAIL is not a clean pass: it is never a candidate or part of a sample
    sample = call(ck, "select gate2_prepare_bulk(%s)", (f["revision"],))[0][0]
    cand = [str(x[0]) for x in admin.execute("select unnest(candidate_ids) from review_sample where id = %s", (sample,))]
    assert r0 not in cand and len(cand) == 11
    refused(ck, "select gate2_review(%s, 'approve', 'a long enough explanation', %s, 'out_of_scope')", (r0, sample), "never part of a bulk")
    call(ck, "select gate2_review(%s, 'approve', 'accepted: covered by performance solution PS-7', null, 'performance_solution', 'PS-7 v2')", (r0,))
    latest = admin.execute("select fail_category, fail_reference from review_latest where rule_result_id = %s", (r0,)).fetchone()
    assert latest == ("performance_solution", "PS-7 v2")


def test_a_disputed_rule_is_recorded_and_written_to_the_engineer_review_list(admin, tmp_path, monkeypatch):
    from mep.api import review_pg
    md = tmp_path / "disputed.md"
    monkeypatch.setenv("MEP_DISPUTED_MD", str(md))
    f = frozen(admin, results=2, classes={0: "fail"})
    call(f["checker"], "select gate2_review(%s, 'approve', 'the rule seems to misread this configuration', null, 'rule_disputed')", (f["results"][0],))
    row = admin.execute("select rule_id, reason from rule_dispute where rule_result_id = %s", (f["results"][0],)).fetchone()
    assert row == ("NCC2025-R0", "the rule seems to misread this configuration")
    assert review_pg.record_disputed(DB_URL, uuid.UUID(f["results"][0])) is True
    assert review_pg.record_disputed(DB_URL, uuid.UUID(f["results"][0])) is False                                  # once per result
    text = md.read_text(encoding="utf-8")
    assert text.startswith("# Disputed rules") and "`NCC2025-R0` disputed" in text and "misread this configuration" in text
    assert "rule_dispute_recorded" in {r[0] for r in admin.execute("select kind from ledger_event where firm_id = %s", (f["firm"],))}
    refused(f["designer"], "insert into rule_dispute (firm_id, rule_id, revision_id, rule_result_id, flagged_by, reason)"
            " values (%s, 'x', %s, %s, %s, 'x')", (f["firm"], f["revision"], f["results"][0], f["designer"]), "permission denied")


def test_gate3_needs_each_accepted_fail_acknowledged_individually_by_the_approver(admin):
    f = frozen(admin, results=3, classes={0: "fail", 1: "fail"})
    for rid in f["results"][:2]:
        call(f["checker"], "select gate2_review(%s, 'approve', 'accepted for the stated reason, see file', null, 'out_of_scope')", (rid,))
    call(f["checker"], "select gate2_review(%s, 'approve', 'checked against the clause')", (f["results"][2],))
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))
    refused(f["approver"], "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],), "2 accepted FAIL(s)")
    refused(f["checker"], "select gate3_acknowledge_fail(%s, 'acknowledged by me')", (f["results"][0],), "only an approver")
    refused(f["approver"], "select gate3_acknowledge_fail(%s, 'acknowledged by me')", (f["results"][2],), "not an accepted FAIL")
    refused(f["approver"], "select gate3_acknowledge_fail(%s, '  ')", (f["results"][0],), "fail_ack_note_check")
    call(f["approver"], "select gate3_acknowledge_fail(%s, 'acknowledged: reviewed the explanation')", (f["results"][0],))
    refused(f["approver"], "select gate3_acknowledge_fail(%s, 'acknowledged again')", (f["results"][0],), "fail_ack_rule_result_id_key")             # once each
    refused(f["approver"], "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],), "1 accepted FAIL(s)")
    call(f["approver"], "select gate3_acknowledge_fail(%s, 'acknowledged: reviewed the explanation')", (f["results"][1],))
    call(f["approver"], "select sign_gate(%s, 'gate3', 'RPEQ 12345')", (f["revision"],))
    refused(f["approver"], "select gate3_acknowledge_fail(%s, 'too late')", (f["results"][0],), "Gate 3 is already signed")
    with pytest.raises(psycopg.errors.RaiseException):
        admin.execute("update fail_ack set note = 'changed' where rule_result_id = %s", (f["results"][0],))

    with admin.cursor():
        package = pkg.assemble(admin, uuid.UUID(f["firm"]), uuid.UUID(f["revision"]))
    assert package is not None
    assert [r["rule_id"] for r in package["results"][:2]] == ["NCC2025-R0", "NCC2025-R1"]                       # accepted FAILs first
    assert len(package["accepted_fails"]) == 2 and all(a["accepted_fail"]["acknowledged"] for a in package["accepted_fails"])
    from io import BytesIO

    from pypdf import PdfReader
    pdf = pkg.to_pdf(package)
    assert pkg.validate_pdf(pdf, package)["passed"]
    text = " ".join("\n".join(p.extract_text() or "" for p in PdfReader(BytesIO(pdf)).pages).split())
    assert text.index("Accepted FAILs") < text.index("Sign-offs") < text.index("Results and review")
    assert package["independence_notice"] is None


# ---- 5. the races, with two live sessions --------------------------------------------------------------------------

def racer(fn):
    box: dict = {}

    def target():
        try:
            box["ok"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    return t, box


def seed_with_space(admin):
    f = seed(admin, results=2)
    f["space"] = str(uuid.uuid4())
    admin.execute("insert into space (id, firm_id, revision_id, name, area_m2_value, area_m2_provenance)"
                  " values (%s, %s, %s, 'Plant', 20, 'default')", (f["space"], f["firm"], f["revision"]))
    admin.execute("update rule_result set inputs_hash = live_inputs_hash(revision_id) where revision_id = %s", (f["revision"],))
    return f


def live_matches_results(admin, f) -> bool:
    h = admin.execute("select live_inputs_hash(%s)", (f["revision"],)).fetchone()[0]
    ran = {r[0] for r in admin.execute("select inputs_hash from rule_result where revision_id = %s and current", (f["revision"],))}
    return ran == {h}


def test_race_an_edit_that_arrives_during_a_freeze_is_refused_after_it(admin):
    f = seed_with_space(admin)
    t1 = as_conn(f["designer"])
    t1.execute("select freeze_revision(%s)", (f["revision"],))                          # T1 holds the revision lock, not yet committed

    def edit():
        c = as_conn(f["designer"])
        try:
            c.execute("update space set name = 'Plant (edited)' where id = %s", (f["space"],))
            c.commit()
        finally:
            c.close()

    t, box = racer(edit)
    time.sleep(1.0)
    assert t.is_alive(), "the edit must WAIT for the freeze, not slip in beside it"
    t1.commit()
    t1.close()
    t.join(10)
    assert "error" in box and ("frozen" in str(box["error"]) or isinstance(box["error"], psycopg.errors.Error)), box
    assert admin.execute("select name from space where id = %s", (f["space"],)).fetchone()[0] == "Plant"
    assert admin.execute("select frozen_at is not null from revision where id = %s", (f["revision"],)).fetchone()[0]
    assert live_matches_results(admin, f)


def test_race_a_freeze_that_arrives_during_an_edit_is_refused_after_it(admin):
    f = seed_with_space(admin)
    edit = as_conn(f["designer"])
    edit.execute("update space set name = 'Plant (edited)' where id = %s", (f["space"],))          # uncommitted edit holds a share lock

    def freeze():
        c = as_conn(f["designer"])
        try:
            c.execute("select freeze_revision(%s)", (f["revision"],))
            c.commit()
        finally:
            c.close()

    t, box = racer(freeze)
    time.sleep(1.0)
    assert t.is_alive(), "the freeze must WAIT for the edit"
    edit.commit()
    edit.close()
    t.join(10)
    assert "error" in box and "inputs changed since the rules were last run" in str(box["error"]), box
    assert not admin.execute("select frozen_at is not null from revision where id = %s", (f["revision"],)).fetchone()[0]


def new_run(conn, f, run_id: str) -> None:
    """What PgRepository.save_results does on the service connection: supersede the current run and store a new one."""
    conn.execute("select 1 from revision where id = %s for share", (f["revision"],))
    h = conn.execute("select live_inputs_hash(%s)", (f["revision"],)).fetchone()[0]
    conn.execute("update rule_result set current = false where revision_id = %s and current", (f["revision"],))
    conn.execute("insert into rule_result (firm_id, revision_id, rule_id, edition, result, inputs, citation, subject_id, current,"
                 " review_class, run_id, inputs_hash) values (%s, %s, 'NCC2025-RN', 'NCC2025', 'PASS', '{}', '{}', 'ahu-1', true,"
                 " 'clean_pass', %s, %s)", (f["firm"], f["revision"], run_id, h))


def test_race_a_run_arriving_during_a_freeze_cannot_replace_frozen_results(admin):
    f = seed_with_space(admin)
    first_run = admin.execute("select count(*) from rule_result where revision_id = %s and current", (f["revision"],)).fetchone()[0]
    t1 = as_conn(f["designer"])
    t1.execute("select freeze_revision(%s)", (f["revision"],))

    def run():
        c = psycopg.connect(DB_URL, autocommit=False)                                   # the service connection
        try:
            new_run(c, f, str(uuid.uuid4()))
            c.commit()
        finally:
            c.close()

    t, box = racer(run)
    time.sleep(1.0)
    assert t.is_alive(), "the run must WAIT for the freeze"
    t1.commit()
    t1.close()
    t.join(10)
    assert "error" in box and "frozen" in str(box["error"]), box
    assert admin.execute("select count(*) from rule_result where revision_id = %s and current", (f["revision"],)).fetchone()[0] == first_run
    assert live_matches_results(admin, f)


def test_race_a_freeze_arriving_during_a_run_waits_and_then_freezes_the_new_results(admin):
    f = seed_with_space(admin)
    run = psycopg.connect(DB_URL, autocommit=False)
    new_run(run, f, str(uuid.uuid4()))                                                   # uncommitted run holds the share lock

    def freeze():
        c = as_conn(f["designer"])
        try:
            c.execute("select freeze_revision(%s)", (f["revision"],))
            c.commit()
        finally:
            c.close()

    t, box = racer(freeze)
    time.sleep(1.0)
    assert t.is_alive(), "the freeze must WAIT for the run"
    run.commit()
    run.close()
    t.join(10)
    assert "error" not in box, box
    assert admin.execute("select frozen_at is not null from revision where id = %s", (f["revision"],)).fetchone()[0]
    assert {r[0] for r in admin.execute("select rule_id from rule_result where revision_id = %s and current", (f["revision"],))} == {"NCC2025-RN"}
    assert live_matches_results(admin, f)


# ---- review round 1 (migration 0016) -------------------------------------------------------------------------------

def second_approver(admin, f, registration="RPEQ 22222"):
    u = str(uuid.uuid4())
    admin.execute("insert into auth.users (id, email) values (%s, %s)", (u, f"approver2-{u}@test.invalid"))
    admin.execute("insert into app_user (id, firm_id, role, registration_no) values (%s, %s, 'approver', %s)", (u, f["firm"], registration))
    return u


def test_a_mode_switch_cannot_launder_reviews_done_in_small_firm_mode(admin):
    f = frozen(admin, results=2)
    admin.execute("update firm set signer_mode = 'small_firm' where id = %s", (f["firm"],))
    admin.execute("update app_user set also_roles = '{designer,checker}' where id = %s", (f["approver"],))
    for rid in f["results"]:
        run_as(f["approver"], "select gate2_review(%s, 'approve', 'reviewed by the person who also froze it')", (rid,), acting="checker")
    admin.execute("update firm set signer_mode = 'strict' where id = %s", (f["firm"],))
    assert admin.execute("select distinct signer_mode from review where revision_id = %s", (f["revision"],)).fetchall() == [("small_firm",)]
    package = pkg.assemble(admin, uuid.UUID(f["firm"]), uuid.UUID(f["revision"]))
    assert package is not None and package["independence_notice"] == "NOT INDEPENDENTLY CHECKED"
    assert package["status"]["signed_gates"] == ["gate1"]


def test_only_the_gate3_signers_own_acknowledgements_count(admin):
    f = frozen(admin, results=2, classes={0: "fail"})
    call(f["checker"], "select gate2_review(%s, 'approve', 'accepted for the stated reason, see file', null, 'out_of_scope')", (f["results"][0],))
    call(f["checker"], "select gate2_review(%s, 'approve', 'checked against the clause')", (f["results"][1],))
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))
    other = second_approver(admin, f)
    call(f["approver"], "select gate3_acknowledge_fail(%s, 'acknowledged by the first approver')", (f["results"][0],))
    refused(other, "select sign_gate(%s, 'gate3', 'RPEQ 22222')", (f["revision"],), "1 accepted FAIL(s) need")        # not his acknowledgement
    refused(f["designer"], "select gate3_acknowledge_fail(%s, 'x acknowledged')", (f["results"][0],), "only an approver")


def test_a_strict_approver_who_signed_earlier_cannot_acknowledge(admin):
    f = frozen(admin, results=2, classes={0: "fail"})
    call(f["checker"], "select gate2_review(%s, 'approve', 'accepted for the stated reason, see file', null, 'out_of_scope')", (f["results"][0],))
    call(f["checker"], "select gate2_review(%s, 'approve', 'checked against the clause')", (f["results"][1],))
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))
    admin.execute("update app_user set role = 'approver', registration_no = 'RPEQ 33333' where id = %s", (f["checker"],))      # gate 2 signer promoted
    refused(f["checker"], "select gate3_acknowledge_fail(%s, 'acknowledged as the checker')", (f["results"][0],), "someone else" if False else "must be someone else")


def test_a_demo_registration_is_refused_outside_the_demo_firm(admin):
    f = frozen(admin, results=1)
    review_all(f)
    call(f["checker"], "select sign_gate(%s, 'gate2')", (f["revision"],))
    admin.execute("update app_user set registration_no = 'DEMO-0001' where id = %s", (f["approver"],))
    refused(f["approver"], "select sign_gate(%s, 'gate3', 'DEMO-0001')", (f["revision"],), "placeholders")
    admin.execute("update firm set name = 'Demo Mechanical (synthetic) test' where id = %s", (f["firm"],))
    call(f["approver"], "select sign_gate(%s, 'gate3', 'DEMO-0001')", (f["revision"],))
