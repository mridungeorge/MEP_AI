"""Who am I, and which revisions can I open. Both are read as the signed-in user, so row-level security decides what is listed.

The role and firm come from `app_user` (never the token), and the UI shows the role in its header; it is a display, the server
enforces every permission on its own.
"""
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException

from mep.api.schedule import CurrentUser

router = APIRouter()


class MeRepository(Protocol):
    def me(self, user: CurrentUser) -> dict[str, Any]: ...

    def list_revisions(self, user: CurrentUser) -> list[dict[str, Any]]: ...


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_repository() -> MeRepository:
    raise HTTPException(status_code=503, detail="repository is not configured")


User = Annotated[CurrentUser, Depends(current_user)]
Repo = Annotated[MeRepository, Depends(get_repository)]


@router.get("/me")
def me(user: User, repo: Repo) -> dict[str, Any]:
    return repo.me(user)


@router.get("/revisions")
def revisions(user: User, repo: Repo) -> list[dict[str, Any]]:
    return repo.list_revisions(user)
