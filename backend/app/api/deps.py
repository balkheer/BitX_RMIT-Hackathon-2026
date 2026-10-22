from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser, get_current_user
from app.config import get_settings
from app.db import get_db
from app.ratelimit import limiter

DB = Annotated[Session, Depends(get_db)]
User = Annotated[CurrentUser, Depends(get_current_user)]

PERMISSIONS: dict[str, list[str]] = {
    "hospital_staff": ["dashboard:read", "requests:create", "requests:read", "patients:read_own_org",
                       "referrals:read", "audit:read", "policy:read"],
    "insurance_officer": ["dashboard:read", "requests:create", "requests:read", "claims:read",
                          "releases:read", "audit:read", "policy:read"],
    "government_officer": ["dashboard:read", "requests:create", "requests:read", "stats:query",
                           "mandates:read", "audit:read", "policy:read"],
    "privacy_reviewer": ["dashboard:read", "requests:read", "reviews:decide", "audit:read", "audit:verify",
                         "policy:read"],
    "platform_admin": ["dashboard:read", "requests:read_metadata", "audit:read_all", "audit:verify",
                       "policy:read", "policy:write", "admin:users", "admin:organizations"],
}


def sensitive_rate_limit(request: Request, user: User) -> None:
    settings = get_settings()
    if settings.rate_limit_enabled:
        limiter.check(f"{user.id}:{request.url.path.split('/')[2] if request.url.path.count('/') > 1 else ''}",
                      settings.rate_limit_sensitive_per_minute)


RateLimited = Depends(sensitive_rate_limit)
