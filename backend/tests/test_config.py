"""Configuration parsing and production guard rails."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

BASE = {"database_url": "postgresql+psycopg://u:p@h/db"}


def test_csv_and_json_lists(monkeypatch):
    monkeypatch.setenv("CB_TRUSTED_HOSTS", "app.example.org, api.example.org")
    monkeypatch.setenv("CB_CORS_ALLOWED_ORIGINS", '["https://app.example.org"]')
    s = Settings(**BASE)
    assert s.trusted_hosts == ["app.example.org", "api.example.org"]
    assert s.cors_allowed_origins == ["https://app.example.org"]


@pytest.mark.parametrize("algs", ["HS256", "none", "ES256,HS512"])
def test_symmetric_or_none_algorithms_refused(monkeypatch, algs):
    monkeypatch.setenv("CB_TIDECLOAK_ALGORITHMS", algs)
    with pytest.raises(ValidationError):
        Settings(**BASE)


def test_production_guards(monkeypatch):
    monkeypatch.setenv("CB_ENVIRONMENT", "production")
    monkeypatch.delenv("CB_TRUSTED_HOSTS", raising=False)
    with pytest.raises(ValidationError):
        Settings(**BASE)  # trusted hosts must be explicit
    monkeypatch.setenv("CB_TRUSTED_HOSTS", "app.example.org")
    monkeypatch.setenv("CB_CORS_ALLOWED_ORIGINS", "*")
    with pytest.raises(ValidationError):
        Settings(**BASE)
    monkeypatch.setenv("CB_CORS_ALLOWED_ORIGINS", "https://app.example.org")
    monkeypatch.setenv("CB_AGGREGATE_MIN_GROUP_SIZE", "3")
    with pytest.raises(ValidationError):
        Settings(**BASE)
    monkeypatch.setenv("CB_AGGREGATE_MIN_GROUP_SIZE", "5")
    assert Settings(**BASE).environment == "production"


def test_production_requires_https_identity_provider(monkeypatch, tmp_path):
    import json

    from app.config import load_tidecloak_adapter

    f = tmp_path / "tidecloak.json"
    f.write_text(json.dumps({"realm": "r", "auth-server-url": "http://idp.local:8080", "resource": "c"}))
    monkeypatch.setenv("CB_ENVIRONMENT", "production")
    monkeypatch.setenv("CB_TRUSTED_HOSTS", "app.example.org")
    monkeypatch.setenv("CB_TIDECLOAK_CONFIG_PATH", str(f))
    with pytest.raises(ValueError):
        load_tidecloak_adapter(Settings(**BASE))
    f.write_text(json.dumps({"realm": "r", "auth-server-url": "https://idp.example.org", "resource": "c"}))
    adapter = load_tidecloak_adapter(Settings(**BASE))
    assert adapter is not None and adapter.issuer == "https://idp.example.org/realms/r"
    assert adapter.jwks_uri == "https://idp.example.org/realms/r/protocol/openid-connect/certs"


def test_internal_url_changes_key_fetch_but_not_issuer(monkeypatch, tmp_path):
    import json

    from app.config import load_tidecloak_adapter

    f = tmp_path / "tidecloak.json"
    f.write_text(json.dumps({"realm": "r", "auth-server-url": "http://localhost:8080", "resource": "c"}))
    monkeypatch.setenv("CB_TIDECLOAK_CONFIG_PATH", str(f))
    monkeypatch.setenv("CB_TIDECLOAK_INTERNAL_URL", "http://tidecloak:8080/")
    adapter = load_tidecloak_adapter(Settings(**BASE))
    assert adapter is not None
    assert adapter.issuer == "http://localhost:8080/realms/r"
    assert adapter.jwks_uri == "http://tidecloak:8080/realms/r/protocol/openid-connect/certs"
