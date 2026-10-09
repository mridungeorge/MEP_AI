"""Production wiring: the Gate 1 and schedule routers on Postgres, with JWT authentication and the service ledger.

    MEP_DB_URL        Postgres URL (a role that may `set local role authenticated`; the local Supabase `postgres` user)
    MEP_JWT_SECRET    the HS256 secret that signs access tokens (at least 32 bytes)
    MEP_CORS_ORIGINS  comma-separated web origins allowed to call the API (default: none)
    MEP_RULES_DIR     rule pack folder (default: <repo>/rules)

Run:  uvicorn --factory mep.api.server:app_from_env --port 8000
"""
import os
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mep.api import gate1
from mep.api import schedule as schedule_api
from mep.api.app import create_app
from mep.api.auth import make_current_user
from mep.api.pg import PgLedger, PgRepository
from mep.api.schedule import CurrentUser
from mep.engine.loader import RulePack, load_pack

REPO_ROOT = Path(__file__).resolve().parents[4]


def create_pg_app(dsn: str, jwt_secret: str, pack: RulePack, cors_origins: list[str] | None = None) -> FastAPI:
    current_user = make_current_user(dsn, jwt_secret)
    app = create_app(None, current_user, pack, ledger=PgLedger(dsn))

    def repository(user: CurrentUser = Depends(current_user)) -> PgRepository:  # noqa: B008
        return PgRepository(dsn, user, pack)

    app.dependency_overrides[gate1.get_repository] = repository
    app.dependency_overrides[schedule_api.get_repository] = repository
    if cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET", "POST", "PUT"],
                           allow_headers=["Authorization", "Content-Type"])
    return app


def app_from_env() -> FastAPI:
    dsn, secret = os.environ.get("MEP_DB_URL", ""), os.environ.get("MEP_JWT_SECRET", "")
    if not dsn or not secret:
        raise RuntimeError("MEP_DB_URL and MEP_JWT_SECRET must be set")
    origins = [o.strip() for o in os.environ.get("MEP_CORS_ORIGINS", "").split(",") if o.strip()]
    rules = Path(os.environ.get("MEP_RULES_DIR") or REPO_ROOT / "rules")
    return create_pg_app(dsn, secret, load_pack(rules), origins)
