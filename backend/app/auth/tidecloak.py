"""Server-side verification of TideCloak-issued access tokens.

This mirrors the checks in Tide's own `@tidecloak/verify` package
(issuer = `{auth-server-url}/realms/{realm}`, JWKS from the adapter's `jwk`
block or `{issuer}/protocol/openid-connect/certs`, pinned asymmetric algorithm
allowlist, `exp`/`nbf` with a small clock tolerance, `azp` == client id) and
adds:

* `exp`, `iat` and `sub` are required claims;
* `typ` must be `Bearer` (rejects ID/refresh tokens replayed as access tokens);
* optional `aud` check when an audience mapper is configured;
* JWKS fetch failures are reported as 503, not silently as 401.

It never decodes a token without verifying it first.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWK, PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from app.config import Settings, TideCloakAdapter


class TokenInvalid(Exception):
    """The token is missing, malformed, expired, or fails a check (HTTP 401)."""


class AuthProviderUnavailable(Exception):
    """Keys could not be obtained from the identity provider (HTTP 503)."""


class AuthNotConfigured(Exception):
    """No TideCloak configuration is present (HTTP 503, fail closed)."""


@dataclass(frozen=True)
class VerifiedToken:
    issuer: str
    subject: str
    claims: dict[str, Any]

    @property
    def roles(self) -> set[str]:
        roles = set(self.claims.get("realm_access", {}).get("roles", []) or [])
        azp = self.claims.get("azp")
        if azp:
            roles |= set(self.claims.get("resource_access", {}).get(azp, {}).get("roles", []) or [])
        return roles


class _LocalJWKS:
    def __init__(self, jwks: dict[str, Any]):
        self._keys: dict[str | None, PyJWK] = {}
        for k in jwks.get("keys", []):
            if k.get("use", "sig") != "sig":
                continue
            jwk = PyJWK.from_dict(k)
            self._keys[k.get("kid")] = jwk

    def get_signing_key(self, kid: str | None) -> PyJWK:
        if kid in self._keys:
            return self._keys[kid]
        if kid is None and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        raise TokenInvalid("signing key not found")


class TideCloakVerifier:
    def __init__(self, settings: Settings, adapter: TideCloakAdapter | None):
        self.settings = settings
        self.adapter = adapter
        self._lock = threading.Lock()
        self._local: _LocalJWKS | None = None
        self._remote: PyJWKClient | None = None
        if adapter is not None:
            if adapter.jwk:
                self._local = _LocalJWKS(adapter.jwk)
            else:
                self._remote = PyJWKClient(
                    adapter.jwks_uri, cache_keys=True, lifespan=settings.jwks_cache_seconds, timeout=5
                )
        self._last_remote_error: float | None = None

    @property
    def configured(self) -> bool:
        return self.adapter is not None

    def _key_for(self, token: str) -> Any:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenInvalid("malformed token") from exc
        alg = header.get("alg")
        if alg not in self.settings.tidecloak_algorithms:
            raise TokenInvalid("token algorithm not permitted")
        kid = header.get("kid")
        if self._local is not None:
            return self._local.get_signing_key(kid).key
        assert self._remote is not None
        try:
            with self._lock:
                return self._remote.get_signing_key_from_jwt(token).key
        except PyJWKClientConnectionError as exc:
            self._last_remote_error = time.time()
            raise AuthProviderUnavailable("could not fetch signing keys") from exc
        except PyJWKClientError as exc:
            raise TokenInvalid("signing key not found") from exc

    def verify(self, token: str | None) -> VerifiedToken:
        if self.adapter is None:
            raise AuthNotConfigured("TideCloak is not configured")
        if not token:
            raise TokenInvalid("missing token")
        if len(token) > 16384 or token.count(".") != 2:
            raise TokenInvalid("malformed token")
        key = self._key_for(token)
        options: Any = {
            "require": ["exp", "iat", "iss", "sub"],
            "verify_signature": True,
            "verify_exp": True,
            "verify_nbf": True,
            "verify_iat": True,
            "verify_iss": True,
            "verify_aud": bool(self.settings.tidecloak_audience),
        }
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=self.settings.tidecloak_algorithms,
                issuer=self.adapter.issuer,
                audience=self.settings.tidecloak_audience,
                leeway=self.settings.tidecloak_clock_tolerance_seconds,
                options=options,
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenInvalid("token expired") from exc
        except jwt.InvalidIssuerError as exc:
            raise TokenInvalid("untrusted issuer") from exc
        except jwt.InvalidAudienceError as exc:
            raise TokenInvalid("token audience not accepted") from exc
        except jwt.PyJWTError as exc:
            raise TokenInvalid("token verification failed") from exc

        resource = self.adapter.resource
        if resource is not None and claims.get("azp") != resource:
            raise TokenInvalid("token was not issued to this client")
        if self.settings.tidecloak_require_bearer_typ and claims.get("typ") != "Bearer":
            raise TokenInvalid("not an access token")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject or len(subject) > 255:
            raise TokenInvalid("invalid subject")
        return VerifiedToken(issuer=self.adapter.issuer, subject=subject, claims=claims)

    def status(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "issuer": self.adapter.issuer if self.adapter else None,
            "key_source": "adapter_jwk" if self._local else ("remote_jwks" if self._remote else None),
        }


def load_jwks_json(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if "keys" not in data:
        raise ValueError("JWKS must contain 'keys'")
    return data
