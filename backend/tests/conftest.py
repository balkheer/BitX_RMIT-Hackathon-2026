"""Test harness.

Tests run against a real PostgreSQL database (migrated with Alembic and loaded
with the synthetic dataset) and the real FastAPI app. Tokens are signed with
throwaway keys generated per test session and published to the verifier as a
local JWKS - exactly the `jwk` block a TideCloak adapter file can carry - so
every signature, issuer, expiry and `azp` check runs for real. No verification
step is mocked or bypassed.

Required environment (see README "Running tests"):
  CB_TEST_DATABASE_URL            runtime role on a disposable database
  CB_TEST_MIGRATION_DATABASE_URL  owner role on the same database
  CB_TEST_SUPERUSER_URL           superuser, used to recreate the DB and simulate tampering
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from jwt.algorithms import ECAlgorithm, OKPAlgorithm
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

BACKEND = Path(__file__).resolve().parents[1]
ISSUER_BASE = "https://tidecloak.test.invalid"
REALM = "consentbridge-test"
CLIENT_ID = "consentbridge-web"
ISSUER = f"{ISSUER_BASE}/realms/{REALM}"

TEST_DB_URL = os.environ.get("CB_TEST_DATABASE_URL", "")
TEST_OWNER_URL = os.environ.get("CB_TEST_MIGRATION_DATABASE_URL", "")
SUPER_URL = os.environ.get("CB_TEST_SUPERUSER_URL", "")
if not (TEST_DB_URL and TEST_OWNER_URL and SUPER_URL):
    pytest.exit("Set CB_TEST_DATABASE_URL, CB_TEST_MIGRATION_DATABASE_URL and CB_TEST_SUPERUSER_URL", 2)


class Keys:
    def __init__(self) -> None:
        self.ec = ec.generate_private_key(ec.SECP256R1())
        self.ed = ed25519.Ed25519PrivateKey.generate()
        self.attacker = ec.generate_private_key(ec.SECP256R1())

    def jwks(self) -> dict[str, Any]:
        ec_jwk = json.loads(ECAlgorithm.to_jwk(self.ec.public_key()))
        ec_jwk.update({"kid": "test-es256", "use": "sig", "alg": "ES256"})
        ed_jwk = json.loads(OKPAlgorithm.to_jwk(self.ed.public_key()))
        ed_jwk.update({"kid": "test-eddsa", "use": "sig", "alg": "EdDSA"})
        return {"keys": [ec_jwk, ed_jwk]}


KEYS = Keys()


def _write_adapter(tmp: Path) -> Path:
    # Mirrors the shape of a downloaded tidecloak.json (only the fields verification uses).
    adapter = {"realm": REALM, "auth-server-url": ISSUER_BASE, "resource": CLIENT_ID, "public-client": True,
               "jwk": KEYS.jwks()}
    p = tmp / "tidecloak.json"
    p.write_text(json.dumps(adapter))
    return p


_tmp = Path(os.environ.get("PYTEST_TMPDIR", "/tmp")) / f"cb-test-{uuid.uuid4().hex[:8]}"
_tmp.mkdir(parents=True, exist_ok=True)
os.environ.update({
    "CB_ENVIRONMENT": "test",
    "CB_DATABASE_URL": TEST_DB_URL,
    "CB_TIDECLOAK_CONFIG_PATH": str(_write_adapter(_tmp)),
    "CB_RATE_LIMIT_ENABLED": "false",
    "CB_LOG_LEVEL": "WARNING",
})


def mint(
    sub: str,
    *,
    key: str = "ec",
    exp_in: int = 300,
    iss: str = ISSUER,
    azp: str = CLIENT_ID,
    typ: str = "Bearer",
    extra: dict[str, Any] | None = None,
    alg: str | None = None,
    kid: str | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {"iss": iss, "sub": sub, "azp": azp, "typ": typ, "iat": now, "exp": now + exp_in,
                              "jti": uuid.uuid4().hex, "preferred_username": sub}
    claims.update(extra or {})
    if key == "ec":
        return jwt.encode(claims, KEYS.ec, algorithm=alg or "ES256", headers={"kid": kid or "test-es256"})
    if key == "ed":
        return jwt.encode(claims, KEYS.ed, algorithm="EdDSA", headers={"kid": kid or "test-eddsa"})
    if key == "attacker":
        return jwt.encode(claims, KEYS.attacker, algorithm="ES256", headers={"kid": kid or "test-es256"})
    raise ValueError(key)


def sub_for(username: str) -> str:
    return f"sub-{username}"


def auth(username: str, **kw: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(sub_for(username), **kw)}"}


def _recreate_database() -> None:
    url = make_url(TEST_DB_URL)
    owner = make_url(TEST_OWNER_URL).username
    eng = create_engine(SUPER_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{url.database}" OWNER "{owner}"'))
    eng.dispose()


@pytest.fixture(scope="session", autouse=True)
def database() -> Iterator[None]:
    _recreate_database()
    env = {**os.environ, "CB_MIGRATION_DATABASE_URL": TEST_OWNER_URL,
           "CB_APP_DB_ROLE": make_url(TEST_DB_URL).username or "cb_app"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True,
                   capture_output=True)
    subprocess.run([sys.executable, "-m", "app.cli", "seed-synthetic"], cwd=BACKEND, env=env, check=True,
                   capture_output=True)
    with owner_engine().begin() as c:
        for (username,) in c.execute(text("SELECT username FROM users")).all():
            c.execute(text("UPDATE users SET external_issuer=:i, external_subject=:s WHERE username=:u"),
                      {"i": ISSUER, "s": sub_for(username), "u": username})
        org = c.execute(text("SELECT id FROM organizations WHERE slug='consentbridge-platform'")).scalar_one()
        c.execute(text("INSERT INTO users (id, username, display_name, organization_id, role, external_issuer, "
                       "external_subject) VALUES (:id, 'ops.admin', 'Ops Admin', :org, 'platform_admin', :i, :s)"),
                  {"id": uuid.uuid4(), "org": org, "i": ISSUER, "s": sub_for("ops.admin")})
    yield


_engines: dict[str, Engine] = {}


def owner_engine() -> Engine:
    if "owner" not in _engines:
        _engines["owner"] = create_engine(TEST_OWNER_URL)
    return _engines["owner"]


def super_engine() -> Engine:
    if "super" not in _engines:
        u = make_url(SUPER_URL).set(database=make_url(TEST_DB_URL).database)
        _engines["super"] = create_engine(u)
    return _engines["super"]


def app_engine() -> Engine:
    if "app" not in _engines:
        _engines["app"] = create_engine(TEST_DB_URL)
    return _engines["app"]


@pytest.fixture(scope="session")
def client(database: None):  # type: ignore[no-untyped-def]
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    from app.ratelimit import limiter

    limiter.reset()


def make_claim(
    insurer_slug: str = "meridian-insurance",
    custodian_slug: str = "st-aurelia",
    consent_status: str | None = "granted",
    excluded: list[str] | None = None,
    sensitive: bool = False,
) -> str:
    """Create a fresh synthetic patient, encounter, claim and consent so tests are isolated.

    Consent is scoped to patient + purpose + organisation, so sharing a patient
    between tests would let one test's withdrawal affect another.
    """
    ref = f"CLM-T-{uuid.uuid4().hex[:8].upper()}"
    with owner_engine().begin() as c:
        custodian = c.execute(text("SELECT id FROM organizations WHERE slug=:s"), {"s": custodian_slug}).scalar_one()
        insurer = c.execute(text("SELECT id FROM organizations WHERE slug=:s"), {"s": insurer_slug}).scalar_one()
        pid = uuid.uuid4()
        c.execute(text("INSERT INTO patients (id, synthetic_ref, display_alias, birth_year, sex, region, "
                       "custodian_org_id) VALUES (:id, :r, 'Test Synthetic', 1980, 'F', 'Central', :c)"),
                  {"id": pid, "r": f"SYN-T-{uuid.uuid4().hex[:8].upper()}", "c": custodian})
        enc_id = uuid.uuid4()
        c.execute(text(
            "INSERT INTO encounters (id, encounter_reference, patient_id, custodian_org_id, treatment_date, "
            "diagnosis_code, diagnosis_description, diagnosis_category, procedure_code, medication, clinician_notes,"
            " sensitive_notes, sensitive_category, billing_amount_cents) VALUES (:id, :er, :p, :c, '2020-03-14', "
            "'K35.80', 'Acute appendicitis (synthetic)', :cat, 'PR-7710', 'Cefazolin 2g SECRET-MED', "
            "'NOTE-SECRET clinician narrative', :sn, :sens, 512345)"),
            {"id": enc_id, "er": f"ENC-T-{uuid.uuid4().hex[:8]}", "p": pid, "c": custodian,
             "cat": "mental_health" if sensitive else "digestive",
             "sn": "SENSITIVE-SECRET psychosocial" if sensitive else None, "sens": sensitive})
        c.execute(text("INSERT INTO insurance_claims (id, claim_reference, encounter_id, patient_id, insurer_org_id,"
                       " policy_number, amount_claimed_cents) VALUES (:id, :r, :e, :p, :i, 'POL-T', 512345)"),
                  {"id": uuid.uuid4(), "r": ref, "e": enc_id, "p": pid, "i": insurer})
        if consent_status:
            c.execute(text("INSERT INTO patient_consents (id, consent_reference, patient_id, purpose, grantee_org_id,"
                           " status, excluded_fields, valid_until) VALUES (:id, :r, :p, "
                           "'insurance_claim_assessment', :i, :s, :x, '2030-01-01')"),
                      {"id": uuid.uuid4(), "r": f"CNS-{ref}", "p": pid, "i": insurer, "s": consent_status,
                       "x": excluded or []})
    return ref


def set_consent(claim_ref: str, status: str) -> None:
    with owner_engine().begin() as c:
        c.execute(text("UPDATE patient_consents SET status=:s, updated_at=now() WHERE consent_reference=:r"),
                  {"s": status, "r": f"CNS-{claim_ref}"})


def audit_rows(request_id: str | None = None) -> list[dict[str, Any]]:
    with owner_engine().connect() as c:
        q = "SELECT * FROM audit_events" + (" WHERE request_id = :r" if request_id else "") + " ORDER BY seq"
        return [dict(r._mapping) for r in c.execute(text(q), {"r": request_id} if request_id else {}).all()]


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
