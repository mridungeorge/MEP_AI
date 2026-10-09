"""Tokens signed by Supabase Auth's asymmetric key (ES256, published as a JWKS) are accepted; every confusion attack is not."""
import base64
import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls.conftest import DB_URL

KID = "test-key-1"


def pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


@pytest.fixture(scope="module")
def keys():
    return ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="module")
def jwks_server(keys):
    good = keys[0]
    jwk = json.loads(ECAlgorithm.to_jwk(good.public_key()))
    jwk.update(kid=KID, alg="ES256", use="sig")
    rsa_jwk = json.loads(RSAAlgorithm.to_jwk(rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()))
    rsa_jwk.update(kid="rsa-1", alg="RS256", use="sig")
    body = json.dumps({"keys": [jwk, rsa_jwk]}).encode()
    hits: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):          # http.server calls this name
            hits.append(1)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    _SERVERS[base] = hits
    yield base
    server.shutdown()


def app(base, pack_dir=h.ROOT / "rules"):
    # the JWKS URL is derived from the Supabase URL: <base>/auth/v1/.well-known/jwks.json (the stub serves every path)
    return TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(pack_dir), supabase_url=base, anon_key="anon"))


def es256(user, key, kid=KID, **extra):
    claims = {"sub": str(user), "aud": "authenticated", "exp": int(time.time()) + 600, **extra}
    return jwt.encode(claims, pem(key), algorithm="ES256", headers={"kid": kid})


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def forge_hs256(secret: bytes, user) -> str:
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    body = b64(json.dumps({"sub": str(user), "aud": "authenticated", "exp": int(time.time()) + 60}).encode())
    mac = hmac.new(secret, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(mac)}"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_a_token_signed_by_the_published_key_is_accepted_and_the_role_is_still_the_databases(admin, jwks_server, keys):
    f = h.seed(admin)
    c = app(jwks_server)
    r = c.get("/me", headers=bearer(es256(f["checker"], keys[0], role="designer", user_metadata={"role": "approver"})))
    assert r.status_code == 200 and r.json()["role"] == "checker"


def test_every_forgery_and_confusion_is_refused(admin, jwks_server, keys):
    f = h.seed(admin)
    c = app(jwks_server)
    good, other = keys
    # signed by a different key under the same kid
    assert c.get("/me", headers=bearer(es256(f["designer"], other))).status_code == 401
    # an unknown kid
    assert c.get("/me", headers=bearer(es256(f["designer"], good, kid="nope"))).status_code == 401
    # expired, wrong audience, no exp
    expired = jwt.encode({"sub": f["designer"], "aud": "authenticated", "exp": int(time.time()) - 5}, pem(good),
                         algorithm="ES256", headers={"kid": KID})
    assert c.get("/me", headers=bearer(expired)).status_code == 401
    wrong_aud = jwt.encode({"sub": f["designer"], "aud": "anon", "exp": int(time.time()) + 60}, pem(good),
                           algorithm="ES256", headers={"kid": KID})
    assert c.get("/me", headers=bearer(wrong_aud)).status_code == 401
    no_exp = jwt.encode({"sub": f["designer"], "aud": "authenticated"}, pem(good), algorithm="ES256", headers={"kid": KID})
    assert c.get("/me", headers=bearer(no_exp)).status_code == 401
    # algorithm confusion: an HS256 token MAC-ed by hand with the PUBLIC key (PEM, or its raw coordinate) as the secret
    public_pem = good.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    raw_x = base64.urlsafe_b64decode(json.loads(ECAlgorithm.to_jwk(good.public_key()))["x"] + "==")
    for secret in (public_pem, raw_x * 2):
        assert c.get("/me", headers=bearer(forge_hs256(secret, f["designer"]))).status_code == 401
    # alg none and an unsupported algorithm
    none = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode() + "." + \
        base64.urlsafe_b64encode(json.dumps({"sub": f["designer"], "aud": "authenticated", "exp": int(time.time()) + 60}).encode()
                                 ).rstrip(b"=").decode() + "."
    assert c.get("/me", headers=bearer(none)).status_code == 401
    # a valid token for a user who is not an app user
    import uuid
    assert c.get("/me", headers=bearer(es256(uuid.uuid4(), good))).status_code == 401


def test_without_a_jwks_the_asymmetric_tokens_are_refused_and_the_secret_ones_still_work(admin, keys):
    f = h.seed(admin)
    c = TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(h.ROOT / "rules")))
    assert c.get("/me", headers=bearer(es256(f["designer"], keys[0]))).status_code == 401
    assert c.get("/me", headers=h.auth(f["designer"])).status_code == 200


def test_an_unreachable_key_service_is_a_503_not_a_401(admin, keys):
    f = h.seed(admin)
    c = app("http://127.0.0.1:9")                       # nothing listens there
    r = c.get("/me", headers=bearer(es256(f["designer"], keys[0])))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "auth_unavailable"


def test_a_key_of_the_wrong_type_for_the_algorithm_is_a_401_not_a_500(admin, jwks_server, keys):
    f = h.seed(admin)
    c = app(jwks_server)
    wrong_type = es256(f["designer"], keys[0], kid="rsa-1")           # the kid names an RSA key; the token claims ES256
    assert c.get("/me", headers=bearer(wrong_type)).status_code == 401


def test_unknown_key_ids_do_not_make_the_api_refetch_the_key_set_every_time(admin, jwks_server, keys):
    f = h.seed(admin)
    c = app(jwks_server)
    assert c.get("/me", headers=bearer(es256(f["designer"], keys[0]))).status_code == 200      # fetches and caches the keys
    before = len(jwks_server_hits(jwks_server))
    for n in range(8):
        assert c.get("/me", headers=bearer(es256(f["designer"], keys[0], kid=f"unknown-{n}"))).status_code == 401
    assert len(jwks_server_hits(jwks_server)) - before <= 1                                       # at most one refetch in the cooldown


_SERVERS: dict[str, list[int]] = {}


def jwks_server_hits(base: str) -> list[int]:
    return _SERVERS[base]
