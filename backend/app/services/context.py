"""Builds policy evaluation contexts from verified identity and database state.

Every fact the engine sees is resolved here, server-side, scoped to the
requester's organisation. Nothing the client sends is trusted beyond the
identifiers it uses to point at a claim, referral or mandate.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.identity import CurrentUser
from app.config import get_settings
from app.models import (
    DataRequest,
    Encounter,
    InsuranceClaim,
    Organization,
    PatientConsent,
    Referral,
    ReportingMandate,
    User,
)
from app.policy.document import PolicyDocument
from app.policy.engine import (
    AggregateQuery,
    AggregateScope,
    ConsentFacts,
    EvaluationContext,
    PatientScope,
    Principal,
)


@dataclass
class ResolvedTarget:
    """What a request points at, already scoped to the requester's organisation."""

    patient_id: object | None = None
    custodian_org_id: object | None = None
    encounter_id: object | None = None
    claim_id: object | None = None
    authorization_reference: str | None = None
    aggregate: AggregateQuery | None = None


def principal_for(db: Session, user_id: object) -> Principal:
    row = db.execute(
        select(User, Organization).join(Organization, Organization.id == User.organization_id).where(User.id == user_id)
    ).one()
    user, org = row
    return Principal(
        user_id=str(user.id),
        role=user.role,
        org_id=str(org.id),
        org_type=org.org_type,
        user_active=user.is_active,
        org_active=org.is_active,
    )


def principal_from_current(u: CurrentUser) -> Principal:
    return Principal(str(u.id), u.role, str(u.org_id), u.org_type, True, True)


def consent_facts(db: Session, patient_id: object, purpose: str, requester_org_id: object,
                  today: dt.date | None = None) -> ConsentFacts:
    today = today or dt.date.today()
    rows = db.execute(
        select(PatientConsent)
        .where(
            PatientConsent.patient_id == patient_id,
            PatientConsent.purpose == purpose,
            or_(PatientConsent.grantee_org_id.is_(None), PatientConsent.grantee_org_id == requester_org_id),
        )
        .order_by(PatientConsent.updated_at.desc())
    ).scalars().all()
    if not rows:
        return ConsentFacts(status="none")
    # A refusal or withdrawal anywhere in scope wins over a grant.
    for r in rows:
        if r.status in ("refused", "withdrawn"):
            return ConsentFacts(status=r.status, reference=r.consent_reference)  # type: ignore[arg-type]
    granted = rows[0]
    expired = granted.valid_until is not None and granted.valid_until < today
    return ConsentFacts(status="granted", reference=granted.consent_reference, expired=expired,
                        excluded_fields=tuple(granted.excluded_fields or ()))


def resolve_claim(db: Session, claim_reference: str, requester_org_id: object) -> InsuranceClaim | None:
    return db.execute(
        select(InsuranceClaim).where(
            InsuranceClaim.claim_reference == claim_reference,
            InsuranceClaim.insurer_org_id == requester_org_id,
        )
    ).scalar_one_or_none()


def resolve_referral(db: Session, referral_reference: str, requester_org_id: object) -> Referral | None:
    return db.execute(
        select(Referral).where(
            Referral.referral_reference == referral_reference,
            Referral.to_org_id == requester_org_id,
        )
    ).scalar_one_or_none()


def resolve_mandate(db: Session, mandate_reference: str, requester_org_id: object) -> ReportingMandate | None:
    return db.execute(
        select(ReportingMandate).where(
            ReportingMandate.mandate_reference == mandate_reference,
            ReportingMandate.organization_id == requester_org_id,
        )
    ).scalar_one_or_none()


def mandate_valid(m: ReportingMandate | None, metric: str, today: dt.date | None = None) -> bool:
    today = today or dt.date.today()
    return bool(
        m is not None
        and m.is_active
        and (m.valid_until is None or m.valid_until >= today)
        and metric in (m.allowed_metrics or [])
    )


def build_context(
    db: Session,
    *,
    principal: Principal,
    purpose: str,
    requested_fields: list[str],
    policy: PolicyDocument,
    target: ResolvedTarget,
    review_approved: bool = False,
) -> tuple[EvaluationContext, ConsentFacts]:
    settings = get_settings()
    consent = ConsentFacts(status="none")
    if target.aggregate is not None:
        m = None
        if target.authorization_reference:
            m = resolve_mandate(db, target.authorization_reference, principal.org_id)
        ctx = EvaluationContext(
            principal=principal,
            purpose=purpose,
            requested_fields=tuple(requested_fields),
            policy=policy,
            aggregate=AggregateScope(query=target.aggregate, mandate_valid=mandate_valid(m, target.aggregate.metric)),
            min_group_size_floor=settings.aggregate_min_group_size,
        )
        return ctx, consent

    encounter = db.get(Encounter, target.encounter_id) if target.encounter_id else None
    claim_valid = False
    referral_valid = False
    if target.claim_id is not None:
        claim = db.get(InsuranceClaim, target.claim_id)
        claim_valid = bool(
            claim is not None
            and str(claim.insurer_org_id) == principal.org_id
            and claim.patient_id == target.patient_id
            and claim.status != "closed"
        )
    if target.authorization_reference and purpose_uses_referral(policy, purpose):
        ref = resolve_referral(db, target.authorization_reference, principal.org_id)
        referral_valid = bool(
            ref is not None and ref.status == "active" and ref.patient_id == target.patient_id
            and ref.from_org_id == target.custodian_org_id
        )
    consent = consent_facts(db, target.patient_id, purpose, principal.org_id)
    ctx = EvaluationContext(
        principal=principal,
        purpose=purpose,
        requested_fields=tuple(requested_fields),
        policy=policy,
        patient=PatientScope(
            patient_found=target.patient_id is not None and encounter is not None,
            custodian_org_id=str(target.custodian_org_id) if target.custodian_org_id else None,
            encounter_sensitive=bool(encounter and encounter.sensitive_category),
            claim_valid=claim_valid,
            referral_valid=referral_valid,
        ),
        consent=consent,
        review_approved=review_approved,
    )
    return ctx, consent


def purpose_uses_referral(policy: PolicyDocument, purpose: str) -> bool:
    rules = policy.purposes.get(purpose)
    return bool(rules and rules.authorization == "referral")


def target_from_request(req: DataRequest) -> ResolvedTarget:
    agg = None
    if req.aggregate_query is not None:
        q = req.aggregate_query
        agg = AggregateQuery(
            metric=q["metric"],
            group_by=tuple(q["group_by"]),
            period_start=dt.date.fromisoformat(q["period_start"]),
            period_end=dt.date.fromisoformat(q["period_end"]),
        )
    return ResolvedTarget(
        patient_id=req.patient_id,
        custodian_org_id=req.custodian_org_id,
        encounter_id=req.encounter_id,
        claim_id=req.claim_id,
        authorization_reference=req.authorization_reference,
        aggregate=agg,
    )
