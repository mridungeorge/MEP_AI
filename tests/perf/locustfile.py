"""HTTP load test for a running API (not part of the pytest suite; locust is not a project dependency).

    pip install locust            # in a throwaway venv, not in pyproject
    MEP_TOKEN=<bearer> MEP_REVISION=<revision uuid> locust -f tests/perf/locustfile.py --host http://localhost:8000

Environment: MEP_TOKEN (bearer token), MEP_REVISION (a revision id the token's firm can run), optional MEP_UPLOAD_FILE (a small
IFC or schedule file to POST), MEP_DIFF_PARENT is not needed (the diff is read from the revision itself).

Page targets (docs/performance.md): a rule run finishes in < 10 s, every other page or download in < 3 s. The checks below mark a
request failed when it is slower than its target, so locust's failure column is the pass/fail of the target.
The endpoint paths below follow apps/api/mep/api/server.py; adjust them there first if a route is renamed.
"""
import os
from pathlib import Path

RUN_TARGET_S = 10.0
PAGE_TARGET_S = 3.0

try:
    from locust import HttpUser, between, task
except ImportError as exc:  # pragma: no cover - locust is optional
    raise SystemExit("locust is not installed. Install it in a throwaway environment (pip install locust); it is deliberately not a project dependency.") from exc


def _need(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"set {name} before starting locust (see the module docstring)")
    return value


TOKEN = _need("MEP_TOKEN")
REVISION = _need("MEP_REVISION")
UPLOAD = os.environ.get("MEP_UPLOAD_FILE")


class Engineer(HttpUser):
    wait_time = between(1, 3)

    def on_start(self) -> None:
        self.client.headers["Authorization"] = f"Bearer {TOKEN}"

    def _timed(self, method: str, path: str, name: str, target_s: float, **kw: object) -> None:
        with self.client.request(method, path, name=name, catch_response=True, **kw) as resp:  # type: ignore[arg-type]
            if resp.status_code >= 400:
                resp.failure(f"HTTP {resp.status_code}")
            elif resp.elapsed.total_seconds() > target_s:
                resp.failure(f"{resp.elapsed.total_seconds():.1f}s is over the {target_s:g}s target")
            else:
                resp.success()

    @task(1)
    def upload(self) -> None:
        if not UPLOAD or not Path(UPLOAD).is_file():
            return
        with open(UPLOAD, "rb") as fh:
            self._timed("POST", f"/revisions/{REVISION}/uploads", "upload", PAGE_TARGET_S * 3, files={"file": (Path(UPLOAD).name, fh)})

    @task(2)
    def run_rules(self) -> None:
        self._timed("POST", f"/revisions/{REVISION}/run-rules", "run rules", RUN_TARGET_S)

    @task(3)
    def diff(self) -> None:
        self._timed("GET", f"/revisions/{REVISION}/diff", "revision diff", PAGE_TARGET_S)

    @task(2)
    def package_pdf(self) -> None:
        self._timed("GET", f"/revisions/{REVISION}/package.pdf", "package.pdf", PAGE_TARGET_S)
