#!/usr/bin/env python3
"""Disposable database for review agents. Reviewers never touch the shared local Supabase DB.

  python scripts/review_db.py up        start a fresh Postgres (same image as local Supabase), apply
                                        supabase/migrations/*.sql, create roles, print JSON with URLs
  python scripts/review_db.py down NAME remove the container
  python scripts/review_db.py run -- CMD...   up, run CMD with MEP_REVIEW_* env vars, always tear down
  python scripts/review_db.py selftest  prove the isolation properties, then tear down (used by ci.sh)

Roles handed to reviewers (the superuser URL is never printed):
  reviewer_ro      LOGIN, SELECT only, default_transaction_read_only=on: inspect schema and data
  reviewer_attack  LOGIN, member of anon/authenticated only: can `set role authenticated` and attack
                   through RLS exactly like a client; no table privileges of its own
Everything lives in a throwaway container on a random port, so even a compromise costs nothing.
"""
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("REVIEW_DB_IMAGE", "public.ecr.aws/supabase/postgres:17.11.0.002")
SHARED_PORT = 54322  # the local Supabase DB used by dev and CI tests: off limits to reviewers
ROLES_SQL = """
create role reviewer_ro login password 'ro' nosuperuser nocreatedb nocreaterole;
alter role reviewer_ro set default_transaction_read_only = on;
grant usage on schema public to reviewer_ro;
grant select on all tables in schema public to reviewer_ro;
create role reviewer_attack login password 'attack' nosuperuser nocreatedb nocreaterole;
grant anon, authenticated to reviewer_attack;
"""


# The Supabase Postgres image has no `storage` schema (the Storage service creates it at start-up). Migrations that add a bucket
# and policies need the two tables, so a minimal stand-in is created where they are missing (the image may carry part of the schema).
STORAGE_STUB = """
create schema if not exists storage;
create table if not exists storage.buckets (id text primary key, name text not null, public boolean default false,
                                            file_size_limit bigint, allowed_mime_types text[]);
create table if not exists storage.objects (id uuid primary key default gen_random_uuid(), bucket_id text references storage.buckets(id),
                                            name text, owner uuid);
alter table storage.buckets owner to postgres;
alter table storage.objects owner to postgres;
alter table storage.objects enable row level security;
grant usage on schema storage to anon, authenticated;
grant select, insert on storage.objects to authenticated;
"""


def docker(*args: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True, check=check)


def psql(name: str, sql: str, user: str = "postgres") -> None:
    try:
        docker("exec", "-i", name, "psql", "-U", user, "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-q", "-f", "-", stdin=sql)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"psql failed: {(exc.stderr or '').strip()[-600:]}") from None


def up() -> dict[str, str | int]:
    name = f"mep-review-db-{secrets.token_hex(4)}"
    docker("run", "-d", "--rm", "--name", name, "-e", f"POSTGRES_PASSWORD={secrets.token_urlsafe(24)}", "-p", "127.0.0.1::5432", IMAGE)
    try:
        port = int(re.search(r":(\d+)$", docker("port", name, "5432/tcp").stdout.splitlines()[0]).group(1))  # type: ignore[union-attr]
        if port == SHARED_PORT:
            raise RuntimeError("refusing to use the shared local database port")
        started = ""
        stable = 0
        for _ in range(180):  # the image restarts once during init: require a stable postmaster for 4 probes
            r = docker("exec", name, "psql", "-U", "postgres", "-d", "postgres", "-tAc",
                       "select pg_postmaster_start_time()", check=False)
            now = r.stdout.strip() if r.returncode == 0 else ""
            stable = stable + 1 if now and now == started else 0
            started = now
            if stable >= 4:
                break
            time.sleep(1)
        else:
            raise RuntimeError("disposable database did not become ready")
        psql(name, STORAGE_STUB, user="supabase_admin")      # the storage schema belongs to the image's admin role
        for migration in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
            psql(name, migration.read_text(encoding="utf-8"))
        psql(name, ROLES_SQL)
    except Exception:
        docker("rm", "-f", name, check=False)
        raise
    base = f"postgresql://%s@127.0.0.1:{port}/postgres"
    return {"name": name, "port": port, "ro_url": base % "reviewer_ro:ro",
            "attack_url": base % "reviewer_attack:attack"}


def down(name: str) -> None:
    docker("rm", "-f", name, check=False)


def run(cmd: list[str]) -> int:
    info = up()
    try:
        env = {**os.environ, "MEP_REVIEW_RO_URL": str(info["ro_url"]), "MEP_REVIEW_ATTACK_URL": str(info["attack_url"])}
        env.pop("MEP_TEST_DB_URL", None)
        return subprocess.run(cmd, env=env, check=False).returncode
    finally:
        down(str(info["name"]))


def selftest() -> int:
    import psycopg  # local import: only needed here

    info = up()
    name = str(info["name"])
    problems: list[str] = []
    try:
        if info["port"] == SHARED_PORT:
            problems.append("review DB is on the shared port")
        with psycopg.connect(str(info["ro_url"]), autocommit=True) as ro:
            if ro.execute("select count(*) from firm").fetchone()[0] != 0:
                problems.append("a fresh review DB must start empty")
            for sql in ("insert into firm (name) values ('x')", "create table t (a int)",
                        "truncate firm", "update app_user set role = 'approver'"):
                try:
                    ro.execute(sql)
                    problems.append(f"read-only role could run: {sql}")
                except psycopg.errors.Error:
                    pass
            try:
                ro.execute("set default_transaction_read_only = off")
                ro.execute("insert into firm (name) values ('x')")
                problems.append("read-only role escaped by turning read-only off")
            except psycopg.errors.Error:
                pass
        with psycopg.connect(str(info["attack_url"]), autocommit=False) as atk:
            atk.execute("set local role authenticated")
            try:
                atk.execute("insert into rule_result (firm_id, revision_id, rule_id, edition, result, inputs, citation)"
                            " values (gen_random_uuid(), gen_random_uuid(), 'NCC2022-X-y', 'NCC2022', 'PASS', '{}', '{}')")
                problems.append("attack role could write a rule_result as a client")
            except psycopg.errors.Error:
                pass
            atk.rollback()
            for role in ("service_role", "postgres", "supabase_admin"):  # no escalation to bypass-RLS roles
                try:
                    atk.execute(f"set local role {role}")
                    problems.append(f"attack role could become {role}")
                except psycopg.errors.Error:
                    atk.rollback()
            try:
                atk.execute("create table t_escape (a int)")
                problems.append("attack role could create a table")
            except psycopg.errors.Error:
                atk.rollback()
        try:  # no superuser credentials are exposed
            psycopg.connect(str(info["ro_url"]).replace("reviewer_ro:ro", "postgres:review")).close()
            problems.append("superuser reachable from the printed host (password auth should still be required)")
        except psycopg.errors.Error:
            pass
        except psycopg.OperationalError:
            pass
    finally:
        down(name)
    gone = name not in docker("ps", "-a", "--format", "{{.Names}}").stdout.split()
    if not gone:
        problems.append("review container was not removed")
    for p in problems:
        print(f"review_db selftest FAILED: {p}", file=sys.stderr)
    if not problems:
        print("review_db selftest passed: disposable, read-only role confined, container removed")
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "up":
        print(json.dumps(up()))
        return 0
    if cmd == "down" and len(argv) == 2:
        down(argv[1])
        return 0
    if cmd == "run" and "--" in argv:
        return run(argv[argv.index("--") + 1:])
    if cmd == "selftest":
        return selftest()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
