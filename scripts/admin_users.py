"""Service-side administration that no client can do: approver registration numbers, extra roles for small firms, the firm's signer mode.

Every change lands in the hash-chained ledger (the database triggers write `app_user_changed` / `firm_signer_mode_changed`; this script
adds the verification evidence as its own entry). Run with the SERVICE connection (MEP_DB_URL), from a machine you trust.

    uv run python scripts/admin_users.py register-approver --email jo@firm.example --number "RPEQ 12345" --register RPEQ \\
        --verified-by "A. Admin" --evidence "RPEQ register search 2026-10-12, name and number match, status current"
    uv run python scripts/admin_users.py grant-roles --email jo@firm.example --roles designer,checker      # small_firm only
    uv run python scripts/admin_users.py set-signer-mode --firm "Firm Pty Ltd" --mode small_firm
    uv run python scripts/admin_users.py show --firm "Firm Pty Ltd"
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
    with connect() as conn:
        uid, firm, role, old, _mode = user_row(conn, a.email)
        if role != "approver":
            sys.exit(f"{a.email} is a {role}; only an approver carries a registration number (set the role first)")
        conn.execute("update app_user set registration_no = %s where id = %s", (a.number.strip(), uid))
        conn.execute("insert into ledger_event (firm_id, kind, payload) values (%s, 'approver_registration_verified', %s::jsonb)",
                     (firm, json.dumps({"user_id": str(uid), "registration_no": a.number.strip(), "previous": old,
                                        "register": a.register, "verified_by": a.verified_by, "evidence": a.evidence})))
    print(f"registered {a.email} as {a.number.strip()} ({a.register}); ledgered with your evidence")


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
        row = conn.execute("select id, signer_mode from firm where name = %s", (a.firm,)).fetchone()
        if row is None:
            sys.exit(f"no firm named {a.firm!r}")
        conn.execute("update firm set signer_mode = %s where id = %s", (a.mode, row[0]))
        if a.mode == "strict":
            conn.execute("update app_user set also_roles = '{}' where firm_id = %s", (row[0],))
    print(f"{a.firm}: signer mode {row[1]} -> {a.mode}" + ("" if a.mode == "strict" else
          "\nEvery package, PDF and ledger entry of this firm now says NOT INDEPENDENTLY CHECKED."))


def show(a: argparse.Namespace) -> None:
    with connect() as conn:
        rows = conn.execute("select a.email, u.role::text, u.also_roles::text[], u.registration_no, f.signer_mode from app_user u"
                            " join auth.users a on a.id = u.id join firm f on f.id = u.firm_id where f.name = %s order by u.role", (a.firm,)).fetchall()
    for r in rows:
        print(f"{r[0]:40s} {r[1]:9s} also={','.join(r[2]) or '-':16s} registration={r[3] or '-':14s} mode={r[4]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("register-approver")
    p.add_argument("--email", required=True)
    p.add_argument("--number", required=True)
    p.add_argument("--register", choices=REGISTERS, required=True)
    p.add_argument("--verified-by", required=True)
    p.add_argument("--evidence", required=True)
    p.set_defaults(fn=register_approver)
    p = sub.add_parser("grant-roles")
    p.add_argument("--email", required=True)
    p.add_argument("--roles", required=True)
    p.set_defaults(fn=grant_roles)
    p = sub.add_parser("set-signer-mode")
    p.add_argument("--firm", required=True)
    p.add_argument("--mode", choices=["strict", "small_firm"], required=True)
    p.set_defaults(fn=set_mode)
    p = sub.add_parser("show")
    p.add_argument("--firm", required=True)
    p.set_defaults(fn=show)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
