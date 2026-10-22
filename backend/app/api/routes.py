"""REST API. Every route below /api (except health) authenticates and authorises."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import DB, PERMISSIONS, RateLimited, User
from app.audit.chain import append_event, verify_chain
from app.auth.identity import CurrentUser, record_denied, require_roles
from app.db import get_db
from app.errors import forbidden, not_found
from app.models import (
    AuditEvent,
    DataRequest,
    Encounter,
    InsuranceClaim,
    Organization,
    Patient,
    PatientConsent,
    Referral,
    ReportingMandate,
)
from app.schemas import (
    AuditPageOut,
    ClaimOut,
    ConsentOut,
    CreateRequestIn,
    CreateRequestOut,
    DashboardOut,
    EncounterOut,
    MandateOut,
    MeOut,
    OrgRef,
    PatientDetailOut,
    PatientSummaryOut,
    PreviewOut,
    ReferralOut,
    ReleaseOut,
    RequestDetailOut,
    RequestSummaryOut,
    ReviewDecisionIn,
    ReviewResultOut,
    VerifyOut,
)
from app.services import requests as svc
from app.services.policy_store import active_policy

router = APIRouter(prefix="/api")


def _money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


# ------------------------------------------------------------------ identity
@router.get("/me", response_model=MeOut, tags=["identity"])
def me(user: User) -> MeOut:
    return MeOut(
        id=user.id, username=user.username, display_name=user.display_name, role=user.role,
        organization=OrgRef(id=user.org_id, name=user.org_name, type=user.org_type, slug=user.org_slug),
        permissions=PERMISSIONS.get(user.role, []),
    )


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard", response_model=DashboardOut, tags=["dashboard"])
def dashboard(user: User, db: DB) -> DashboardOut:
    counts = svc.status_counts(db, user)
    queue = None
    if user.role == "privacy_reviewer":
        sub = svc.review_queue_query(user).subquery()
        queue = int(db.execute(select(func.count()).select_from(sub)).scalar_one())
    events = _audit_scope(select(AuditEvent), user).order_by(AuditEvent.seq.desc()).limit(8)
    return DashboardOut(
        scope_description=svc.scope_description(user),
        total_requests=sum(counts.values()),
        pending_review=counts.get("pending_review", 0),
        approved=counts.get("approved", 0),
        approved_with_restrictions=counts.get("approved_with_restrictions", 0),
        rejected=counts.get("rejected", 0),
        releases=svc.release_count(db, user),
        review_queue=queue,
        recent_events=svc.audit_out(db, list(db.execute(events).scalars().all())),
    )


# ------------------------------------------------------------------ requests
StatusFilter = Literal["approved", "approved_with_restrictions", "pending_review", "rejected"]


@router.get("/requests", response_model=list[RequestSummaryOut], tags=["requests"])
def list_requests(
    user: User,
    db: DB,
    status: StatusFilter | None = None,
    purpose: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z_]+$")] = None,
    q: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[RequestSummaryOut]:
    stmt = svc.visible_requests_query(user)
    if status:
        stmt = stmt.where(DataRequest.status == status)
    if purpose:
        stmt = stmt.where(DataRequest.purpose == purpose)
    if q:
        term = q.strip()
        conds: list[ColumnElement[bool]] = [DataRequest.authorization_reference.ilike(f"%{term}%")]
        digits = term.upper().removeprefix("CB-")
        if digits.isdigit():
            conds.append(DataRequest.request_number == int(digits))
        if user.role != "platform_admin":
            conds.append(DataRequest.patient.has(Patient.synthetic_ref.ilike(f"%{term}%")))
            conds.append(DataRequest.patient.has(Patient.display_alias.ilike(f"%{term}%")))
        stmt = stmt.where(or_(*conds))
    rows = db.execute(stmt.order_by(DataRequest.created_at.desc()).limit(limit)).unique().scalars().all()
    policy = active_policy(db)
    return [svc.serialize_summary(db, user, r, policy) for r in rows]


@router.post("/requests/preview", response_model=PreviewOut, tags=["requests"], dependencies=[RateLimited])
def preview_request(body: CreateRequestIn, user: User, db: DB) -> PreviewOut:
    result, consent_status, ref = svc.preview(db, user, body)
    return PreviewOut(decision=svc.decision_out(result), consent_status=consent_status, authorization_reference=ref)


@router.post("/requests", response_model=CreateRequestOut, status_code=201, tags=["requests"],
             dependencies=[RateLimited])
def create_request(body: CreateRequestIn, user: User, db: DB) -> CreateRequestOut:
    req = svc.create(db, user, body)
    return CreateRequestOut(request=svc.detail(db, user, req))


@router.get("/requests/{request_id}", response_model=RequestDetailOut, tags=["requests"])
def get_request(request_id: uuid.UUID, user: User, db: DB) -> RequestDetailOut:
    req = svc.get_visible_request(db, user, request_id)
    return svc.detail(db, user, req)


@router.post("/requests/{request_id}/release", response_model=ReleaseOut, tags=["requests"],
             dependencies=[RateLimited])
def release_request(request_id: uuid.UUID, user: User, db: DB) -> ReleaseOut:
    return svc.release(db, user, request_id)


# ------------------------------------------------------------------ reviews
@router.get("/reviews/queue", response_model=list[RequestSummaryOut], tags=["reviews"])
def review_queue(user: Annotated[CurrentUser, Depends(require_roles("privacy_reviewer"))], db: DB
                 ) -> list[RequestSummaryOut]:
    rows = db.execute(svc.review_queue_query(user).order_by(DataRequest.created_at)).unique().scalars().all()
    policy = active_policy(db)
    return [svc.serialize_summary(db, user, r, policy) for r in rows]


@router.get("/reviews/history", response_model=list[RequestSummaryOut], tags=["reviews"])
def review_history(user: Annotated[CurrentUser, Depends(require_roles("privacy_reviewer"))], db: DB
                   ) -> list[RequestSummaryOut]:
    from app.models import ReviewDecision

    rows = db.execute(
        select(DataRequest).join(ReviewDecision, ReviewDecision.request_id == DataRequest.id)
        .where(DataRequest.custodian_org_id == user.org_id)
        .order_by(ReviewDecision.created_at.desc()).limit(100)
    ).unique().scalars().all()
    policy = active_policy(db)
    return [svc.serialize_summary(db, user, r, policy) for r in rows]


@router.post("/reviews/{request_id}", response_model=ReviewResultOut, tags=["reviews"], dependencies=[RateLimited])
def decide_review(request_id: uuid.UUID, body: ReviewDecisionIn, user: User, db: DB) -> ReviewResultOut:
    req = svc.decide_review(db, user, request_id, body.decision, body.justification)
    return ReviewResultOut(request=svc.detail(db, user, req))


# ------------------------------------------------------------------ patient data (restricted)
@router.get("/patients", response_model=list[PatientSummaryOut], tags=["patients"])
def list_patients(user: Annotated[CurrentUser, Depends(require_roles("hospital_staff"))], db: DB
                  ) -> list[PatientSummaryOut]:
    # Only patients whose records this hospital holds. There is no global directory.
    counts = (select(Encounter.patient_id, func.count().label("n"))
              .where(Encounter.custodian_org_id == user.org_id).group_by(Encounter.patient_id).subquery())
    rows = db.execute(
        select(Patient, func.coalesce(counts.c.n, 0)).outerjoin(counts, counts.c.patient_id == Patient.id)
        .where(Patient.custodian_org_id == user.org_id).order_by(Patient.synthetic_ref)
    ).all()
    return [PatientSummaryOut(id=p.id, synthetic_ref=p.synthetic_ref, display_alias=p.display_alias,
                              birth_year=p.birth_year, sex=p.sex, region=p.region, encounter_count=int(n))
            for p, n in rows]


@router.get("/patients/{patient_id}", response_model=PatientDetailOut, tags=["patients"])
def get_patient(patient_id: uuid.UUID, user: User, db: DB) -> PatientDetailOut:
    patient = db.get(Patient, patient_id)
    if user.role != "hospital_staff" or patient is None or patient.custodian_org_id != user.org_id:
        if patient is None and user.role == "hospital_staff":
            raise not_found("Patient not found.")
        record_denied(db, user, "PATIENT_OUT_OF_SCOPE", str(patient_id), resource_type="patient",
                      related=[patient.custodian_org_id] if patient else None)
        raise forbidden("You do not have access to this patient record.")
    encounters = db.execute(select(Encounter).where(Encounter.patient_id == patient.id,
                                                    Encounter.custodian_org_id == user.org_id)
                            .order_by(Encounter.treatment_date.desc())).scalars().all()
    consents = db.execute(select(PatientConsent, Organization.name)
                          .outerjoin(Organization, Organization.id == PatientConsent.grantee_org_id)
                          .where(PatientConsent.patient_id == patient.id)).all()
    custodian = db.get(Organization, patient.custodian_org_id)
    append_event(db, actor=user.actor, action="PATIENT_RECORD_VIEWED", outcome="success", resource_type="patient",
                 resource_id=patient.synthetic_ref, related_org_ids=[patient.custodian_org_id],
                 details={"sensitive_notes_withheld": True})
    db.commit()
    return PatientDetailOut(
        id=patient.id, synthetic_ref=patient.synthetic_ref, display_alias=patient.display_alias,
        birth_year=patient.birth_year, sex=patient.sex, region=patient.region, encounter_count=len(encounters),
        custodian_org=custodian.name if custodian else "",
        encounters=[EncounterOut(
            encounter_reference=e.encounter_reference, treatment_date=e.treatment_date,
            diagnosis_code=e.diagnosis_code, diagnosis_description=e.diagnosis_description,
            diagnosis_category=e.diagnosis_category, procedure_code=e.procedure_code, medication=e.medication,
            clinician_notes=e.clinician_notes, has_sensitive_notes=bool(e.sensitive_notes),
            sensitive_category=e.sensitive_category, billing_amount=_money(e.billing_amount_cents))
            for e in encounters],
        consents=[ConsentOut(consent_reference=c.consent_reference, purpose=c.purpose, grantee_org=name,
                             status=c.status, excluded_fields=list(c.excluded_fields or []),
                             valid_until=c.valid_until) for c, name in consents],
    )


@router.get("/claims", response_model=list[ClaimOut], tags=["references"])
def list_claims(user: Annotated[CurrentUser, Depends(require_roles("insurance_officer"))], db: DB) -> list[ClaimOut]:
    rows = db.execute(
        select(InsuranceClaim, Patient, Organization)
        .join(Patient, Patient.id == InsuranceClaim.patient_id)
        .join(Encounter, Encounter.id == InsuranceClaim.encounter_id)
        .join(Organization, Organization.id == Encounter.custodian_org_id)
        .where(InsuranceClaim.insurer_org_id == user.org_id).order_by(InsuranceClaim.claim_reference)
    ).all()
    return [ClaimOut(claim_reference=c.claim_reference, patient_reference=p.synthetic_ref,
                     patient_alias=p.display_alias, policy_number=c.policy_number,
                     amount_claimed=_money(c.amount_claimed_cents), status=c.status, lodged_at=c.created_at,
                     custodian_org=o.name) for c, p, o in rows]


@router.get("/referrals", response_model=list[ReferralOut], tags=["references"])
def list_referrals(user: Annotated[CurrentUser, Depends(require_roles("hospital_staff"))], db: DB
                   ) -> list[ReferralOut]:
    rows = db.execute(
        select(Referral, Patient, Organization)
        .join(Patient, Patient.id == Referral.patient_id)
        .join(Organization, Organization.id == Referral.from_org_id)
        .where(Referral.to_org_id == user.org_id).order_by(Referral.referral_reference)
    ).all()
    return [ReferralOut(referral_reference=r.referral_reference, patient_reference=p.synthetic_ref,
                        patient_alias=p.display_alias, from_org=o.name, status=r.status, created_at=r.created_at)
            for r, p, o in rows]


@router.get("/mandates", response_model=list[MandateOut], tags=["references"])
def list_mandates(user: Annotated[CurrentUser, Depends(require_roles("government_officer"))], db: DB
                  ) -> list[MandateOut]:
    rows = db.execute(select(ReportingMandate).where(ReportingMandate.organization_id == user.org_id)
                      .order_by(ReportingMandate.mandate_reference)).scalars().all()
    return [MandateOut(mandate_reference=m.mandate_reference, description=m.description,
                       allowed_metrics=list(m.allowed_metrics), valid_until=m.valid_until, is_active=m.is_active)
            for m in rows]


@router.get("/releases", response_model=list[RequestSummaryOut], tags=["requests"])
def my_releasable(user: User, db: DB) -> list[RequestSummaryOut]:
    """Requests whose approved data the caller may open (re-authorised on each open)."""
    rows = db.execute(
        select(DataRequest).where(DataRequest.requester_user_id == user.id,
                                  DataRequest.status.in_(["approved", "approved_with_restrictions"]))
        .order_by(DataRequest.created_at.desc())
    ).unique().scalars().all()
    policy = active_policy(db)
    return [svc.serialize_summary(db, user, r, policy) for r in rows]


# ------------------------------------------------------------------ audit
def _audit_scope(stmt, user: CurrentUser):  # type: ignore[no-untyped-def]
    if user.role == "platform_admin":
        return stmt
    return stmt.where(AuditEvent.related_org_ids.contains([user.org_id]))


def audit_scope_description(user: CurrentUser) -> str:
    if user.role == "platform_admin":
        return "All audit events (events contain identifiers and field names, never patient values)"
    return f"Events involving {user.org_name}"


@router.get("/audit", response_model=AuditPageOut, tags=["audit"])
def list_audit(
    user: User,
    db: DB,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    action: Annotated[str | None, Query(max_length=64, pattern=r"^[A-Z_]+$")] = None,
    request_id: uuid.UUID | None = None,
    outcome: Literal["success", "denied", "failure", "info"] | None = None,
) -> AuditPageOut:
    stmt = _audit_scope(select(AuditEvent), user)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if request_id:
        stmt = stmt.where(AuditEvent.request_id == request_id)
    if outcome:
        stmt = stmt.where(AuditEvent.outcome == outcome)
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    rows = db.execute(stmt.order_by(AuditEvent.seq.desc()).limit(limit).offset(offset)).scalars().all()
    return AuditPageOut(items=svc.audit_out(db, list(rows)), total=total, limit=limit, offset=offset,
                        scope_description=audit_scope_description(user))


@router.post("/audit/verify", response_model=VerifyOut, tags=["audit"], dependencies=[RateLimited])
def verify_audit(user: Annotated[CurrentUser, Depends(require_roles("platform_admin", "privacy_reviewer"))],
                 db: Annotated[Session, Depends(get_db)]) -> VerifyOut:
    res = verify_chain(db)
    db.rollback()
    append_event(db, actor=user.actor, action="AUDIT_VERIFIED", outcome="success" if res.valid else "failure",
                 resource_type="audit_chain", decision="valid" if res.valid else "invalid",
                 policy_codes=[] if res.valid else ["AUDIT_CHAIN_MISMATCH"],
                 details={"checked_events": res.checked, "head_seq": res.head_seq,
                          "first_invalid_seq": res.first_invalid_seq})
    db.commit()
    return VerifyOut(
        valid=res.valid, checked_events=res.checked, head_seq=res.head_seq, head_hash=res.head_hash,
        first_invalid_seq=res.first_invalid_seq, problem=res.problem, verified_at=res.verified_at,
        notice=("The hash chain is tamper-evident, not tamper-proof. Record the head hash outside this database "
                "to detect truncation or wholesale rewriting."),
    )
