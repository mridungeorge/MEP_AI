"""Staging smoke test: sign in, upload the fixture IFC, Gate 1, run the rules, read the report, make and use (then revoke) a share link.

Runs against ANY deployment of the API (local, staging, a preview URL); it reads and writes the one revision you name, so use a throwaway revision
(for example the one `scripts/seed_demo.py` creates, before the live demo). Run it ONCE per revision: after the run the revision has results, and
rerunning is refused (that is the product working). Nothing is hard-coded: no URL, no key.

    uv run python scripts/staging_smoke.py --api https://<your-api> --revision <revision-uuid> --designer-token <JWT>
        [--checker-token <JWT> --approver-token <JWT> --registration "<the approver's registered number>"]
        [--ifc path/to/model.ifc] [--json]

With only the designer token it covers sign-in, upload, Gate 1, the run and the report. With the checker and approver tokens as well it goes on to
FREEZE the revision, sign Gates 2 and 3 (for the throwaway revision only: every result is approved by the script, a FAIL as out of scope), and then
makes, uses and revokes a share link (a link needs a revision signed at Gate 3). Without them the share steps are reported as SKIP.

A designer token is the Supabase access token of a signed-in designer (browser dev tools, or the session after a magic-link sign-in), or one minted
with the API's own secret (`mep.api.auth.mint_token`). Exit status 0 only if every step passed; each step prints PASS or FAIL with the reason.
The rule results it produces come from DRAFT rules and are not engineer-approved: this checks that the pipeline works, not that anything complies.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IFC = ROOT / "tests" / "fixtures" / "ifc" / "bsi-arch-ifc4.ifc"


class Smoke:
    def __init__(self, api: str, token: str, revision: str, ifc: Path, checker_token: str = "", approver_token: str = "", registration: str = "") -> None:
        self.api, self.revision, self.ifc = api.rstrip("/"), revision, ifc
        self.client = httpx.Client(base_url=self.api, timeout=120, headers={"Authorization": f"Bearer {token}"})
        self.checker = httpx.Client(base_url=self.api, timeout=120, headers={"Authorization": f"Bearer {checker_token}"}) if checker_token else None
        self.approver = httpx.Client(base_url=self.api, timeout=120, headers={"Authorization": f"Bearer {approver_token}"}) if approver_token else None
        self.registration = registration
        self.results: list[dict[str, Any]] = []

    def step(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append({"step": name, "ok": ok, "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
        return ok

    def rev(self, path: str = "") -> str:
        return f"/revisions/{self.revision}{path}"

    def _sign_off(self) -> bool:
        assert self.checker is not None and self.approver is not None
        r = self.client.post(self.rev("/freeze"))
        if not self.step("freeze the revision", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120] if r.status_code != 200 else ''}"):
            return False
        ws = self.checker.get(self.rev("/review")).json()
        for res in ws.get("results", []):
            body: dict[str, Any] = {"result_id": res["id"], "decision": "approve", "reason": "smoke test: checked against the clause and inputs"}
            if res["outcome"] == "FAIL":
                body |= {"fail_category": "out_of_scope", "reason": "smoke test: outside the scope of this throwaway revision, noted by the checker"}
            d = self.checker.post(self.rev("/review/decisions"), json=body)
            if d.status_code != 200:
                return self.step("Gate 2: review every result", False, f"HTTP {d.status_code} {d.text[:120]}")
        r = self.checker.post(self.rev("/sign/gate2"), json={})
        if not self.step("Gate 2: the checker signs", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120] if r.status_code != 200 else ''}"):
            return False
        ws = self.approver.get(self.rev("/review")).json()
        for res in ws.get("results", []):
            if res.get("fail_category") and not res.get("acknowledged"):
                self.approver.post(self.rev("/review/acknowledge-fail"), json={"result_id": res["id"], "note": "smoke test: acknowledged"})
        r = self.approver.post(self.rev("/sign/gate3"), json={"registration": self.registration})
        return self.step("Gate 3: the approver signs", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120] if r.status_code != 200 else ''}")


    def run(self) -> bool:
        try:
            return self._run()
        except httpx.HTTPError as exc:
            return self.step("network", False, f"{type(exc).__name__}: {exc}")

    def _run(self) -> bool:
        r = self.client.get("/healthz")
        if not self.step("health check", r.status_code == 200 and r.json().get("status") == "ok", f"HTTP {r.status_code}"):
            return False
        r = self.client.get("/me")
        me = r.json() if r.status_code == 200 else {}
        if not self.step("sign in (token accepted)", r.status_code == 200, f"HTTP {r.status_code}"):
            return False
        if not self.step("signed in as a designer", me.get("role") == "designer", f"role {me.get('role')!r}"):
            return False

        with self.ifc.open("rb") as fh:
            r = self.client.post(self.rev("/uploads"), files={"file": (self.ifc.name, fh.read())})
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        already = r.status_code == 409 and (body.get("detail") or {}).get("code") == "already_uploaded"
        if not self.step("upload the fixture IFC", r.status_code == 200 or already,
                         "already uploaded earlier" if already else f"HTTP {r.status_code}, {body.get('spaces')} space(s)"):
            return False

        view = self.client.get(self.rev("/gate1")).json()
        rows = ([{"kind": "project", "id": view["project"]["id"], "etag": view["project"]["etag"]}]
                + [{"kind": "building_part", "id": p["id"], "etag": p["etag"]} for p in view["parts"]]
                + [{"kind": "space", "id": s["id"], "etag": s["etag"]} for s in view["spaces"]]
                + [{"kind": "system_input", "id": i["id"], "etag": i["etag"]} for i in view["inputs"]])
        r = self.client.post(self.rev("/gate1/confirm"), json={"rows": rows})
        if not self.step("Gate 1: confirm what the designer is shown", r.status_code == 200, f"{len(rows)} row(s), HTTP {r.status_code} {r.text[:120] if r.status_code != 200 else ''}"):
            return False

        r = self.client.post(self.rev("/run-rules"))
        report = (r.json() or {}).get("report", {}) if r.status_code == 200 else {}
        results = report.get("results", [])
        if not self.step("run the rules", r.status_code == 200 and bool(results), f"HTTP {r.status_code}, {len(results)} result(s)"):
            return False
        cited = all((x.get("citation") or {}).get("clause") and (x.get("citation") or {}).get("document") for x in results)
        if not self.step("report: every result cites its clause", cited):
            return False
        self.step("report: carries the draft-rules banner", str(report.get("banner", "")).startswith("DRAFT RULES"), str(report.get("banner")))

        if self.checker is None or self.approver is None:
            self.step("sign Gates 2 and 3 and share (SKIP: needs --checker-token and --approver-token)", True, "skipped")
            return all(x["ok"] for x in self.results)
        if not self._sign_off():
            return False
        r = self.client.post(self.rev("/share-links"), json={"days": 1, "label": "smoke test"})
        token = (r.json() or {}).get("token") if r.status_code == 200 else None
        if not self.step("create a share link", bool(token), f"HTTP {r.status_code}"):
            return False
        public = httpx.Client(base_url=self.api, timeout=60)
        r = public.post("/share/exchange", json={"token": token})
        if not self.step("exchange the link for a read-only session", r.status_code == 200, f"HTTP {r.status_code}"):
            return False
        r = public.get("/share/package")
        shared_ok = r.status_code == 200 and (r.json().get("revision") or {}).get("id") == self.revision
        self.step("the share session reads the package", shared_ok, f"HTTP {r.status_code}")
        links = self.client.get(self.rev("/share-links")).json()
        link_id = next((link["id"] for link in links if link.get("label") == "smoke test" and not link.get("revoked_at")), None)
        if link_id:
            self.client.delete(self.rev(f"/share-links/{link_id}"))
            ended = public.get("/share/package").status_code == 401
            self.step("revoking the link ends the session", ended)
        else:
            self.step("revoke the share link", False, "the link was not listed")
        return all(x["ok"] for x in self.results)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", required=True, help="base URL of the API, e.g. https://mep-api.up.railway.app")
    ap.add_argument("--revision", required=True, help="an open revision of the designer's firm (a throwaway one)")
    ap.add_argument("--designer-token", default=os.environ.get("SMOKE_DESIGNER_TOKEN", ""), help="or set SMOKE_DESIGNER_TOKEN")
    ap.add_argument("--checker-token", default=os.environ.get("SMOKE_CHECKER_TOKEN", ""))
    ap.add_argument("--approver-token", default=os.environ.get("SMOKE_APPROVER_TOKEN", ""))
    ap.add_argument("--registration", default=os.environ.get("SMOKE_REGISTRATION", ""), help="the approver's registered number, as stored")
    ap.add_argument("--ifc", type=Path, default=DEFAULT_IFC, help="the fixture IFC to upload")
    ap.add_argument("--json", action="store_true", help="print the step results as JSON at the end")
    args = ap.parse_args()
    if not args.designer_token:
        print("a designer token is required (--designer-token or SMOKE_DESIGNER_TOKEN)", file=sys.stderr)
        return 64
    if not args.ifc.is_file():
        print(f"fixture not found: {args.ifc}", file=sys.stderr)
        return 64
    smoke = Smoke(args.api, args.designer_token, args.revision, args.ifc, args.checker_token, args.approver_token, args.registration)
    ok = smoke.run()
    if args.json:
        print(json.dumps(smoke.results, indent=2))
    print("SMOKE TEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
