"""Production wiring: the Gate 1 and schedule routers on Postgres, with JWT authentication and the service ledger.

    MEP_DB_URL        Postgres URL (a role that may `set local role authenticated`; the local Supabase `postgres` user)
    MEP_JWT_SECRET    the HS256 secret that signs access tokens (at least 32 bytes)
    MEP_CORS_ORIGINS  comma-separated web origins allowed to call the API (default: none)
    MEP_RULES_DIR     rule pack folder (default: <repo>/rules)
    MEP_SUPABASE_URL, MEP_SUPABASE_ANON_KEY   Supabase API (storage) for uploads; without them the upload route answers 503
    MEP_ALLOW_DEMO_JWT_SECRET=1   local runs only: accept the public Supabase demo secret

Run:  uvicorn --factory mep.api.server:app_from_env --port 8000
"""
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import psycopg
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mep import ratelimit
from mep.api import admin as admin_api
from mep.api import agents as agents_api
from mep.api import base_model as base_model_api
from mep.api import billing as billing_api
from mep.api import commissioning as commissioning_api
from mep.api import declaration as declaration_api
from mep.api import evidence as evidence_api
from mep.api import feedback as feedback_api
from mep.api import fixes as fixes_api
from mep.api import gate1, revisions, sizing_api, standards_api, uploads
from mep.api import me as me_api
from mep.api import notifications as notifications_api
from mep.api import performance as performance_api
from mep.api import platform as platform_api
from mep.api import projects as projects_api
from mep.api import review as review_api
from mep.api import schedule as schedule_api
from mep.api import services as services_api
from mep.api import skills as skills_api
from mep.api import vision_jobs as vision_jobs_api
from mep.api.agents_pg import PgAgentBackend
from mep.api.app import create_app
from mep.api.auth import make_auth
from mep.api.pg import PgLedger, PgRepository
from mep.api.review_pg import PgReview, PgShare
from mep.api.schedule import CurrentUser
from mep.api.skills_pg import PgSkills
from mep.api.uploads_pg import PgUploads
from mep.diff.graph import build_graph
from mep.engine.loader import RulePack, load_pack
from mep.ingest.vision_anthropic import vision_from_env
from mep.observability import RequestLogMiddleware, configure_logging, init_sentry


class _RedactShareTokens(logging.Filter):
    """The certifier token is the credential and sits in the URL path: keep it out of the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(re.sub(r"(/share/)[^/\s?\"]+", r"\1[redacted]", a) if isinstance(a, str) else a for a in record.args)
        return True


REPO_ROOT = Path(__file__).resolve().parents[4]
DEMO_JWT_SECRET = "super-secret-jwt-token-with-at-least-32-characters-long"   # `supabase start` default


def create_pg_app(dsn: str, jwt_secret: str, pack: RulePack, cors_origins: list[str] | None = None,
                  supabase_url: str | None = None, anon_key: str | None = None, vision: Any = None, vision_worker: bool = False,
                  skill_executor: str | None = None, mailer: Any = None, stripe: Any = None) -> FastAPI:
    jwks_url = f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json" if supabase_url else None
    logging.getLogger("uvicorn.access").addFilter(_RedactShareTokens())
    current_user, token_subject = make_auth(dsn, jwt_secret, jwks_url)
    app = create_app(None, current_user, pack, ledger=PgLedger(dsn))
    ratelimit.install(app)                       # per user and per client IP on upload, run, share and sign-in routes (off with MEP_RATELIMIT=off)

    ready_cache: dict[str, Any] = {"at": -1e9, "ok": False}
    ready_lock = threading.Lock()

    @app.get("/readyz")
    def readyz() -> Any:
        """Ready when the database answers and the schema is migrated. The answer is cached for 5 seconds (an unauthenticated route must not be able to use up
        the database's connections) and says only ready / not ready: no checks, no versions, nothing about why."""
        from fastapi.responses import JSONResponse
        with ready_lock:
            if time.monotonic() - ready_cache["at"] > 5:
                try:
                    with psycopg.connect(dsn, connect_timeout=3, autocommit=True) as conn:
                        ready_cache["ok"] = bool(conn.execute("select to_regclass('public.firm') is not null and to_regclass('public.notification') is not null").fetchone()[0])
                except psycopg.Error:
                    ready_cache["ok"] = False
                ready_cache["at"] = time.monotonic()
            ok = bool(ready_cache["ok"])
        return JSONResponse(status_code=200 if ok else 503, content={"status": "ready" if ok else "not_ready"})

    def repository(user: CurrentUser = Depends(current_user)) -> PgRepository:  # noqa: B008
        return PgRepository(dsn, user, pack)

    app.dependency_overrides[gate1.get_repository] = repository
    app.include_router(uploads.router)
    app.add_middleware(uploads.UploadGuard)
    app.include_router(me_api.router)
    app.include_router(revisions.router)
    graph = build_graph(pack)
    app.dependency_overrides[revisions.current_user] = current_user
    app.dependency_overrides[revisions.get_repository] = repository
    app.dependency_overrides[revisions.get_graph] = lambda: graph
    app.dependency_overrides[revisions.get_pack] = lambda: pack
    app.include_router(review_api.router)
    app.dependency_overrides[review_api.current_user] = current_user
    app.dependency_overrides[review_api.get_service] = lambda user=Depends(current_user): PgReview(dsn, user)  # noqa: B008
    share = PgShare(dsn)
    app.dependency_overrides[review_api.get_share] = lambda: share
    app.include_router(skills_api.router)
    app.dependency_overrides[skills_api.current_user] = current_user
    app.dependency_overrides[skills_api.get_service] = lambda user=Depends(current_user): PgSkills(dsn, user, skill_executor)  # noqa: B008
    app.include_router(vision_jobs_api.router)
    app.dependency_overrides[vision_jobs_api.current_user] = current_user
    app.dependency_overrides[vision_jobs_api.get_dsn] = lambda: dsn
    app.include_router(feedback_api.router)
    app.include_router(standards_api.router)
    app.include_router(commissioning_api.router)
    app.include_router(declaration_api.router)
    app.include_router(base_model_api.router)
    app.include_router(sizing_api.router)
    app.include_router(services_api.router)
    app.dependency_overrides[services_api.get_dsn] = lambda: dsn
    app.include_router(performance_api.router)
    app.dependency_overrides[performance_api.get_dsn] = lambda: dsn
    app.include_router(fixes_api.router)
    app.dependency_overrides[fixes_api.get_dsn] = lambda: dsn
    app.include_router(billing_api.router)
    app.dependency_overrides[billing_api.current_user] = current_user
    app.dependency_overrides[billing_api.get_dsn] = lambda: dsn
    app.dependency_overrides[billing_api.get_stripe] = lambda: stripe
    app.include_router(notifications_api.router)
    app.dependency_overrides[notifications_api.current_user] = current_user
    app.dependency_overrides[notifications_api.get_dsn] = lambda: dsn
    app.dependency_overrides[admin_api.get_mailer] = lambda: mailer
    app.include_router(projects_api.router)
    app.dependency_overrides[projects_api.current_user] = current_user
    app.dependency_overrides[projects_api.get_dsn] = lambda: dsn
    app.include_router(platform_api.router)
    app.dependency_overrides[platform_api.current_user] = current_user
    app.dependency_overrides[platform_api.get_dsn] = lambda: dsn
    app.include_router(admin_api.router)
    app.dependency_overrides[admin_api.current_user] = current_user
    app.dependency_overrides[admin_api.token_subject] = token_subject
    app.dependency_overrides[admin_api.get_dsn] = lambda: dsn
    app.include_router(evidence_api.router)
    app.dependency_overrides[evidence_api.current_user] = current_user
    app.dependency_overrides[evidence_api.get_dsn] = lambda: dsn
    app.include_router(agents_api.router)
    app.dependency_overrides[agents_api.current_user] = current_user
    app.dependency_overrides[agents_api.get_backend_factory] = lambda: (lambda user, rev: PgAgentBackend(dsn, user, rev, pack, skill_executor))
    app.dependency_overrides[me_api.current_user] = current_user
    app.dependency_overrides[me_api.get_repository] = repository
    app.dependency_overrides[uploads.current_user] = current_user
    if supabase_url and anon_key:      # without a storage endpoint the upload route refuses (503)
        service = PgUploads(dsn, supabase_url, anon_key, vision=vision if vision is not None else vision_from_env())
        app.dependency_overrides[uploads.get_service] = lambda: service
        app.dependency_overrides[skills_api.get_uploads] = lambda: service
    app.dependency_overrides[schedule_api.get_repository] = repository
    if vision_worker:                  # PDF drawings are read by this background thread (one per process; replicas share the queue)
        runner = vision_jobs_api.VisionRunner(dsn, vision if vision is not None else vision_from_env())
        app.router.on_startup.append(runner.start)
        app.router.on_shutdown.append(runner.stop)
    if mailer is not None and (vision_worker or os.environ.get("MEP_MAIL_WORKER") == "1"):      # e-mail is delivered by a background thread; without a provider the outbox waits
        mail_runner = notifications_api.NotificationRunner(dsn, mailer)
        app.router.on_startup.append(mail_runner.start)
        app.router.on_shutdown.append(mail_runner.stop)
    if cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET", "POST", "PUT", "DELETE"],
                           allow_headers=["Authorization", "Content-Type", "X-Acting-Role"])
    return app


def app_from_env() -> FastAPI:
    dsn, secret = os.environ.get("MEP_DB_URL", ""), os.environ.get("MEP_JWT_SECRET", "")
    if not dsn or not secret:
        raise RuntimeError("MEP_DB_URL and MEP_JWT_SECRET must be set")
    if secret == DEMO_JWT_SECRET and os.environ.get("MEP_ALLOW_DEMO_JWT_SECRET") != "1":
        # the local Supabase's published secret: anyone could sign a token for any user id
        raise RuntimeError("MEP_JWT_SECRET is the public demo secret; set MEP_ALLOW_DEMO_JWT_SECRET=1 for local runs only")
    # a deployment never runs a drafting build inside the API process (no isolation): without the worker, drafting is off
    configure_logging("api")
    init_sentry("api")
    stripe_on, mail_on = bool(os.environ.get("STRIPE_SECRET_KEY")), bool(os.environ.get("RESEND_API_KEY"))
    if stripe_on and not (os.environ.get("STRIPE_WEBHOOK_SECRET") and os.environ.get("MEP_APP_URL")):
        raise RuntimeError("STRIPE_SECRET_KEY is set: STRIPE_WEBHOOK_SECRET and MEP_APP_URL must be set too (otherwise a payment would never reach the subscription)")
    if mail_on and not (os.environ.get("MEP_APP_URL") and os.environ.get("MEP_MAIL_FROM")):
        raise RuntimeError("RESEND_API_KEY is set: MEP_APP_URL and MEP_MAIL_FROM must be set too (links in e-mails would point nowhere)")
    skill_executor = "queue" if os.environ.get("MEP_SKILL_EXECUTOR") == "queue" else ("local" if os.environ.get("MEP_ALLOW_LOCAL_SKILLS") == "1" else "disabled")
    origins = [o.strip() for o in os.environ.get("MEP_CORS_ORIGINS", "").split(",") if o.strip()]
    rules = Path(os.environ.get("MEP_RULES_DIR") or REPO_ROOT / "rules")
    app = create_pg_app(dsn, secret, load_pack(rules), origins, os.environ.get("MEP_SUPABASE_URL"),
                         os.environ.get("MEP_SUPABASE_ANON_KEY"), vision_worker=os.environ.get("MEP_VISION_WORKER", "1") != "0",
                         skill_executor=skill_executor, mailer=notifications_api.mailer_from_env(), stripe=billing_api.stripe_from_env())
    app.add_middleware(RequestLogMiddleware)
    return app
