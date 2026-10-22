"""Authentication: token verification and trusted identity mapping."""

from __future__ import annotations

import base64
import json
import time

import jwt
from sqlalchemy import text

from tests.conftest import ISSUER, KEYS, audit_rows, auth, mint, owner_engine, sub_for


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_missing_token_is_401(client):
    r = client.get("/api/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_TOKEN"
    assert r.headers["www-authenticate"].startswith("Bearer")


def test_non_bearer_scheme_is_401(client):
    assert client.get("/api/me", headers={"Authorization": "Basic dXNlcjpwYXNz"}).status_code == 401


def test_malformed_token_is_401(client):
    assert client.get("/api/me", headers=bearer("not.a.jwt")).status_code == 401
    assert client.get("/api/me", headers=bearer("abc")).status_code == 401


def test_valid_es256_token(client):
    r = client.get("/api/me", headers=auth("j.walker"))
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "insurance_officer"
    assert body["organization"]["slug"] == "meridian-insurance"


def test_valid_eddsa_token(client):
    r = client.get("/api/me", headers=auth("j.walker", key="ed"))
    assert r.status_code == 200


def test_wrong_signing_key_is_401(client):
    # Same kid as the real key, signed by an attacker-held key.
    assert client.get("/api/me", headers=auth("j.walker", key="attacker")).status_code == 401


def test_expired_token_is_401(client):
    r = client.get("/api/me", headers=auth("j.walker", exp_in=-120))
    assert r.status_code == 401


def test_expiry_within_clock_tolerance_is_accepted(client):
    assert client.get("/api/me", headers=auth("j.walker", exp_in=-2)).status_code == 200


def test_untrusted_issuer_is_401(client):
    r = client.get("/api/me", headers=auth("j.walker", iss="https://evil.example/realms/consentbridge-test"))
    assert r.status_code == 401


def test_token_for_another_client_is_401(client):
    assert client.get("/api/me", headers=auth("j.walker", azp="some-other-app")).status_code == 401


def test_id_token_cannot_be_used_as_access_token(client):
    assert client.get("/api/me", headers=auth("j.walker", typ="ID")).status_code == 401


def test_unknown_kid_is_401(client):
    assert client.get("/api/me", headers=auth("j.walker", kid="unknown-kid")).status_code == 401


def test_alg_none_is_401(client):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=")
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": sub_for("j.walker"), "azp": "consentbridge-web", "typ": "Bearer",
              "iat": now, "exp": now + 300}
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    token = f"{header.decode()}.{body.decode()}."
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_hs256_with_public_key_confusion_is_401(client):
    # Classic algorithm confusion: HMAC-sign with the server's *public* key as the secret.
    import hashlib
    import hmac

    from cryptography.hazmat.primitives import serialization

    pem = KEYS.ec.public_key().public_bytes(serialization.Encoding.PEM,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": sub_for("j.walker"), "azp": "consentbridge-web", "typ": "Bearer",
              "iat": now, "exp": now + 300}

    def b64(d: bytes) -> bytes:
        return base64.urlsafe_b64encode(d).rstrip(b"=")

    signing_input = b64(json.dumps({"alg": "HS256", "kid": "test-es256"}).encode()) + b"." + b64(
        json.dumps(claims).encode())
    sig = b64(hmac.new(pem, signing_input, hashlib.sha256).digest())
    token = (signing_input + b"." + sig).decode()
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_missing_required_claims_is_401(client):
    now = int(time.time())
    token = jwt.encode({"iss": ISSUER, "azp": "consentbridge-web", "typ": "Bearer", "iat": now, "exp": now + 60},
                       KEYS.ec, algorithm="ES256", headers={"kid": "test-es256"})
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_verified_but_unmapped_identity_is_403_and_audited(client):
    r = client.get("/api/me", headers=bearer(mint("sub-stranger")))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "IDENTITY_NOT_MAPPED"
    last = audit_rows()[-1]
    assert last["action"] == "AUTH_DENIED" and "sub-stranger" not in json.dumps(last["details"])


def test_token_claims_cannot_grant_roles_or_org(client):
    # A valid token stuffed with privileged-looking claims still maps to the DB record only.
    token = mint(sub_for("j.walker"), extra={
        "realm_access": {"roles": ["platform_admin", "privacy_reviewer"]},
        "resource_access": {"consentbridge-web": {"roles": ["platform_admin"]}},
        "org_id": "00000000-0000-0000-0000-000000000000", "role": "platform_admin"})
    headers = {**bearer(token), "X-Org-Id": "st-aurelia", "X-Role": "platform_admin"}
    me = client.get("/api/me", headers=headers).json()
    assert me["role"] == "insurance_officer" and me["organization"]["slug"] == "meridian-insurance"
    assert client.get("/api/admin/users", headers=headers).status_code == 403
    assert client.get("/api/patients", headers=headers).status_code == 403


def test_disabled_user_is_403(client):
    with owner_engine().begin() as c:
        c.execute(text("UPDATE users SET is_active=false WHERE username='s.lim'"))
    try:
        r = client.get("/api/me", headers=auth("s.lim"))
        assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_DISABLED"
    finally:
        with owner_engine().begin() as c:
            c.execute(text("UPDATE users SET is_active=true WHERE username='s.lim'"))


def test_disabled_organization_is_403(client):
    with owner_engine().begin() as c:
        c.execute(text("UPDATE organizations SET is_active=false WHERE slug='kestrel-mutual'"))
    try:
        assert client.get("/api/me", headers=auth("s.lim")).status_code == 403
    finally:
        with owner_engine().begin() as c:
            c.execute(text("UPDATE organizations SET is_active=true WHERE slug='kestrel-mutual'"))


def test_identity_must_match_issuer_too(client):
    # Same subject string but registered under a different issuer must not map.
    with owner_engine().begin() as c:
        c.execute(text("UPDATE users SET external_issuer='https://other-idp/realms/x' WHERE username='s.lim'"))
    try:
        assert client.get("/api/me", headers=auth("s.lim")).status_code == 403
    finally:
        with owner_engine().begin() as c:
            c.execute(text("UPDATE users SET external_issuer=:i WHERE username='s.lim'"), {"i": ISSUER})


def test_auth_not_configured_fails_closed(client):
    from app.auth.identity import get_verifier
    from app.auth.tidecloak import TideCloakVerifier
    from app.config import get_settings
    from app.main import app

    app.dependency_overrides[get_verifier] = lambda: TideCloakVerifier(get_settings(), None)
    try:
        r = client.get("/api/me", headers=auth("j.walker"))
        assert r.status_code == 503 and r.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"
    finally:
        app.dependency_overrides.clear()


def test_session_start_is_audited(client):
    with owner_engine().begin() as c:
        c.execute(text("UPDATE users SET last_seen_at=NULL WHERE username='t.brennan'"))
    client.get("/api/me", headers=auth("t.brennan"))
    events = [e for e in audit_rows() if e["action"] == "AUTH_SESSION_STARTED" and e["actor_display"] == "Tess Brennan"]
    assert events
    assert "eyJ" not in json.dumps(events[-1]["details"])  # no token material


def test_health_endpoints(client):
    assert client.get("/healthz").json()["status"] == "ok"
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["auth_configured"] is True
    assert r.json()["checks"]["policy_version"]
