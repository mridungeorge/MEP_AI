"""Authentication: a Supabase access token names the user; the firm and role come from `app_user`.

Two kinds of token are accepted, each checked ONLY against its own key material (the algorithm is read from the token header,
then enforced as the single allowed algorithm, so an HS256 token can never be verified with a public key):

* HS256, signed with the project's JWT secret (`MEP_JWT_SECRET`): tests, seeds and legacy projects;
* ES256 / RS256, signed by Supabase Auth's signing key, verified against its published JWKS (`<supabase>/auth/v1/.well-known/jwks.json`).

Nothing in the token body other than `sub` (and `exp`, `aud`) is trusted: a client-editable `role` or `firm_id` claim is ignored, so
a token cannot grant a role. Unknown users are refused. Used by the Postgres-backed app (mep.api.server).
"""
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import jwt
import psycopg
from fastapi import Header, HTTPException
from jwt import PyJWKClient

from mep.api.schedule import CurrentUser

AUDIENCE = "authenticated"
MIN_SECRET_BYTES = 32
ASYMMETRIC = ("ES256", "RS256")


class _RateLimitedJWKS(PyJWKClient):
    """PyJWKClient refetches the key set on every unknown `kid`, so an unauthenticated caller could make the API send one outbound
    request per request. Refetch at most once per `cooldown` seconds."""

    cooldown = 30.0

    def get_signing_key(self, kid: str) -> Any:
        import time
        try:
            return self.__dict__["_known"][kid]
        except KeyError:
            pass
        now = time.monotonic()
        if now - self.__dict__.get("_fetched", -1e9) < self.cooldown and "_known" in self.__dict__:
            raise jwt.PyJWKClientError(f"Unable to find a signing key that matches: {kid}")
        keys = {k.key_id: k for k in self.get_signing_keys(refresh=True)}
        self.__dict__["_known"], self.__dict__["_fetched"] = keys, now
        if kid not in keys:
            raise jwt.PyJWKClientError(f"Unable to find a signing key that matches: {kid}")
        return keys[kid]


def _unauthenticated(message: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": "unauthenticated", "message": message})


def make_current_user(dsn: str, jwt_secret: str, jwks_url: str | None = None) -> Callable[..., CurrentUser]:
    if len(jwt_secret.encode()) < MIN_SECRET_BYTES:
        raise ValueError("the JWT secret must be at least 32 bytes")
    jwks = _RateLimitedJWKS(jwks_url, cache_keys=True, lifespan=600, timeout=5) if jwks_url else None

    def decode(token: str) -> dict[str, Any]:
        alg = jwt.get_unverified_header(token).get("alg")
        options: Any = {"require": ["exp", "sub"]}
        if alg == "HS256":
            return jwt.decode(token, jwt_secret, algorithms=["HS256"], audience=AUDIENCE, options=options)
        if alg in ASYMMETRIC and jwks is not None:
            try:
                key = jwks.get_signing_key_from_jwt(token).key
            except jwt.PyJWKClientConnectionError:
                raise HTTPException(status_code=503, detail={"code": "auth_unavailable",
                                                             "message": "the sign-in service's keys could not be fetched"}) from None
            return jwt.decode(token, key, algorithms=[alg], audience=AUDIENCE, options=options)
        raise jwt.InvalidAlgorithmError("unsupported token algorithm")

    def current_user(authorization: Annotated[str | None, Header()] = None) -> CurrentUser:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthenticated("a bearer token is required")
        try:
            claims = decode(token.strip())
            user_id = uuid.UUID(str(claims["sub"]))
        except (jwt.PyJWTError, ValueError, TypeError):          # TypeError: a key of the wrong type for the algorithm
            raise _unauthenticated("the token is invalid or expired") from None
        with psycopg.connect(dsn, autocommit=True) as conn:
            row = conn.execute("select firm_id, role::text from app_user where id = %s", (user_id,)).fetchone()
        if row is None:
            raise _unauthenticated("no such user")
        return CurrentUser(user_id=user_id, firm_id=row[0], role=row[1])

    return current_user


def mint_token(jwt_secret: str, user_id: uuid.UUID | str, *, ttl_seconds: int = 3600) -> str:
    """Test and seed helper: sign an access token the way the local Supabase's legacy secret would."""
    import time
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "aud": AUDIENCE, "role": "authenticated", "iat": now,
                       "exp": now + ttl_seconds}, jwt_secret, algorithm="HS256")
