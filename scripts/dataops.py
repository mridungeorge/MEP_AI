"""Data safety operations: backup, restore check, per-firm export, firm retirement.

    dataops.py backup        --dsn $MEP_DB_URL --out backups/2026-10-12            (pg_dump of the public schema + auth.users, with a manifest)
    dataops.py restore-check --dir backups/2026-10-12 --target-dsn <scratch>       (restore into a SCRATCH database and prove it matches)
    dataops.py export-firm   --dsn $MEP_DB_URL --firm "Firm Pty Ltd" --out export/ (every row of the firm as JSON + every file)
    dataops.py retire-firm   --dsn $MEP_DB_URL --firm "Firm Pty Ltd" --export export/ --confirm "Firm Pty Ltd"
    dataops.py purge-firm    --dsn $MEP_DB_URL --firm "Firm Pty Ltd" --confirm "Firm Pty Ltd" [--retention-years 7]

PostgreSQL client tools (pg_dump, pg_restore) must be on the PATH, or give --docker <container> to run them inside a Postgres container (how the tests
and the local Supabase are used). Read docs/runbooks/backup-restore.md before running any of this against real data.
"""
import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

RETENTION_YEARS_MIN = 7
SECRET_COLUMNS = {"token"}                      # never exported (only hashes are stored, but there is no reason to copy them)
BYTEA_COLUMNS = {"artifact_blob": "content", "firm_template": "content", "skill_job_file": "content", "vision_job": "pdf"}
NO_EXPORT = {"skill_job", "skill_job_file", "vision_job"}   # transient queues


class Pg:
    """Runs PostgreSQL client tools locally or inside a container."""

    def __init__(self, dsn: str | None = None, container: str | None = None, options: str | None = None, user: str = "postgres") -> None:
        self.dsn, self.container, self.options, self.user = dsn, container, options, user

    def run(self, tool: str, args: list[str], stdin: bytes | None = None) -> bytes:
        if self.container:
            cmd = ["docker", "exec", "-i", *(["-e", f"PGOPTIONS={self.options}"] if self.options else []), self.container, tool, "-U", self.user, "-d", "postgres", *args]
            env = None
        else:
            cmd = [tool, *args, "--dbname", str(self.dsn)]
            env = {**os.environ, **({"PGOPTIONS": self.options} if self.options else {})}
        r = subprocess.run(cmd, input=stdin, capture_output=True, env=env, check=False)
        if r.returncode != 0:
            raise RuntimeError(f"{tool} failed: {r.stderr.decode('utf-8', 'replace').strip()[-500:]}")
        return r.stdout


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def first(row: Any) -> Any:
    """The first column of a row, whichever row factory the connection uses."""
    return next(iter(row.values())) if isinstance(row, dict) else row[0]


def tables(conn: psycopg.Connection[Any], with_firm: bool = False) -> list[str]:
    q = ("select table_name from information_schema.tables t where table_schema = 'public' and table_type = 'BASE TABLE'"
         + (" and exists (select 1 from information_schema.columns c where c.table_schema = 'public' and c.table_name = t.table_name and c.column_name = 'firm_id')"
            if with_firm else "") + " order by 1")
    return [first(r) for r in conn.execute(q).fetchall()]


def snapshot(dsn: str) -> dict[str, Any]:
    """Counts per table, the ledger heads and a checksum of every stored file: what a restore must reproduce."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        counts = {t: conn.execute(f'select count(*) from public."{t}"').fetchone()[0] for t in tables(conn)}
        heads = {str(f): [s, h] for f, s, h in conn.execute("select firm_id, seq, row_hash from ledger_head").fetchall()}
        blobs = {str(a): d for a, d in conn.execute("select artifact_id, encode(sha256(content), 'hex') from artifact_blob").fetchall()}
        firms = [str(r[0]) for r in conn.execute("select id from firm").fetchall()]
        ledger_ok = {f: bool((conn.execute("select ok from verify_ledger(%s)", (f,)).fetchone() or [False])[0]) for f in firms}
    return {"counts": counts, "ledger_heads": heads, "artifact_sha256": blobs, "firms": firms, "ledger_ok": ledger_ok}


def auth_users_json(dsn: str) -> bytes:
    """The sign-in identities the app needs (id, address, confirmation). Passwords, sessions and other Auth data are covered by the provider's own backups."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        rows = conn.execute("select id::text, email, email_confirmed_at::text, created_at::text from auth.users order by id").fetchall()
    return json.dumps(rows).encode()


def restore_auth_users(dsn: str, data: bytes) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        cols = {r[0] for r in conn.execute("select column_name from information_schema.columns where table_schema = 'auth' and table_name = 'users'").fetchall()}
        confirm = "email_confirmed_at" if "email_confirmed_at" in cols else "confirmed_at"
        for uid_, email, confirmed, created in json.loads(data):
            conn.execute(f"insert into auth.users (id, email, {confirm}, created_at) values (%s, %s, %s, coalesce(%s::timestamptz, now())) on conflict (id) do nothing",
                         (uid_, email, confirmed, created))


def cmd_backup(a: argparse.Namespace) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pg = Pg(a.dsn, a.docker)
    files = {"public.dump": pg.run("pg_dump", ["--format=custom", "--schema=public", "--no-owner", "--no-privileges"]),
             "auth_users.json": auth_users_json(a.dsn)}
    for name, data in files.items():
        (out / name).write_bytes(data)
    manifest = {"format": 1, "files": {n: {"bytes": len(d), "sha256": sha256(d)} for n, d in files.items()}, **snapshot(a.dsn)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"backup written to {out}: {sum(manifest['counts'].values())} rows in {len(manifest['counts'])} tables, {len(manifest['firms'])} firm(s)")
    return 0


def cmd_restore_check(a: argparse.Namespace) -> int:
    """Restore into a database that already has the migrations applied and holds NO data, then compare with the manifest."""
    d = Path(a.dir)
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    for name, meta in manifest["files"].items():
        if sha256((d / name).read_bytes()) != meta["sha256"]:
            print(f"FAIL: {name} does not match its checksum: the backup is damaged")
            return 1
    with psycopg.connect(a.target_dsn, autocommit=True) as conn:
        left = sum(conn.execute(f'select count(*) from public."{t}"').fetchone()[0] for t in tables(conn))
        if left and not a.allow_nonempty:
            print("FAIL: the target already holds data; restore checks run against an empty scratch database only")
            return 1
    pg = Pg(a.target_dsn, a.docker, options="-c session_replication_role=replica", user=getattr(a, "docker_user", "postgres"))     # keep the ledger rows exactly as dumped: no triggers
    restore_auth_users(a.target_dsn, (d / "auth_users.json").read_bytes())
    pg.run("pg_restore", ["--data-only", "--no-owner", "--no-privileges", "--exit-on-error"], stdin=(d / "public.dump").read_bytes())
    got = snapshot(a.target_dsn)
    problems: list[str] = []
    for t, n in manifest["counts"].items():
        if got["counts"].get(t) != n:
            problems.append(f"table {t}: {got['counts'].get(t)} rows restored, {n} expected")
    if got["ledger_heads"] != manifest["ledger_heads"]:
        problems.append("the ledger heads differ from the backup")
    if got["artifact_sha256"] != manifest["artifact_sha256"]:
        problems.append("stored files differ from the backup")
    if got["ledger_ok"] != manifest["ledger_ok"]:
        problems.append("the audit ledger verdicts after the restore differ from the source's: " + ", ".join(
            f for f in manifest["firms"] if got["ledger_ok"].get(f) != manifest["ledger_ok"].get(f)))
    broken = [f for f, ok in manifest["ledger_ok"].items() if not ok]
    for p in problems:
        print("FAIL:", p)
    if broken:
        print(f"NOTE: {len(broken)} firm(s) had a ledger that did not verify in the SOURCE already; the restore reproduces that, it did not cause it")
    if not problems:
        print(f"restore verified: {sum(got['counts'].values())} rows, {sum(got['ledger_ok'].values())}/{len(manifest['firms'])} ledger chains verify (same as the source), "
              f"{len(got['artifact_sha256'])} stored file(s) match")
    return 1 if problems else 0


# ---------------------------------------------------------------------------------------------------------------------------- per-firm export
def find_firm(conn: psycopg.Connection[Any], name: str) -> str:
    rows = conn.execute("select id from firm where name = %s", (name,)).fetchall()
    if len(rows) != 1:
        sys.exit(f"{len(rows)} firms are named {name!r}; refusing to guess (use the exact, unique name)")
    return str(first(rows[0]))


def export_firm(dsn: str, firm: str, out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"format": 1, "firm_id": None, "tables": {}, "files": {}}
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        fid = find_firm(conn, firm) if not firm.startswith("id:") else firm[3:]
        manifest["firm_id"] = fid
        (out / "data").mkdir(exist_ok=True)
        for t in tables(conn, with_firm=True):
            if t in NO_EXPORT:
                continue
            drop = {c for c in (BYTEA_COLUMNS.get(t), *SECRET_COLUMNS) if c}
            expr = "to_jsonb(t)" + "".join(f" - '{c}'" for c in sorted(drop))
            id_col = "id" if t not in ("artifact_blob",) else "artifact_id"
            rows = conn.execute(f'select {expr} as j from public."{t}" t where firm_id = %s order by 1::text', (fid,)).fetchall()
            data = "".join(json.dumps(r["j"], sort_keys=True, default=str) + "\n" for r in rows).encode()
            (out / "data" / f"{t}.jsonl").write_bytes(data)
            manifest["tables"][t] = {"rows": len(rows), "sha256": sha256(data)}
            col = BYTEA_COLUMNS.get(t)
            if col:
                (out / "files" / t).mkdir(parents=True, exist_ok=True)
                for r in conn.execute(f'select {id_col} as i, {col} as c from public."{t}" where firm_id = %s', (fid,)).fetchall():
                    p = out / "files" / t / str(r["i"])
                    p.write_bytes(bytes(r["c"]))
                    manifest["files"][f"{t}/{r['i']}"] = {"bytes": len(r["c"]), "sha256": sha256(bytes(r["c"]))}
        users = conn.execute("select id, email, role::text as role, is_admin, active from app_user where firm_id = %s order by id", (fid,)).fetchall()
        (out / "data" / "people.json").write_text(json.dumps(users, indent=1, default=str), encoding="utf-8")
    storage = os.environ.get("MEP_SUPABASE_URL"), os.environ.get("MEP_SUPABASE_SERVICE_KEY")
    if all(storage):
        manifest["uploads"] = fetch_uploads(dsn, fid, out, *storage)             # type: ignore[arg-type]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
    return manifest


def fetch_uploads(dsn: str, fid: str, out: Path, url: str, key: str) -> dict[str, Any]:
    """The architect files (IFC, DXF, PDF) kept in Supabase Storage for this firm."""
    import httpx
    got: dict[str, Any] = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        paths = [r[0] for r in conn.execute("select distinct metadata ->> 'storage_path' from ingest_run where firm_id = %s and metadata ? 'storage_path'", (fid,)).fetchall()]
    (out / "files" / "uploads").mkdir(parents=True, exist_ok=True)
    for p in paths:
        r = httpx.get(f"{url.rstrip('/')}/storage/v1/object/{p}", headers={"apikey": key, "Authorization": f"Bearer {key}"}, timeout=120)
        if r.status_code == 200:
            name = base64.urlsafe_b64encode(p.encode()).decode()
            (out / "files" / "uploads" / name).write_bytes(r.content)
            got[p] = {"bytes": len(r.content), "sha256": sha256(r.content), "saved_as": name}
        else:
            got[p] = {"error": f"HTTP {r.status_code}"}
    return got


def cmd_export(a: argparse.Namespace) -> int:
    m = export_firm(a.dsn, a.firm, Path(a.out))
    print(f"exported firm {m['firm_id']}: {sum(t['rows'] for t in m['tables'].values())} rows, {len(m['files'])} stored file(s) to {a.out}")
    return 0


# ---------------------------------------------------------------------------------------------------------------------------- retire / purge
def verify_export(export_dir: Path, fid: str) -> None:
    mp = export_dir / "manifest.json"
    if not mp.is_file():
        sys.exit("no export manifest in that folder: run export-firm first")
    m = json.loads(mp.read_text(encoding="utf-8"))
    if m.get("firm_id") != fid:
        sys.exit("that export belongs to a different firm")
    for t, meta in m["tables"].items():
        if sha256((export_dir / "data" / f"{t}.jsonl").read_bytes()) != meta["sha256"]:
            sys.exit(f"the export of {t} is damaged")


def cmd_retire(a: argparse.Namespace) -> int:
    """Retire a firm: sign-in ends, personal data and stored drawings are erased, the SIGNED RECORD is kept (retention policy)."""
    if a.confirm != a.firm:
        sys.exit("--confirm must repeat the firm name exactly")
    with psycopg.connect(a.dsn, autocommit=False, row_factory=dict_row) as conn:
        fid = find_firm(conn, a.firm)
        verify_export(Path(a.export), fid)
        users = [r["id"] for r in conn.execute("select id from app_user where firm_id = %s", (fid,)).fetchall()]
        conn.execute("update app_user set active = false, is_admin = false where firm_id = %s", (fid,))
        conn.execute("update app_user set email = null, notify_prefs = '{}' where firm_id = %s", (fid,))
        for t in ("notification", "invitation", "vision_job", "skill_job", "registration"):
            if t == "registration":
                conn.execute("update registration set evidence = 'erased on retirement', decision_note = null where firm_id = %s", (fid,))
            else:
                conn.execute(f"delete from {t} where firm_id = %s", (fid,))
        conn.execute("delete from firm_template where firm_id = %s", (fid,))
        conn.execute("delete from share_session where firm_id = %s", (fid,))                              # certifier links stop working at once
        conn.execute("update ledger_link set expires_at = least(expires_at, now()), revoked_at = coalesce(revoked_at, now()) where firm_id = %s", (fid,))
        conn.execute("alter table artifact_blob disable trigger artifact_blob_append_only")        # break-glass for the one erase the product allows
        conn.execute("delete from artifact_blob where firm_id = %s", (fid,))
        conn.execute("alter table artifact_blob enable trigger artifact_blob_append_only")
        # sign-in accounts: no password, banned for good, identity removed
        conn.execute("update auth.users set email = 'retired+' || id::text || '@invalid.example', phone = null, encrypted_password = null,"
                     " raw_user_meta_data = '{}'::jsonb, raw_app_meta_data = '{}'::jsonb, banned_until = 'infinity' where id = any(%s)", (users,))
        conn.execute("update firm set deleted_at = now() where id = %s", (fid,))
        conn.execute("insert into ledger_event (firm_id, kind, payload) values (%s, 'firm_retired', %s::jsonb)",
                     (fid, json.dumps({"people": len(users), "export_sha256": sha256((Path(a.export) / "manifest.json").read_bytes()),
                                       "kept": "signed record, audit ledger", "erased": "e-mail addresses, invitations, notifications, stored drawings, templates"})))
        conn.commit()
    print(f"firm retired: {len(users)} account(s) closed and anonymised, drawings and templates erased; the signed record and the ledger are kept")
    return 0


def cmd_purge(a: argparse.Namespace) -> int:
    """Erase a retired firm's remaining records, but only after the retention period."""
    if a.confirm != a.firm:
        sys.exit("--confirm must repeat the firm name exactly")
    if a.retention_years < RETENTION_YEARS_MIN:
        sys.exit(f"the retention period cannot be shorter than {RETENTION_YEARS_MIN} years")
    with psycopg.connect(a.dsn, autocommit=False, row_factory=dict_row) as conn:
        fid = find_firm(conn, a.firm)
        row = conn.execute("select deleted_at, deleted_at + make_interval(years => %s) <= now() as due from firm where id = %s", (a.retention_years, fid)).fetchone()
        if row is None or row["deleted_at"] is None:
            sys.exit("the firm has not been retired: retire it first")
        if not row["due"]:
            sys.exit(f"the retention period ({a.retention_years} years from {row['deleted_at']:%Y-%m-%d}) has not passed")
        # everything of the firm, children first; the append-only guards are switched off for this one deliberate erase
        order = [t for t in reversed(tables(conn, with_firm=True)) if t != "firm"]
        guarded: list[tuple[str, str]] = []
        for t in order:
            for trg in [r["tgname"] for r in conn.execute(
                    "select tgname from pg_trigger where tgrelid = %s::regclass and not tgisinternal and tgname ~ '(append_only|no_edit|no_update|immutable|no_truncate)'",
                    (f"public.{t}",)).fetchall()]:
                conn.execute(f'alter table public."{t}" disable trigger "{trg}"')
                guarded.append((t, trg))
        conn.execute("set session_replication_role = replica")
        for t in order:
            conn.execute(f'delete from public."{t}" where firm_id = %s', (fid,))
        conn.execute("delete from public.firm where id = %s", (fid,))
        for t, trg in guarded:                       # the guards go back on in the SAME transaction: they are never left off
            conn.execute(f'alter table public."{t}" enable trigger "{trg}"')
        conn.commit()
    print("firm purged")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("backup")
    p.add_argument("--dsn", default=os.environ.get("MEP_DB_URL"), required=not os.environ.get("MEP_DB_URL"))
    p.add_argument("--out", required=True)
    p.add_argument("--docker", help="run pg_dump inside this container")
    p.set_defaults(fn=cmd_backup)
    p = sub.add_parser("restore-check")
    p.add_argument("--dir", required=True)
    p.add_argument("--target-dsn", required=True)
    p.add_argument("--docker", help="run pg_restore inside this (scratch) container")
    p.add_argument("--docker-user", default="postgres", help="database role used inside the container (a superuser, to restore without firing triggers)")
    p.add_argument("--allow-nonempty", action="store_true")
    p.set_defaults(fn=cmd_restore_check)
    p = sub.add_parser("export-firm")
    p.add_argument("--dsn", default=os.environ.get("MEP_DB_URL"), required=not os.environ.get("MEP_DB_URL"))
    p.add_argument("--firm", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("retire-firm")
    p.add_argument("--dsn", default=os.environ.get("MEP_DB_URL"), required=not os.environ.get("MEP_DB_URL"))
    p.add_argument("--firm", required=True)
    p.add_argument("--export", required=True)
    p.add_argument("--confirm", required=True)
    p.set_defaults(fn=cmd_retire)
    p = sub.add_parser("purge-firm")
    p.add_argument("--dsn", default=os.environ.get("MEP_DB_URL"), required=not os.environ.get("MEP_DB_URL"))
    p.add_argument("--firm", required=True)
    p.add_argument("--confirm", required=True)
    p.add_argument("--retention-years", type=int, default=RETENTION_YEARS_MIN)
    p.set_defaults(fn=cmd_purge)
    args = ap.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
