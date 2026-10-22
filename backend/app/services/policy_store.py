from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import PolicyVersion
from app.policy.document import PolicyDocument


def active_policy(db: Session) -> PolicyDocument:
    row = db.execute(select(PolicyVersion).where(PolicyVersion.is_active.is_(True))).scalar_one_or_none()
    if row is None:
        # Fail closed: without an active policy nothing can be authorised.
        raise AppError(503, "POLICY_UNAVAILABLE", "No active data-sharing policy is configured.")
    return PolicyDocument.model_validate(row.document)


def version_key(v: str) -> tuple[int, int, int]:
    y, m, n = v.split(".")
    return int(y), int(m), int(n)
