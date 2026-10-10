"""Create the DEMO firm: three users (designer, checker, approver) and the synthetic VIC office project, ready for the walkthrough in
docs/demo-script.md. Run it against a database YOU own (local Supabase or your cloud project): it is not for production data.

    uv run python scripts/seed_demo.py --designer you+designer@example.com --checker you+checker@example.com \\
                                       --approver you+approver@example.com [--mode strict|small_firm]

Environment (see deploy/env.api.example; nothing is read from git):
    MEP_DB_URL                  Postgres connection string of the project (the service role / pooler string)
    MEP_JWT_SECRET              YOUR OWN random secret for the API (NOT the Supabase JWT secret); used only to talk to the API code
                                in-process while seeding
    MEP_SUPABASE_URL            https://<ref>.supabase.co  (or http://127.0.0.1:54321 locally)
    MEP_SUPABASE_SERVICE_KEY    the service_role key (creates the three sign-in users; never leaves this machine)

What it creates (all synthetic, from evals/golden/syn-vic-office-2025): firm "Demo Mechanical (synthetic)", project "1 Demo Street,
Melbourne VIC (synthetic office)" NCC 2025, climate zone 6, Class 5, one revision A with its building part and the four air-handling systems
entered but NOT confirmed (the demo does Gate 1 live). The approver gets the placeholder registration number DEMO-0001, which is NOT a real
registration: a real approver is registered by docs/runbooks/register-approver.md. It is safe to run twice: existing users and the firm are reused.
"""
import argparse
import os
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import psycopg
import yaml
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))
from mep.api.auth import mint_token
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

FIRM_NAME = "Demo Mechanical (synthetic)"
ADDRESS = "1 Demo Street, Melbourne VIC (synthetic office)"
GOLDEN = ROOT / "evals" / "golden" / "syn-vic-office-2025" / "project.yaml"
DEMO_REGISTRATION = "DEMO-0001"
SMOKE_ADDRESS = "SMOKE TEST (throwaway, synthetic)"


def need(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"{name} is not set (see deploy/env.api.example)")
    return value


def ensure_user(conn: psycopg.Connection[Any], supabase_url: str, service_key: str, email: str) -> str:
    row = conn.execute("select id from auth.users where lower(email) = lower(%s)", (email,)).fetchone()
    if row:
        return str(row[0])
    r = httpx.post(f"{supabase_url.rstrip('/')}/auth/v1/admin/users", timeout=30,
                   headers={"apikey": service_key, "Authorization": f"Bearer {service_key}"},
                   json={"email": email, "email_confirm": True})
    r.raise_for_status()
    return str(r.json()["id"])


def systems_body(golden: dict[str, Any]) -> dict[str, Any]:
    systems = []
    for s in golden["subjects"]:
        inputs = [{"name": n, "value": v["value"], **({"unit": v["unit"]} if "unit" in v else {})}
                  for n, v in s["inputs"].items() if n != "system_type"]
        systems.append({"tag": s["id"], "system_type": s["inputs"]["system_type"]["value"], "inputs": inputs})
    return {"systems": systems}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--designer", required=True)
    ap.add_argument("--checker", required=True)
    ap.add_argument("--approver", required=True)
    ap.add_argument("--mode", choices=["strict", "small_firm"], default="strict")
    ap.add_argument("--platform-admin", help="e-mail of the operator's person who verifies approver registration numbers (created in a separate firm)")
    ap.add_argument("--smoke", action="store_true",
                    help=f'create the separate throwaway project "{SMOKE_ADDRESS}" that scripts/staging_smoke.py is allowed to freeze and sign (instead of the demo project)')
    ap.add_argument("--firm-name", default=FIRM_NAME, help='for a second demo set: a name starting "Demo Mechanical (synthetic)"')
    args = ap.parse_args()
    emails = {"designer": args.designer, "checker": args.checker, "approver": args.approver}
    if len({e.lower() for e in emails.values()}) < 3 and args.mode == "strict":
        sys.exit("strict mode needs three different e-mail addresses (use small_firm for one person)")
    if not args.firm_name.startswith(FIRM_NAME):
        sys.exit(f'the demo firm name must start with "{FIRM_NAME}" (DEMO- registration numbers are only valid in a demo firm)')
    distinct = len({e.lower() for e in emails.values()})
    if distinct == 2:
        sys.exit("give three different addresses, or the same address three times with --mode small_firm (not two the same)")
    one_person = distinct == 1
    if one_person and args.mode != "small_firm":
        sys.exit("one e-mail address for all three roles needs --mode small_firm")
    dsn, secret = need("MEP_DB_URL"), need("MEP_JWT_SECRET")
    supabase_url, service_key = need("MEP_SUPABASE_URL"), need("MEP_SUPABASE_SERVICE_KEY")
    golden = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    assert golden.get("synthetic") is True, "the demo project must be the synthetic golden project"

    with psycopg.connect(dsn, autocommit=True) as conn:
        firm = conn.execute("select id from firm where name = %s", (args.firm_name,)).fetchone()
        firm_id = str(firm[0]) if firm else str(uuid.uuid4())
        if not firm:
            conn.execute("insert into firm (id, name) values (%s, %s)", (firm_id, args.firm_name))
        ids = {role: ensure_user(conn, supabase_url, service_key, email) for role, email in emails.items()}
        for role, uid in ids.items():
            if one_person and role != "approver":
                continue                      # one person: ONE account, as the approver, acting as the other two
            conn.execute("insert into app_user (id, firm_id, role, registration_no) values (%s, %s, %s, %s)"
                         " on conflict (id) do nothing", (uid, firm_id, role, DEMO_REGISTRATION if role == "approver" else None))
        conn.execute("update app_user set is_admin = true where id = %s", (ids["approver"] if one_person else ids["designer"],))   # the firm administrator
        if args.platform_admin:             # the operator's own person, in a separate firm, who verifies registration numbers
            pf = conn.execute("select id from firm where name = 'Platform operator (synthetic)'").fetchone()
            pfid = str(pf[0]) if pf else str(uuid.uuid4())
            if not pf:
                conn.execute("insert into firm (id, name) values (%s, 'Platform operator (synthetic)')", (pfid,))
            puid = ensure_user(conn, supabase_url, service_key, args.platform_admin)
            if conn.execute("select 1 from app_user where id = %s and firm_id <> %s", (puid, pfid)).fetchone():
                sys.exit("the platform administrator must be a separate person: that address already belongs to a firm")
            conn.execute("insert into app_user (id, firm_id, role, is_admin) values (%s, %s, 'checker', true) on conflict (id) do nothing", (puid, pfid))
            conn.execute("insert into platform_admin (user_id, firm_id) values (%s, %s) on conflict do nothing", (puid, pfid))
            print(f"platform administrator: {args.platform_admin}")
        if args.mode == "small_firm":       # one person may hold every gate; every package then says NOT INDEPENDENTLY CHECKED
            conn.execute("update firm set signer_mode = 'small_firm' where id = %s", (firm_id,))
            conn.execute("update app_user set also_roles = '{designer,checker}' where id = %s", (ids["approver"],))
        address = SMOKE_ADDRESS if args.smoke else ADDRESS
        project = conn.execute("select id from project where firm_id = %s and address = %s", (firm_id, address)).fetchone()
        if project:
            print(f"project {address!r} already exists ({project[0]}); nothing more to create")
            report(firm_id, ids, emails, args.mode)
            return
        p = golden["project"]
        project_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
        conn.execute("insert into project (id, firm_id, address, state, climate_zone, ncc_edition, approval_date)"
                     " values (%s, %s, %s, %s, %s, %s, %s)", (project_id, firm_id, address, p["state"], p["climate_zone"],
                                                               p["ncc_edition"], date.fromisoformat(str(p["approval_date"]))))
        conn.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'A')",
                     (revision_id, firm_id, project_id))

    client = TestClient(create_pg_app(dsn, secret, load_pack(ROOT / "rules")))
    h = {"Authorization": f"Bearer {mint_token(secret, ids['designer'], ttl_seconds=600)}",
         **({"X-Acting-Role": "designer"} if one_person else {})}
    base = f"/revisions/{revision_id}"
    r = client.put(f"{base}/gate1/parts", headers=h, json={"parts": [{"building_class": p["building_class"], "storeys": 4, "area_m2": 2400}]})
    r.raise_for_status()
    r = client.post(f"{base}/schedule/systems", headers=h, json=systems_body(golden))
    if r.status_code != 200:
        sys.exit(f"could not enter the demo systems: {r.status_code} {r.text}")
    print(f"created project {project_id}, revision A {revision_id}")
    report(firm_id, ids, emails, args.mode)


def report(firm_id: str, ids: dict[str, str], emails: dict[str, str], mode: str) -> None:
    print(f"\nfirm {firm_id} (signer mode: {mode})")
    for role in ("designer", "checker", "approver"):
        print(f"  {role:9s} {emails[role]}  ({ids[role]})")
    print("Sign in with each address (a magic link is e-mailed). Next: docs/demo-script.md")


if __name__ == "__main__":
    main()
