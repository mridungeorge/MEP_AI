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

    cooldown = 30.0          # at most one fetch attempt (successful or not) per this many seconds
    key_lifetime = 600.0     # a key is trusted from the set fetched at most this long ago, then the set is fetched again

    def get_signing_key(self, kid: str) -> Any:
        import time
        d = self.__dict__
        now = time.monotonic()
        known, fetched = d.get("_known", {}), d.get("_fetched", -1e9)
        if kid in known and now - fetched < self.key_lifetime:
            return known[kid]
        if now - d.get("_attempted", -1e9) < self.cooldown:
            if kid in known and now - fetched < 2 * self.key_lifetime:
                return known[kid]              # the key service is not answering: keep the last set a little longer
            raise jwt.PyJWKClientError(f"Unable to find a signing key that matches: {kid}")
        d["_attempted"] = now                   # recorded BEFORE the fetch, so an outage is rate limited too
        keys = {k.key_id: k for k in self.get_signing_keys(refresh=True)}
        d["_known"], d["_fetched"] = keys, now
        if kid not in keys:
            raise jwt.PyJWKClientError(f"Unable to find a signing key that matches: {kid}")
        return keys[kid]


def _unauthenticated(message: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": "unauthenticated", "message": message})


def make_current_user(dsn: str, jwt_secret: str, jwks_url: str | None = None) -> Callable[..., CurrentUser]:
    current_user, _ = make_auth(dsn, jwt_secret, jwks_url)
    return current_user


def make_auth(dsn: str, jwt_secret: str, jwks_url: str | None = None) -> tuple[Callable[..., CurrentUser], Callable[..., uuid.UUID]]:
    """(current_user, token_subject). The second only proves WHO signed in (no firm needed): used to join a firm by invitation."""
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

    def token_subject(authorization: Annotated[str | None, Header()] = None) -> uuid.UUID:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthenticated("a bearer token is required")
        try:
            return uuid.UUID(str(decode(token.strip())["sub"]))
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            raise _unauthenticated("the token is invalid or expired") from None

    def current_user(authorization: Annotated[str | None, Header()] = None,
                     x_acting_role: Annotated[str | None, Header()] = None) -> CurrentUser:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthenticated("a bearer token is required")
        try:
            claims = decode(token.strip())
            user_id = uuid.UUID(str(claims["sub"]))
        except (jwt.PyJWTError, ValueError, TypeError):          # TypeError: a key of the wrong type for the algorithm
            raise _unauthenticated("the token is invalid or expired") from None
        with psycopg.connect(dsn, autocommit=True) as conn:
            row = conn.execute(
                "select u.firm_id, u.role::text, u.also_roles::text[], f.signer_mode, u.active from app_user u join firm f on f.id = u.firm_id"
                " where u.id = %s", (user_id,)).fetchone()
        if row is None:
            raise _unauthenticated("no such user")
        if not row[4]:
            raise HTTPException(status_code=403, detail={"code": "deactivated", "message": "this account has been deactivated by your firm's administrator"})
        if x_acting_role and x_acting_role != row[1]:
            # acting in another role is only for a small firm, and only in a role the person has been given; the database checks too
            if row[3] != "small_firm" or x_acting_role not in (row[2] or []):
                raise HTTPException(status_code=403, detail={"code": "not_permitted_role",
                                                             "message": f"you cannot act as {x_acting_role} in this firm"})
            return CurrentUser(user_id=user_id, firm_id=row[0], role=x_acting_role, acting_role=x_acting_role)
        return CurrentUser(user_id=user_id, firm_id=row[0], role=row[1])

    return current_user, token_subject


def mint_token(jwt_secret: str, user_id: uuid.UUID | str, *, ttl_seconds: int = 3600) -> str:
    """Test and seed helper: sign an access token the way the local Supabase's legacy secret would."""
    import time
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "aud": AUDIENCE, "role": "authenticated", "iat": now,
                       "exp": now + ttl_seconds}, jwt_secret, algorithm="HS256")
