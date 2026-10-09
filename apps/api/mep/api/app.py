"""Application factory: wires the injected repository, rule pack and current user into the routers."""
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from mep.api import gate1
from mep.api import schedule as schedule_api
from mep.engine.loader import RulePack

TEMPLATE_DIR = Path(__file__).resolve().parents[4] / "docs" / "templates"
TEMPLATE_NAME = re.compile(r"^mep-system-schedule-NCC20(22|25)\.xlsx$")


def create_app(repo: Any, current_user: Callable[..., Any], pack: RulePack | None = None,
               schedule_repo: Any = None, ledger: Any = None) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Drop the echoed input: it can hold values (inf, nan) that JSON cannot carry, and need not be echoed.
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.get("/healthz")
    async def _healthz() -> dict[str, str]:
        return {"status": "ok"}          # for the host's health check: no database, no data

    @app.get("/templates/{filename}")
    async def _template(filename: str) -> FileResponse:
        # the published Excel templates only: the name must match exactly, nothing else is served from this route
        path = TEMPLATE_DIR / filename
        if not TEMPLATE_NAME.fullmatch(filename) or not path.is_file():
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "no such template"})
        return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    app.include_router(gate1.router)
    app.dependency_overrides[gate1.get_repository] = lambda: repo
    app.dependency_overrides[gate1.current_user] = current_user
    if ledger is not None:
        app.dependency_overrides[gate1.get_ledger] = lambda: ledger
    app.include_router(schedule_api.router)
    app.dependency_overrides[schedule_api.current_user] = current_user
    app.dependency_overrides[schedule_api.get_repository] = lambda: schedule_repo if schedule_repo is not None else repo
    if pack is not None:
        app.dependency_overrides[gate1.get_pack] = lambda: pack
        app.dependency_overrides[schedule_api.get_pack] = lambda: pack
    return app
