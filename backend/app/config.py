"""Environment-based configuration.

All secrets come from the environment (or a secret manager that populates it).
Nothing confidential has a default value here.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Lists accept either JSON ('["a","b"]') or comma-separated text ('a,b'); NoDecode lets our validator parse both.
StrList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CB_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"

    # Runtime connection uses the least-privileged application role.
    database_url: str = Field(..., description="SQLAlchemy URL for the runtime (cb_app) role")
    # Migrations use the schema-owner role. Only needed by Alembic / the CLI.
    migration_database_url: str | None = None

    # --- TideCloak (OIDC) -------------------------------------------------
    # Path to the adapter file downloaded from tide-console
    # (Apps -> your app -> Credentials -> Tide adapter -> Download tidecloak.json).
    tidecloak_config_path: str | None = None
    # Alternatively, supply the three values explicitly.
    tidecloak_auth_server_url: str | None = None
    tidecloak_realm: str | None = None
    tidecloak_resource: str | None = None  # client id; checked against the `azp` claim
    # Base URL the API uses to fetch signing keys when it reaches TideCloak by a different address than
    # browsers do (e.g. http://tidecloak:8080 inside Docker). The expected token issuer is unchanged.
    tidecloak_internal_url: str | None = None
    # Optional expected audience. TideCloak's own verifier checks `azp`; set this
    # only if an audience mapper adds this value to `aud`.
    tidecloak_audience: str | None = None
    # Same default allowlist as @tidecloak/verify.
    tidecloak_algorithms: StrList = ["ES256", "ES384", "ES512", "EdDSA"]
    tidecloak_clock_tolerance_seconds: int = 5
    # Keycloak-family access tokens carry typ=Bearer; rejecting other types
    # stops ID tokens or refresh tokens being replayed as access tokens.
    tidecloak_require_bearer_typ: bool = True
    # Optional realm/client role every user must hold in the token, in addition
    # to the ConsentBridge identity mapping (defence in depth).
    tidecloak_required_role: str | None = None
    jwks_cache_seconds: int = 300

    # --- HTTP --------------------------------------------------------------
    cors_allowed_origins: StrList = []
    trusted_hosts: StrList = ["*"]

    # --- Policy / privacy --------------------------------------------------
    aggregate_min_group_size: int = 5

    # --- Abuse controls ----------------------------------------------------
    rate_limit_sensitive_per_minute: int = 30
    rate_limit_enabled: bool = True

    log_level: str = "INFO"

    @field_validator("cors_allowed_origins", "trusted_hosts", "tidecloak_algorithms", mode="before")
    @classmethod
    def _split_csv(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                return json.loads(v)
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @field_validator("tidecloak_algorithms")
    @classmethod
    def _no_symmetric_or_none(cls, v: list[str]) -> list[str]:
        bad = [a for a in v if a.lower() == "none" or a.upper().startswith("HS")]
        if bad:
            raise ValueError("symmetric or 'none' algorithms are not permitted for access tokens")
        if not v:
            raise ValueError("at least one token signature algorithm is required")
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        if self.environment == "production":
            if "*" in self.cors_allowed_origins:
                raise ValueError("wildcard CORS origin is not allowed in production")
            if self.trusted_hosts == ["*"]:
                raise ValueError("CB_TRUSTED_HOSTS must be set in production")
            if self.aggregate_min_group_size < 5:
                raise ValueError("aggregate minimum group size below 5 is not allowed in production")
        return self


class TideCloakAdapter:
    """The subset of tidecloak.json that server-side verification needs."""

    def __init__(self, auth_server_url: str, realm: str, resource: str | None, jwk: dict[str, Any] | None,
                 internal_url: str | None = None):
        self.auth_server_url = auth_server_url
        self.realm = realm
        self.resource = resource
        self.jwk = jwk
        self.internal_url = internal_url

    @property
    def issuer(self) -> str:
        # Mirrors @tidecloak/verify: `${auth-server-url}/realms/${realm}`
        sep = "" if self.auth_server_url.endswith("/") else "/"
        return f"{self.auth_server_url}{sep}realms/{self.realm}"

    @property
    def jwks_uri(self) -> str:
        base = (self.internal_url or self.auth_server_url).rstrip("/")
        return f"{base}/realms/{self.realm}/protocol/openid-connect/certs"


def load_tidecloak_adapter(settings: Settings) -> TideCloakAdapter | None:
    data: dict[str, Any] = {}
    if settings.tidecloak_config_path:
        path = Path(settings.tidecloak_config_path)
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
    auth_server_url = settings.tidecloak_auth_server_url or data.get("auth-server-url")
    realm = settings.tidecloak_realm or data.get("realm")
    resource = settings.tidecloak_resource or data.get("resource")
    jwk = data.get("jwk")
    if not auth_server_url or not realm:
        return None
    if settings.environment == "production" and not str(auth_server_url).startswith("https://"):
        raise ValueError("production requires an https:// TideCloak auth-server-url")
    return TideCloakAdapter(auth_server_url=auth_server_url, realm=realm, resource=resource, jwk=jwk,
                            internal_url=settings.tidecloak_internal_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
