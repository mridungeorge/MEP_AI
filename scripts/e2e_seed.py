"""Seed the local Supabase for the Playwright e2e run and write the state file the tests read.

Every scenario is a fresh firm, project and revision. Data goes in through the real API code (an in-process TestClient on
the Postgres app) and the real ingest store, so the e2e starts from the same rows production would hold:

  flow                       a public IFC ingested (spaces are 'extracted'); nothing else. The UI test does the rest.
  refuse-project / -building_part / -space / -system_input
                             everything entered and confirmed EXCEPT that one kind.

    uv run python scripts/e2e_seed.py --out apps/web/e2e/.state.json
"""
import argparse
import json
import os
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any

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
EDITION, AC, IFC = "NCC2025", "air_conditioning", ROOT / "tests/fixtures/ifc/bsi-arch-ifc4.ifc"
KINDS = ("project", "building_part", "space", "system_input")


def make_world(conn: psycopg.Connection[Any], label: str) -> dict[str, str]:
    f = {k: str(uuid.uuid4()) for k in ("firm", "project", "revision", "designer", "checker")}
    conn.execute("insert into firm (id, name) values (%s, %s)", (f["firm"], f"e2e {label}"))
    for role in ("designer", "checker"):
        conn.execute("insert into auth.users (id, email) values (%s, %s)", (f[role], f"{role}-{f[role]}@e2e.invalid"))
        conn.execute("insert into app_user (id, firm_id, role) values (%s, %s, %s)", (f[role], f["firm"], role))
    conn.execute("insert into project (id, firm_id, address, state, climate_zone, ncc_edition, approval_date)"
                 " values (%s, %s, '1 E2E St', 'VIC', 6, %s, %s)", (f["project"], f["firm"], EDITION, date(2026, 10, 1)))
    conn.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'A')",
                 (f["revision"], f["firm"], f["project"]))
    return f


def ingest_ifc(conn: psycopg.Connection[Any], f: dict[str, str]) -> None:
    store_ingest(conn, firm_id=f["firm"], revision_id=f["revision"], result=read_ifc(IFC))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    out: Path = ap.parse_args().out
    pack = load_pack(ROOT / "rules")
    client = TestClient(create_pg_app(DB_URL, SECRET, pack))
    scenarios: dict[str, dict[str, Any]] = {}
    with psycopg.connect(DB_URL, autocommit=True) as conn:
        for name in ("flow", *[f"refuse-{k}" for k in KINDS]):
            f = make_world(conn, name)
            ingest_ifc(conn, f)
            h = {"Authorization": f"Bearer {mint_token(SECRET, f['designer'], ttl_seconds=6 * 3600)}"}
            base = f"/revisions/{f['revision']}"
            if name != "flow":
                assert client.put(f"{base}/gate1/parts", headers=h, json={"parts": [
                    {"building_class": "5", "storeys": 3, "area_m2": 400}]}).status_code == 200
                r = client.post(f"{base}/schedule/systems", headers=h, json=e2e_lib.form_system(pack, EDITION, "ahu-1", AC))
                assert r.status_code == 200, r.text
                v = client.get(f"{base}/gate1", headers=h).json()
                rows = ([{"kind": "project", "id": v["project"]["id"]}]
                        + [{"kind": "building_part", "id": p["id"]} for p in v["parts"]]
                        + [{"kind": "space", "id": s["id"]} for s in v["spaces"]]
                        + [{"kind": "system_input", "id": i["id"]} for i in v["inputs"]])
                left = name.removeprefix("refuse-")
                # leave exactly ONE row of the kind unconfirmed
                skip = next(r for r in rows if r["kind"] == left)
                r = client.post(f"{base}/gate1/confirm", headers=h, json={"rows": [x for x in rows if x != skip]})
                assert r.status_code == 200, r.text
            view = client.get(f"{base}/gate1", headers=h).json()
            scenarios[name] = {"project": f["project"], "revision": f["revision"], "designer": f["designer"],
                               "checker": f["checker"], "token": h["Authorization"].split()[1],
                               "health_score": view["health"]["score_percent"] if view["health"] else None,
                               "spaces": len(view["spaces"])}
    fixtures = out.parent / ".fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    workbook = fixtures / "schedule.xlsx"
    workbook.write_bytes(e2e_lib.schedule_workbook(pack, EDITION, "ahu-1", AC))
    n_inputs = len(e2e_lib.sample_inputs(pack, EDITION, AC)) + 1          # + system_type
    out.write_text(json.dumps({"scenarios": scenarios, "workbook": str(workbook), "inputs_per_system": n_inputs,
                               "edition": EDITION}, indent=2), encoding="utf-8")
    print(f"seeded {len(scenarios)} scenarios -> {out}")


if __name__ == "__main__":
    main()
