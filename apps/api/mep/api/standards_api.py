"""Licensed-standard slots as the app shows them. Read-only: there is no way to switch a licence on, or to produce a result for a slot, through the API."""
from typing import Any
from uuid import UUID

from fastapi import APIRouter

from mep import standards
from mep.api.revisions import Repo, _err
from mep.api.revisions import User as RevUser

router = APIRouter()


@router.get("/standards")
def slots(user: RevUser) -> dict[str, Any]:
    return {"slots": standards.all_states(),
            "note": "Rule packs for these standards need a licensed copy of the standard. None is encoded. A slot that is not READY produces no result."}


@router.get("/revisions/{revision_id}/standards")
def for_revision(revision_id: UUID, user: RevUser, repo: Repo) -> dict[str, Any]:
    if repo.revision_info(revision_id, user.firm_id) is None:
        raise _err(404, "not_found", "revision not found")
    return {"slots": [{**s, "results": [], "evaluated": False} for s in standards.all_states()],
            "note": "Checks against these standards are not run: a result is never produced for a slot that is not READY."}

