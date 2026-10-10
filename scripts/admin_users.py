"""Service-side administration that no client can do: approver registration numbers, extra roles for small firms, the firm's signer mode.

Every change lands in the hash-chained ledger (the database triggers write `app_user_changed` / `firm_signer_mode_changed`; this script
adds the verification evidence as its own entry). Run with the SERVICE connection (MEP_DB_URL), from a machine you trust.

    uv run python scripts/admin_users.py register-approver --email jo@firm.example --number "RPEQ 12345" --register RPEQ \\
        --verified-by "A. Admin" --evidence "RPEQ register search 2026-10-12, name and number match, status current" \
        --reason "no platform administrator available for this pilot firm"   # BREAK-GLASS: normally done in the app
    uv run python scripts/admin_users.py grant-roles --email jo@firm.example --roles designer,checker      # small_firm only
    uv run python scripts/admin_users.py set-signer-mode --firm "Firm Pty Ltd" --mode small_firm
    uv run python scripts/admin_users.py show --firm "Firm Pty Ltd"

Onboarding a real firm (all ledgered; the person then signs in by magic link):
    uv run python scripts/admin_users.py add-firm --name "Firm Pty Ltd"
    uv run python scripts/admin_users.py add-user --firm "Firm Pty Ltd" --email jo@firm.example --role approver
    uv run python scripts/admin_users.py set-role --email jo@firm.example --role checker
add-user creates the sign-in account through the Supabase admin API: it also needs MEP_SUPABASE_URL and MEP_SUPABASE_SERVICE_KEY.
"""
import argparse
import json
import os
import re
import sys
from typing import Any

import psycopg

REGISTERS = ("NER", "RPEQ", "OTHER")
NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ./-]{2,39}$")


def firm_id(conn: psycopg.Connection[Any], name: str) -> Any:
    rows = conn.execute("select id from firm where name = %s", (name,)).fetchall()
    if not rows:
        sys.exit(f"no firm named {name!r}")
    if len(rows) > 1:
        sys.exit(f"{len(rows)} firms are named {name!r}: names are not unique, refusing to guess")
    return rows[0][0]


def connect() -> psycopg.Connection[Any]:
    dsn = os.environ.get("MEP_DB_URL", "")
    if not dsn:
        sys.exit("MEP_DB_URL is not set (see deploy/env.api.example)")
    return psycopg.connect(dsn, autocommit=False)


def user_row(conn: psycopg.Connection[Any], email: str) -> tuple[Any, ...]:
    row = conn.execute("select u.id, u.firm_id, u.role::text, u.registration_no, f.signer_mode from app_user u join auth.users a on a.id = u.id"
                       " join firm f on f.id = u.firm_id where lower(a.email) = lower(%s)", (email,)).fetchone()
    if row is None:
        sys.exit(f"no user with e-mail {email} (they must have signed in or been seeded first)")
    return row


def register_approver(a: argparse.Namespace) -> None:
    if not NUMBER.match(a.number):
        sys.exit("the registration number looks wrong (3 to 40 letters, digits, space . / -)")
    if len(a.evidence.strip()) < 15:
        sys.exit("--evidence must say what you checked on the register, where and when (at least 15 characters)")
    if len(a.reason.strip()) < 15:
        sys.exit("--reason must say why the normal route (firm submits, platform administrator verifies, in the app) cannot be used (at least 15 characters)")
    with connect() as conn:
        uid, firm, role, old, _mode = user_row(conn, a.email)
        if role != "approver":
            sys.exit(f"{a.email} is a {role}; only an approver carries a registration number (set the role first)")
        conn.execute("select set_config('mep.reg_path', 'break_glass', true)")
        conn.execute("update app_user set registration_no = %s where id = %s", (a.number.strip(), uid))
        conn.execute("insert into ledger_event (firm_id, kind, payload) values (%s, 'approver_registration_verified', %s::jsonb)",
                     (firm, json.dumps({"user_id": str(uid), "registration_no": a.number.strip(), "previous": old, "path": "break_glass",
                                        "reason": a.reason.strip(), "register": a.register, "verified_by": a.verified_by, "evidence": a.evidence})))
    print(f"BREAK-GLASS: registered {a.email} as {a.number.strip()} ({a.register}); ledgered with your evidence and reason."
          " The normal route is the app: the firm submits, a platform administrator verifies.")


def grant_roles(a: argparse.Namespace) -> None:
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    if not roles or any(r not in ("designer", "checker", "approver") for r in roles):
        sys.exit("--roles is a comma list of designer, checker, approver")
    with connect() as conn:
        uid, _firm, role, _reg, mode = user_row(conn, a.email)
        if mode != "small_firm":
            sys.exit("extra roles only work in a small_firm firm (set-signer-mode first); in a strict firm they would be ignored")
        extra = sorted({r for r in roles if r != role})
        conn.execute("update app_user set also_roles = %s::user_role[] where id = %s", (extra, uid))
    print(f"{a.email} ({role}) may now also act as: {', '.join(extra) or 'nobody else'}")


def set_mode(a: argparse.Namespace) -> None:
    with connect() as conn:
        fid = firm_id(conn, a.firm)
        row = conn.execute("select id, signer_mode from firm where id = %s", (fid,)).fetchone()
        assert row is not None
        conn.execute("update firm set signer_mode = %s where id = %s", (a.mode, row[0]))
        if a.mode == "strict":
            conn.execute("update app_user set also_roles = '{}' where firm_id = %s", (row[0],))
            for (uid,) in conn.execute("select distinct created_by from review_sample where firm_id = %s and used_at is null", (row[0],)).fetchall():
                void_samples(conn, uid, row[0], "the firm went back to strict mode")
    print(f"{a.firm}: signer mode {row[1]} -> {a.mode}" + ("" if a.mode == "strict" else
          "\nEvery package, PDF and ledger entry of this firm now says NOT INDEPENDENTLY CHECKED."))


def show(a: argparse.Namespace) -> None:
    with connect() as conn:
        fid = firm_id(conn, a.firm)
        rows = conn.execute("select a.email, u.role::text, u.also_roles::text[], u.registration_no, f.signer_mode from app_user u"
                            " join auth.users a on a.id = u.id join firm f on f.id = u.firm_id where f.id = %s order by u.role", (fid,)).fetchall()
    for r in rows:
        print(f"{r[0]:40s} {r[1]:9s} also={','.join(r[2]) or '-':16s} registration={r[3] or '-':14s} mode={r[4]}")


ROLES = ("designer", "checker", "approver")


def void_samples(conn: psycopg.Connection[Any], user_id: Any, firm: Any, why: str) -> None:
    """A checker whose role or firm mode changes can no longer decide their open spot-check sample: close it (ledgered) so the others can."""
    n = conn.execute("update review_sample set used_at = now() where created_by = %s and used_at is null", (user_id,)).rowcount
    if n:
        conn.execute("insert into ledger_event (firm_id, kind, payload) values (%s, 'spot_check_sample_voided', %s::jsonb)",
                     (firm, json.dumps({"user_id": str(user_id), "samples": n, "why": why})))


def add_firm(a: argparse.Namespace) -> None:
    name = a.name.strip()
    if name.lower().startswith("demo mechanical (synthetic)"):
        sys.exit("that name is reserved for the demo firm (DEMO- registration numbers are valid only there)")
    if len(name) < 3 or len(name) > 120:
        sys.exit("--name must be 3 to 120 characters")
    with connect() as conn:
        if conn.execute("select 1 from firm where name = %s", (name,)).fetchone():
            sys.exit(f"a firm named {name!r} already exists")
        fid = conn.execute("insert into firm (name) values (%s) returning id", (name,)).fetchone()[0]
        conn.execute("insert into ledger_event (firm_id, kind, payload) values (%s, 'firm_created', %s::jsonb)",
                     (fid, json.dumps({"name": name, "signer_mode": "strict"})))
    print(f"created firm {name!r} ({fid}); signer mode strict")


def add_user(a: argparse.Namespace) -> None:
    import httpx
    url, key = os.environ.get("MEP_SUPABASE_URL", ""), os.environ.get("MEP_SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        sys.exit("MEP_SUPABASE_URL and MEP_SUPABASE_SERVICE_KEY are needed to create the sign-in account")
    with connect() as conn:
        fid = firm_id(conn, a.firm)
        row = conn.execute("select id from auth.users where lower(email) = lower(%s)", (a.email,)).fetchone()
        if row is None:
            r = httpx.post(f"{url.rstrip('/')}/auth/v1/admin/users", timeout=30, headers={"apikey": key, "Authorization": f"Bearer {key}"},
                           json={"email": a.email, "email_confirm": True})
            r.raise_for_status()
            uid = r.json()["id"]
        else:
            uid = row[0]
        if conn.execute("select 1 from app_user where id = %s", (uid,)).fetchone():
            sys.exit(f"{a.email} already belongs to a firm; use set-role to change the role")
        conn.execute("insert into app_user (id, firm_id, role) values (%s, %s, %s)", (uid, fid, a.role))
    print(f"added {a.email} to {a.firm!r} as {a.role}" + ("; now register their number (register-approver)" if a.role == "approver" else ""))


def set_role(a: argparse.Namespace) -> None:
    with connect() as conn:
        uid, _firm, old, reg, _mode = user_row(conn, a.email)
        if old == a.role:
            sys.exit(f"{a.email} is already a {old}")
        if a.role != "approver":
            reg = None                                  # a registration number belongs to the approver role only
        conn.execute("update app_user set role = %s, registration_no = %s, also_roles = '{}' where id = %s", (a.role, reg, uid))
        void_samples(conn, uid, _firm, f"role changed from {old} to {a.role}")
    print(f"{a.email}: {old} -> {a.role} (ledgered); open sign-ins keep working until the next request, which uses the new role")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("register-approver")
    p.add_argument("--email", required=True)
    p.add_argument("--number", required=True)
    p.add_argument("--register", choices=REGISTERS, required=True)
    p.add_argument("--verified-by", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--reason", required=True, help="BREAK-GLASS: why the app route (firm submits, platform admin verifies) cannot be used")
    p.set_defaults(fn=register_approver)
    p = sub.add_parser("grant-roles")
    p.add_argument("--email", required=True)
    p.add_argument("--roles", required=True)
    p.set_defaults(fn=grant_roles)
    p = sub.add_parser("set-signer-mode")
    p.add_argument("--firm", required=True)
    p.add_argument("--mode", choices=["strict", "small_firm"], required=True)
    p.set_defaults(fn=set_mode)
    p = sub.add_parser("add-firm")
    p.add_argument("--name", required=True)
    p.set_defaults(fn=add_firm)
    p = sub.add_parser("add-user")
    p.add_argument("--firm", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--role", choices=ROLES, required=True)
    p.set_defaults(fn=add_user)
    p = sub.add_parser("set-role")
    p.add_argument("--email", required=True)
    p.add_argument("--role", choices=ROLES, required=True)
    p.set_defaults(fn=set_role)
    p = sub.add_parser("show")
    p.add_argument("--firm", required=True)
    p.set_defaults(fn=show)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
