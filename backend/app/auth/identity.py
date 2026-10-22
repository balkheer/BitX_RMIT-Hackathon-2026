"""Trusted identity mapping and request-scoped principal.

A verified TideCloak token proves *who* the caller is (issuer + subject).
What they may do comes only from the ConsentBridge `users` row that an
administrator linked to that issuer/subject pair. Roles, organisation IDs or
permission claims sent by the client - in headers, bodies or token claims - are
never used for authorisation.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.chain import Actor, append_event
from app.auth.tidecloak import AuthNotConfigured, AuthProviderUnavailable, TideCloakVerifier, TokenInvalid
from app.config import get_settings, load_tidecloak_adapter
from app.db import get_db
from app.errors import AppError, forbidden, unauthorized
from app.models import Organization, User

log = logging.getLogger("consentbridge.auth")

SESSION_GAP = dt.timedelta(minutes=30)
TOUCH_INTERVAL = dt.timedelta(minutes=5)


@lru_cache
def get_verifier() -> TideCloakVerifier:
    settings = get_settings()
    return TideCloakVerifier(settings, load_tidecloak_adapter(settings))


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    username: str
    display_name: str
    role: str
    org_id: uuid.UUID
    org_name: str
    org_type: str
    org_slug: str

    @property
    def actor(self) -> Actor:
        return Actor(user_id=self.id, display=self.display_name, role=self.role, org_id=self.org_id)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def get_current_user(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    verifier: Annotated[TideCloakVerifier, Depends(get_verifier)],
) -> CurrentUser:
    token = _bearer(request)
    try:
        verified = verifier.verify(token)
    except TokenInvalid as exc:
        log.info("token rejected: %s", exc)  # reason only; never the token
        raise unauthorized("Your session is missing, invalid or expired. Please sign in again.",
                           code="INVALID_TOKEN") from exc
    except AuthNotConfigured as exc:
        raise AppError(503, "AUTH_NOT_CONFIGURED", "Authentication is not configured on this server.") from exc
    except AuthProviderUnavailable as exc:
        raise AppError(503, "AUTH_PROVIDER_UNAVAILABLE",
                       "The identity provider is unreachable. Please try again shortly.") from exc

    settings = get_settings()
    row = db.execute(
        select(User, Organization)
        .join(Organization, Organization.id == User.organization_id)
        .where(User.external_issuer == verified.issuer, User.external_subject == verified.subject)
    ).first()

    def deny(reason: str, code: str, msg: str, user: User | None = None) -> AppError:
        append_event(
            db,
            actor=Actor(user.id, user.display_name, user.role, user.organization_id) if user else None,
            action="AUTH_DENIED",
            outcome="denied",
            resource_type="identity",
            policy_codes=[code],
            details={"reason": reason, "subject_hash": _subject_fingerprint(verified.subject)},
        )
        db.commit()
        return forbidden(msg, code=code)

    if row is None:
        raise deny("identity_not_mapped", "IDENTITY_NOT_MAPPED",
                   "Your identity is verified but has not been provisioned for ConsentBridge. "
                   "Ask a platform administrator to link your account.")
    user, org = row
    if not user.is_active or not org.is_active:
        raise deny("account_disabled", "ACCOUNT_DISABLED", "Your ConsentBridge account is disabled.", user)
    if settings.tidecloak_required_role and settings.tidecloak_required_role not in verified.roles:
        raise deny("idp_role_missing", "IDP_ROLE_MISSING",
                   "Your identity-provider account lacks the role required for ConsentBridge.", user)

    now = dt.datetime.now(dt.UTC)
    if user.last_seen_at is None or now - user.last_seen_at > SESSION_GAP:
        append_event(
            db,
            actor=Actor(user.id, user.display_name, user.role, user.organization_id),
            action="AUTH_SESSION_STARTED",
            outcome="success",
            resource_type="identity",
            details={"method": "tidecloak_oidc"},
        )
        user.last_seen_at = now
        db.commit()
    elif now - user.last_seen_at > TOUCH_INTERVAL:
        user.last_seen_at = now
        db.commit()

    request.state.user_id = str(user.id)
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        org_id=org.id,
        org_name=org.name,
        org_type=org.org_type,
        org_slug=org.slug,
    )


def _subject_fingerprint(subject: str) -> str:
    import hashlib

    return hashlib.sha256(subject.encode()).hexdigest()[:16]


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def require_roles(*roles: str):  # type: ignore[no-untyped-def]
    """Dependency factory: 403 unless the mapped role is one of `roles`."""

    def checker(
        user: CurrentUserDep,
        request: Request,
        db: Annotated[Session, Depends(get_db)],
    ) -> CurrentUser:
        if user.role not in roles:
            record_denied(db, user, "ROLE_NOT_PERMITTED", request.url.path)
            raise forbidden()
        return user

    return checker


def record_denied(db: Session, user: CurrentUser, code: str, resource: str,
                  resource_type: str = "endpoint", related: list[uuid.UUID] | None = None) -> None:
    """Audit a denied access attempt in its own transaction (no sensitive data)."""
    db.rollback()
    append_event(
        db,
        actor=user.actor,
        action="ACCESS_DENIED",
        outcome="denied",
        resource_type=resource_type,
        resource_id=resource[:64],
        related_org_ids=related,
        policy_codes=[code],
    )
    db.commit()
