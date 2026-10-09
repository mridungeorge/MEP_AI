import re
from pathlib import Path

SQL = "\n".join(p.read_text() for p in sorted(Path("supabase/migrations").glob("*.sql")))
TABLES = re.findall(r"create table (\w+) \(", SQL)
# every table named in a `foreach t in array array[...]` loop that enables row level security (0001 and later migrations)
RLS_TABLES = {
    name
    for block in re.findall(r"foreach t in array array\[([^\]]*)\]\s+loop\s+execute format\('alter table %I enable row level",
                            SQL, re.DOTALL)
    for name in re.findall(r"'(\w+)'", block)
}

SECTION4 = {"firm", "app_user", "project", "revision", "space", "system", "equipment",
            "rule_result", "artifact", "review", "signoff", "ledger_event", "ledger_link"}


def table_body(name: str) -> str:
    return re.search(rf"create table {name} \((.*?)\n\);", SQL, re.DOTALL).group(1)


def test_all_section4_tables_exist():
    assert SECTION4 <= set(TABLES)


def test_every_table_has_rls_and_firm_id():
    assert set(TABLES) == RLS_TABLES
    for t in TABLES:
        assert "firm_id" in table_body(t), f"{t} lacks firm_id"


def test_numeric_inputs_carry_unit_and_provenance():
    for t in ("space", "system"):
        cols = re.findall(r"(\w+)_value numeric", table_body(t))
        assert cols
        for c in cols:
            assert f"{c}_unit" in table_body(t) and f"{c}_provenance provenance" in table_body(t)


def test_provenance_values():
    m = re.search(r"create type provenance as enum \((.*?)\)", SQL)
    assert set(re.findall(r"'(\w+)'", m.group(1))) == {
        "engineer_confirmed", "address_lookup_confirmed", "extracted", "calculated", "default"}


def test_provenance_includes_address_lookup():
    assert "'address_lookup_confirmed'" in SQL


def test_firm_comes_from_app_user_not_jwt_claim():
    assert "auth.jwt()" not in SQL
    assert "from public.app_user where id = auth.uid()" in SQL


def test_server_written_tables_have_no_client_write_policies():
    for t in ("rule_result", "artifact", "ledger_event", "ledger_link", "app_user", "firm"):
        assert not re.search(rf"on {t} for (insert|update|delete|all)", SQL), t
    writable = re.search(r"foreach t in array array\[('project'.*?)\]", SQL, re.DOTALL).group(1)
    assert "rule_result" not in writable and "artifact" not in writable


def test_signoff_and_review_bind_user_and_gate_role():
    for t in ("review", "signoff"):
        m = re.search(rf"create policy {t}_insert on {t} for insert with check \((.*?)\);", SQL, re.DOTALL)
        assert "user_id = auth.uid()" in m.group(1) and "gate_role(gate)" in m.group(1)


def test_released_artifact_is_immutable():
    assert "create trigger artifact_frozen before update on artifact" in SQL


def test_ledger_is_append_only():
    assert "before update or delete on ledger_event" in SQL
    assert "before truncate on ledger_event" in SQL


def test_artifact_cannot_release_without_passing_validator():
    assert "not released or coalesce((validator -> 'passed') = 'true'::jsonb, false)" in table_body("artifact")


def test_hardening_migration_present():
    assert "create trigger revision_immutable_ids" in SQL
    assert "reject_if_revision_frozen" in SQL
    assert "revoke truncate, trigger, references on all tables in schema public from authenticated" in SQL
    assert "alter column rule_result_id set not null" in SQL
