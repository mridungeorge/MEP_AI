"""Seed the local Supabase for the Playwright e2e run and write the state file the tests read.

Every scenario is a fresh firm, project and revision, with a designer and a checker created in Supabase Auth (the tests sign in by
magic link). Data goes in through the real API code (an in-process TestClient on the Postgres app) and the real ingest store:

  flow                       nothing ingested: the UI test uploads the IFC itself.
  refuse-project / -building_part / -space / -system_input
                             everything entered and confirmed EXCEPT that one kind (IFC ingested by the store).
  mixed                      two building parts, two systems, everything confirmed; the UI test assigns each system to a part.

    uv run python scripts/e2e_seed.py --out apps/web/e2e/.state.json
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import jwt
import psycopg
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import e2e_lib
from mep.api.auth import mint_token
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack
from mep.ingest.ifc import read_ifc
from mep.ingest.store import store_ingest

DB_URL = os.environ.get("MEP_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
SECRET = os.environ.get("MEP_JWT_SECRET", "super-secret-jwt-token-with-at-least-32-characters-long")
SUPABASE_URL = os.environ.get("MEP_SUPABASE_URL", "http://127.0.0.1:54321")
EDITION, AC, IFC = "NCC2025", "air_conditioning", ROOT / "tests/fixtures/ifc/bsi-arch-ifc4.ifc"
KINDS = ("project", "building_part", "space", "system_input")


def supabase_key(role: str) -> str:
    return jwt.encode({"iss": "supabase-demo", "role": role, "exp": int(time.time()) + 6 * 3600}, SECRET, algorithm="HS256")


def make_auth_user(email: str) -> str:
    """A confirmed user in Supabase Auth (so a magic link can be sent to the address)."""
    uid = str(uuid.uuid4())
    r = httpx.post(f"{SUPABASE_URL}/auth/v1/admin/users", timeout=30,
                   headers={"apikey": supabase_key("anon"), "Authorization": f"Bearer {supabase_key('service_role')}"},
                   json={"id": uid, "email": email, "email_confirm": True})
    r.raise_for_status()
    return uid


def make_world(conn: psycopg.Connection[Any], label: str) -> dict[str, str]:
    run = uuid.uuid4().hex[:8]
    f = {k: str(uuid.uuid4()) for k in ("firm", "project", "revision")}
    conn.execute("insert into firm (id, name) values (%s, %s)", (f["firm"], f"e2e {label}"))
    for role in ("designer", "checker"):
        f[f"{role}_email"] = f"{role}-{label}-{run}@e2e.invalid"
        f[role] = make_auth_user(f[f"{role}_email"])
        conn.execute("insert into app_user (id, firm_id, role) values (%s, %s, %s)", (f[role], f["firm"], role))
    conn.execute("insert into project (id, firm_id, address, state, climate_zone, ncc_edition, approval_date)"
                 " values (%s, %s, %s, 'VIC', 6, %s, %s)", (f["project"], f["firm"], f"1 E2E St ({label})", EDITION, date(2026, 10, 1)))
    conn.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'A')",
                 (f["revision"], f["firm"], f["project"]))
    return f


def rows_of(view: dict[str, Any]) -> list[dict[str, Any]]:
    return ([{"kind": "project", "id": view["project"]["id"], "etag": view["project"]["etag"]}]
            + [{"kind": "building_part", "id": p["id"], "etag": p["etag"]} for p in view["parts"]]
            + [{"kind": "space", "id": s["id"], "etag": s["etag"]} for s in view["spaces"]]
            + [{"kind": "system_input", "id": i["id"], "etag": i["etag"]} for i in view["inputs"]])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    out: Path = ap.parse_args().out
    pack = load_pack(ROOT / "rules")
    client = TestClient(create_pg_app(DB_URL, SECRET, pack))
    scenarios: dict[str, dict[str, Any]] = {}
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        for name in ("flow", *[f"refuse-{k}" for k in KINDS], "mixed"):
            f = make_world(conn, name)
            h = {"Authorization": f"Bearer {mint_token(SECRET, f['designer'], ttl_seconds=6 * 3600)}"}
            base = f"/revisions/{f['revision']}"
            if name != "flow":
                store_ingest(conn, firm_id=f["firm"], revision_id=f["revision"], result=read_ifc(IFC))
                classes = ["5", "6"] if name == "mixed" else ["5"]
                parts = [{"building_class": c, "storeys": 3, "area_m2": 400 + 100 * i} for i, c in enumerate(classes)]
                assert client.put(f"{base}/gate1/parts", headers=h, json={"parts": parts}).status_code == 200
                tags = ["ahu-1", "ahu-2"] if name == "mixed" else ["ahu-1"]
                for tag in tags:
                    r = client.post(f"{base}/schedule/systems", headers=h, json=e2e_lib.form_system(pack, EDITION, tag, AC))
                    assert r.status_code == 200, r.text
                rows = rows_of(client.get(f"{base}/gate1", headers=h).json())
                if name.startswith("refuse-"):
                    skip = next(r for r in rows if r["kind"] == name.removeprefix("refuse-"))   # ONE row of that kind stays
                    rows = [x for x in rows if x != skip]
                r = client.post(f"{base}/gate1/confirm", headers=h, json={"rows": rows})
                assert r.status_code == 200, r.text
            view = client.get(f"{base}/gate1", headers=h).json()
            scenarios[name] = {"project": f["project"], "revision": f["revision"], "designer": f["designer"],
                               "checker": f["checker"], "designer_email": f["designer_email"],
                               "checker_email": f["checker_email"], "token": h["Authorization"].split()[1],
                               "health_score": view["health"]["score_percent"] if view["health"] else None,
                               "spaces": len(view["spaces"])}
    fixtures = out.parent / ".fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    workbook = fixtures / "schedule.xlsx"
    workbook.write_bytes(e2e_lib.schedule_workbook(pack, EDITION, "ahu-1", AC))
    n_inputs = len(e2e_lib.sample_inputs(pack, EDITION, AC)) + 1          # + system_type
    out.write_text(json.dumps({"scenarios": scenarios, "workbook": str(workbook), "ifc": str(IFC),
                               "inputs_per_system": n_inputs, "edition": EDITION}, indent=2), encoding="utf-8")
    print(f"seeded {len(scenarios)} scenarios -> {out}")


if __name__ == "__main__":
    main()
