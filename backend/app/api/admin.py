"""Policy configuration and administration. Every change is validated and audited.

Administrators manage users, organisations and policy versions. They cannot
read patient values, release data, review requests, or change their own account
(separation of duties). Creating or promoting another platform administrator is
done only through the operator CLI.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.deps import DB, RateLimited, User
from app.audit.chain import append_event
from app.auth.identity import CurrentUser, get_verifier, record_denied, require_roles
from app.config import get_settings
from app.errors import conflict, forbidden, not_found, unprocessable
from app.models import Organization, PolicyVersion
from app.models import User as UserRow
from app.policy.catalog import AGGREGATE_GROUPINGS, AGGREGATE_METRICS, FIELD_CATALOG
from app.policy.document import PolicyDocument
from app.schemas import (
    AdminOrgOut,
    AdminOrgUpdateIn,
    AdminUserCreateIn,
    AdminUserOut,
    AdminUserUpdateIn,
    FieldOut,
    OrgRef,
    PolicyChangeIn,
    PolicyOut,
    PolicyVersionOut,
)
from app.services.policy_store import version_key

router = APIRouter(prefix="/api")
Admin = Annotated[CurrentUser, Depends(require_roles("platform_admin"))]


def _policy_out(db: DB) -> PolicyOut:  # type: ignore[valid-type]
    active = db.execute(select(PolicyVersion).where(PolicyVersion.is_active.is_(True))).scalar_one()
    history = db.execute(
        select(PolicyVersion, UserRow.display_name)
        .outerjoin(UserRow, UserRow.id == PolicyVersion.created_by_user_id)
        .order_by(PolicyVersion.created_at.desc())
    ).all()
    doc = PolicyDocument.model_validate(active.document)
    k_policy = max((p.aggregate.min_group_size for p in doc.purposes.values() if p.aggregate), default=5)
    return PolicyOut(
        version=active.version,
        activated_at=active.created_at,
        change_note=active.change_note,
        document=active.document,
        fields=[FieldOut(id=f.id, label=f.label, sensitivity=f.sensitivity, description=f.description)
                for f in FIELD_CATALOG.values()],
        metrics=AGGREGATE_METRICS,
        groupings=AGGREGATE_GROUPINGS,
        effective_min_group_size=max(k_policy, get_settings().aggregate_min_group_size),
        history=[PolicyVersionOut(version=v.version, is_active=v.is_active, change_note=v.change_note,
                                  created_at=v.created_at, created_by=name) for v, name in history],
    )


@router.get("/policy", response_model=PolicyOut, tags=["policy"])
def get_policy(user: User, db: DB) -> PolicyOut:
    return _policy_out(db)


@router.get("/policy/fields", response_model=list[FieldOut], tags=["policy"])
def get_fields(user: User) -> list[FieldOut]:
    return [FieldOut(id=f.id, label=f.label, sensitivity=f.sensitivity, description=f.description)
            for f in FIELD_CATALOG.values()]


@router.post("/admin/policy", response_model=PolicyOut, tags=["admin"], dependencies=[RateLimited])
def change_policy(body: PolicyChangeIn, user: Admin, db: DB) -> PolicyOut:
    try:
        doc = PolicyDocument.model_validate(body.document)
    except ValidationError as exc:
        msgs = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()][:10]
        raise unprocessable("The policy document is invalid.", code="POLICY_INVALID", extra={"errors": msgs}) from exc
    current = db.execute(select(PolicyVersion).where(PolicyVersion.is_active.is_(True)).with_for_update()
                         ).scalar_one()
    if version_key(doc.version) <= version_key(current.version):
        raise conflict(f"New version must be greater than the active version {current.version}.",
                       code="POLICY_VERSION_NOT_INCREASED")
    exists = db.execute(select(PolicyVersion.id).where(PolicyVersion.version == doc.version)).first()
    if exists:
        raise conflict("That version already exists.", code="POLICY_VERSION_EXISTS")
    old = PolicyDocument.model_validate(current.document)
    changes = _diff(old, doc)
    current.is_active = False
    db.flush()
    db.add(PolicyVersion(version=doc.version, document=doc.model_dump(mode="json"), is_active=True,
                         change_note=body.change_note, created_by_user_id=user.id))
    append_event(db, actor=user.actor, action="POLICY_CHANGED", outcome="success", resource_type="policy",
                 resource_id=doc.version, decision="activated",
                 details={"previous_version": current.version, "new_version": doc.version,
                          "change_note": body.change_note[:300], "changes": changes[:50]})
    db.commit()
    return _policy_out(db)


def _diff(old: PolicyDocument, new: PolicyDocument) -> list[str]:
    out: list[str] = []
    for k in sorted(set(old.purposes) | set(new.purposes)):
        a, b = old.purposes.get(k), new.purposes.get(k)
        if a is None:
            out.append(f"added purpose {k}")
        elif b is None:
            out.append(f"removed purpose {k}")
        else:
            for bucket in ("allowed", "review_required", "hard_denied"):
                sa, sb = set(getattr(a.fields, bucket)), set(getattr(b.fields, bucket))
                for f in sorted(sb - sa):
                    out.append(f"{k}.{bucket} +{f}")
                for f in sorted(sa - sb):
                    out.append(f"{k}.{bucket} -{f}")
            if a.model_dump(exclude={"fields"}) != b.model_dump(exclude={"fields"}):
                out.append(f"{k}: other settings changed")
    return out


# ------------------------------------------------------------------ users
def _user_out(u: UserRow) -> AdminUserOut:
    o = u.organization
    return AdminUserOut(id=u.id, username=u.username, display_name=u.display_name, role=u.role,
                        organization=OrgRef(id=o.id, name=o.name, type=o.org_type, slug=o.slug),
                        is_active=u.is_active, identity_linked=u.external_subject is not None,
                        last_seen_at=u.last_seen_at, created_at=u.created_at)


ROLE_ORG_TYPE = {"hospital_staff": "hospital", "insurance_officer": "insurer", "government_officer": "government",
                 "privacy_reviewer": "hospital"}


@router.get("/admin/users", response_model=list[AdminUserOut], tags=["admin"])
def list_users(user: Admin, db: DB) -> list[AdminUserOut]:
    rows = db.execute(select(UserRow).order_by(UserRow.username)).scalars().all()
    return [_user_out(u) for u in rows]


@router.post("/admin/users", response_model=AdminUserOut, status_code=201, tags=["admin"],
             dependencies=[RateLimited])
def create_user(body: AdminUserCreateIn, user: Admin, db: DB) -> AdminUserOut:
    org = db.execute(select(Organization).where(Organization.slug == body.organization_slug)).scalar_one_or_none()
    if org is None:
        raise unprocessable("Unknown organisation.", code="ORG_UNKNOWN")
    if ROLE_ORG_TYPE[body.role] != org.org_type:
        raise unprocessable(f"Role {body.role} is not valid for a {org.org_type} organisation.",
                            code="ROLE_ORG_MISMATCH")
    if db.execute(select(UserRow.id).where(UserRow.username == body.username)).first():
        raise conflict("Username already exists.", code="USERNAME_EXISTS")
    adapter = get_verifier().adapter
    issuer = adapter.issuer if (body.external_subject and adapter is not None) else None
    if body.external_subject and issuer is None:
        raise unprocessable("TideCloak is not configured, so an identity cannot be linked yet.",
                            code="AUTH_NOT_CONFIGURED")
    new = UserRow(username=body.username, display_name=body.display_name, organization_id=org.id, role=body.role,
                  external_subject=body.external_subject or None, external_issuer=issuer)
    db.add(new)
    db.flush()
    append_event(db, actor=user.actor, action="USER_CREATED", outcome="success", resource_type="user",
                 resource_id=str(new.id), related_org_ids=[org.id],
                 details={"username": new.username, "role": new.role, "organization": org.slug,
                          "identity_linked": new.external_subject is not None})
    db.commit()
    db.refresh(new)
    return _user_out(new)


@router.patch("/admin/users/{user_id}", response_model=AdminUserOut, tags=["admin"], dependencies=[RateLimited])
def update_user(user_id: uuid.UUID, body: AdminUserUpdateIn, user: Admin, db: DB) -> AdminUserOut:
    target = db.get(UserRow, user_id)
    if target is None:
        raise not_found("User not found.")
    if target.id == user.id:
        record_denied(db, user, "SELF_ADMINISTRATION", str(user_id), resource_type="user")
        raise forbidden("You cannot change your own account.", code="SEPARATION_OF_DUTIES")
    if target.role == "platform_admin":
        record_denied(db, user, "ADMIN_CHANGE_REQUIRES_OPERATOR", str(user_id), resource_type="user")
        raise forbidden("Platform administrator accounts are managed by the operator CLI.",
                        code="OPERATOR_ONLY")
    changes: dict[str, object] = {}
    if body.is_active is not None and body.is_active != target.is_active:
        target.is_active = body.is_active
        changes["is_active"] = body.is_active
    if body.display_name and body.display_name != target.display_name:
        target.display_name = body.display_name
        changes["display_name_changed"] = True
    if body.unlink_identity:
        target.external_subject = None
        target.external_issuer = None
        changes["identity_linked"] = False
    elif body.external_subject:
        adapter = get_verifier().adapter
        if adapter is None:
            raise unprocessable("TideCloak is not configured.", code="AUTH_NOT_CONFIGURED")
        clash = db.execute(select(UserRow.id).where(UserRow.external_issuer == adapter.issuer,
                                                    UserRow.external_subject == body.external_subject,
                                                    UserRow.id != target.id)).first()
        if clash:
            raise conflict("That identity is already linked to another user.", code="IDENTITY_IN_USE")
        target.external_subject = body.external_subject
        target.external_issuer = adapter.issuer
        changes["identity_linked"] = True
    if changes:
        append_event(db, actor=user.actor, action="USER_UPDATED", outcome="success", resource_type="user",
                     resource_id=str(target.id), related_org_ids=[target.organization_id], details=changes)
    db.commit()
    db.refresh(target)
    return _user_out(target)


# ------------------------------------------------------------------ organisations
@router.get("/admin/organizations", response_model=list[AdminOrgOut], tags=["admin"])
def list_orgs(user: Admin, db: DB) -> list[AdminOrgOut]:
    counts: dict[uuid.UUID, int] = {
        o: int(n) for o, n in db.execute(select(UserRow.organization_id, func.count())
                                         .group_by(UserRow.organization_id)).all()}
    rows = db.execute(select(Organization).order_by(Organization.org_type, Organization.name)).scalars().all()
    return [AdminOrgOut(id=o.id, slug=o.slug, name=o.name, type=o.org_type, is_active=o.is_active,
                        user_count=int(counts.get(o.id, 0))) for o in rows]


@router.patch("/admin/organizations/{org_id}", response_model=AdminOrgOut, tags=["admin"],
              dependencies=[RateLimited])
def update_org(org_id: uuid.UUID, body: AdminOrgUpdateIn, user: Admin, db: DB) -> AdminOrgOut:
    org = db.get(Organization, org_id)
    if org is None:
        raise not_found("Organisation not found.")
    if org.id == user.org_id:
        raise forbidden("You cannot deactivate your own organisation.", code="SEPARATION_OF_DUTIES")
    if org.is_active != body.is_active:
        org.is_active = body.is_active
        append_event(db, actor=user.actor, action="ORGANIZATION_UPDATED", outcome="success",
                     resource_type="organization", resource_id=org.slug, related_org_ids=[org.id],
                     details={"is_active": body.is_active})
    db.commit()
    n = db.execute(select(func.count()).select_from(UserRow).where(UserRow.organization_id == org.id)).scalar_one()
    return AdminOrgOut(id=org.id, slug=org.slug, name=org.name, type=org.org_type, is_active=org.is_active,
                       user_count=int(n))
