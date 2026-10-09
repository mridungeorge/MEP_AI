"""Authentication: a Supabase-style HS256 access token names the user; the firm and role come from `app_user`.

Nothing in the token body other than `sub` (and `exp`) is trusted: a client-editable `role` or `firm_id` claim is
ignored, so a token cannot grant a role. Unknown users are refused. Used by the Postgres-backed app (mep.api.server).
"""
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import jwt
import psycopg
from fastapi import Header, HTTPException

from mep.api.schedule import CurrentUser

AUDIENCE = "authenticated"
MIN_SECRET_BYTES = 32


def _unauthenticated(message: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": "unauthenticated", "message": message})


def make_current_user(dsn: str, jwt_secret: str) -> Callable[..., CurrentUser]:
    if len(jwt_secret.encode()) < MIN_SECRET_BYTES:
        raise ValueError("the JWT secret must be at least 32 bytes")

    def current_user(authorization: Annotated[str | None, Header()] = None) -> CurrentUser:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthenticated("a bearer token is required")
        try:
            claims: dict[str, Any] = jwt.decode(
                token.strip(), jwt_secret, algorithms=["HS256"], audience=AUDIENCE,
                options={"require": ["exp", "sub"]})
            user_id = uuid.UUID(str(claims["sub"]))
        except (jwt.PyJWTError, ValueError):
            raise _unauthenticated("the token is invalid or expired") from None
        with psycopg.connect(dsn, autocommit=True) as conn:
            row = conn.execute("select firm_id, role::text from app_user where id = %s", (user_id,)).fetchone()
        if row is None:
            raise _unauthenticated("no such user")
        return CurrentUser(user_id=user_id, firm_id=row[0], role=row[1])

    return current_user


def mint_token(jwt_secret: str, user_id: uuid.UUID | str, *, ttl_seconds: int = 3600) -> str:
    """Test and seed helper: sign an access token the way the local Supabase does."""
    import time
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "aud": AUDIENCE, "role": "authenticated", "iat": now,
                       "exp": now + ttl_seconds}, jwt_secret, algorithm="HS256")
