from __future__ import annotations

import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mep import ratelimit
from mep.ratelimit import SlidingWindowLimiter, classify, client_key, limits_from_env


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_window_slides() -> None:
    clock = Clock()
    lim = SlidingWindowLimiter(clock=clock)
    assert [lim.check("k", 2, 10) for _ in range(2)] == [0.0, 0.0]
    wait = lim.check("k", 2, 10)
    assert 9.9 < wait <= 10
    clock.now += 10
    assert lim.check("k", 2, 10) == 0.0


def test_memory_is_bounded() -> None:
    lim = SlidingWindowLimiter(max_keys=50)
    for i in range(500):
        lim.check(f"k{i}", 1, 60)
    assert len(lim) <= 50


def test_thread_safe_count() -> None:
    lim = SlidingWindowLimiter()
    allowed: list[int] = []

    def hit() -> None:
        for _ in range(50):
            if lim.check("k", 100, 60) == 0.0:
                allowed.append(1)

    threads = [threading.Thread(target=hit) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(allowed) == 100


def test_classify() -> None:
    assert classify("POST", "/revisions/1/uploads") == "upload"
    assert classify("POST", "/revisions/1/base-model") == "upload"
    assert classify("POST", "/revisions/1/clash/models") == "upload"
    assert classify("POST", "/revisions/1/run-rules") == "run"
    assert classify("POST", "/revisions/1/skills/duct-fab/run") == "run"
    assert classify("GET", "/revisions/1/run-rules") is None
    assert classify("POST", "/share/exchange") == "share"
    assert classify("GET", "/me") == "auth"
    assert classify("POST", "/invitations/accept") == "auth"
    assert classify("GET", "/projects") is None


def test_client_key() -> None:
    a = client_key({"authorization": "Bearer abc"}, "1.1.1.1")
    b = client_key({"authorization": "Bearer abd"}, "1.1.1.1")
    assert a != b and a.startswith("u:") and "abc" not in a
    assert client_key({"x-forwarded-for": "9.9.9.9, 2.2.2.2"}, "1.1.1.1") == "ip:2.2.2.2"
    assert client_key({}, "1.1.1.1") == "ip:1.1.1.1"


def test_env_limits() -> None:
    got = limits_from_env({"MEP_RATELIMIT_UPLOAD": "5/30", "MEP_RATELIMIT_RUN": "junk"})
    assert got["upload"] == (5, 30.0)
    assert got["run"] == ratelimit.DEFAULT_LIMITS["run"]


def _app(limits: dict[str, tuple[int, float]]) -> TestClient:
    app = FastAPI()

    @app.post("/revisions/{rid}/uploads")
    def up(rid: str) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/projects")
    def projects() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(ratelimit.RateLimitMiddleware, limits=limits)
    return TestClient(app)


def test_middleware_429_and_per_user_buckets() -> None:
    client = _app({"upload": (2, 60.0)})
    h1 = {"Authorization": "Bearer one-token"}
    assert client.post("/revisions/1/uploads", headers=h1).status_code == 200
    assert client.post("/revisions/1/uploads", headers=h1).status_code == 200
    r = client.post("/revisions/1/uploads", headers=h1)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert r.json()["detail"]["code"] == "rate_limited"
    assert client.post("/revisions/1/uploads", headers={"Authorization": "Bearer other"}).status_code == 200
    for _ in range(5):
        assert client.get("/projects").status_code == 200


def test_install_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MEP_RATELIMIT", "off")
    assert ratelimit.install(FastAPI()) is False
    monkeypatch.delenv("MEP_RATELIMIT")
    assert ratelimit.install(FastAPI()) is True
