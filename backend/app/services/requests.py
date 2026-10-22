"""Data-sharing request lifecycle: create, view, release, review."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Select, false, func, or_, select
from sqlalchemy.orm import Session

from app.audit.chain import append_event, compute_hash
from app.auth.identity import CurrentUser, record_denied
from app.errors import AppError, conflict, forbidden, not_found, unprocessable
from app.models import (
    AuditEvent,
    DataRelease,
    DataRequest,
    Encounter,
    InsuranceClaim,
    Organization,
    Patient,
    ReviewDecision,
)
from app.policy.document import PolicyDocument
from app.policy.engine import AggregateQuery, PolicyResult, evaluate
from app.schemas import (
    AuditEventOut,
    CreateRequestIn,
    OrgRef,
    PolicyDecisionOut,
    ReleaseOut,
    ReleaseSummaryOut,
    RequestDetailOut,
    RequestSummaryOut,
    ReviewOut,
)
from app.services import aggregates
from app.services.context import (
    ResolvedTarget,
    build_context,
    principal_for,
    principal_from_current,
    resolve_claim,
    resolve_mandate,
    resolve_referral,
    target_from_request,
)
from app.services.policy_store import active_policy

REQUESTER_ROLES = ("hospital_staff", "insurance_officer", "government_officer")


# ------------------------------------------------------------------ helpers
def decision_out(r: PolicyResult) -> PolicyDecisionOut:
    return PolicyDecisionOut(
        status=r.status,
        approved_fields=r.approved_fields,
        denied_fields=r.denied_fields,
        review_fields=r.review_fields,
        field_decisions=r.field_decisions,
        review_required=r.review_required,
        review_reasons=r.review_reasons,
        policy_codes=r.policy_codes,
        explanation=r.explanation,
        policy_version=r.policy_version,
        evaluated_at=r.evaluated_at,
    )


def _apply_result(req: DataRequest, r: PolicyResult) -> None:
    req.status = r.status
    req.approved_fields = r.approved_fields
    req.denied_fields = r.denied_fields
    req.review_fields = r.review_fields
    req.field_decisions = r.field_decisions
    req.review_required = r.review_required
    req.review_reasons = r.review_reasons
    req.policy_codes = r.policy_codes
    req.decision_explanation = r.explanation
    req.policy_version = r.policy_version
    req.evaluated_at = r.evaluated_at


def _related(req: DataRequest) -> list[uuid.UUID]:
    out = [req.requester_org_id]
    if req.custodian_org_id:
        out.append(req.custodian_org_id)
    return out


def _resolve_target(db: Session, user: CurrentUser, body: CreateRequestIn, policy: PolicyDocument) -> ResolvedTarget:
    """Resolve the claim / referral / mandate, scoped to the caller's organisation.

    A reference that does not exist and one that belongs to another organisation
    are indistinguishable to the caller (both 403), and nothing is persisted.
    """
    rules = policy.purposes.get(body.purpose)
    if rules is None:
        raise unprocessable("The declared purpose is not defined in the active policy.", code="PURPOSE_UNKNOWN")
    if user.role not in rules.allowed_roles:
        record_denied(db, user, "ROLE_NOT_PERMITTED_FOR_PURPOSE", f"purpose:{body.purpose}",
                      resource_type="purpose")
        raise forbidden("Your role may not make requests for this purpose.", code="PURPOSE_NOT_PERMITTED")

    if rules.authorization == "insurance_claim":
        if not body.claim_reference:
            raise unprocessable("This purpose requires a claim reference.", code="CLAIM_REFERENCE_REQUIRED")
        claim = resolve_claim(db, body.claim_reference, user.org_id)
        if claim is None:
            record_denied(db, user, "AUTHZ_CLAIM_INVALID", "claim", resource_type="claim")
            raise forbidden("No claim with that reference is lodged with your organisation.",
                            code="AUTHORIZATION_REFERENCE_INVALID")
        enc = db.get(Encounter, claim.encounter_id)
        assert enc is not None
        return ResolvedTarget(patient_id=claim.patient_id, custodian_org_id=enc.custodian_org_id,
                              encounter_id=enc.id, claim_id=claim.id,
                              authorization_reference=claim.claim_reference)
    if rules.authorization == "referral":
        if not body.referral_reference:
            raise unprocessable("This purpose requires a referral reference.", code="REFERRAL_REFERENCE_REQUIRED")
        ref = resolve_referral(db, body.referral_reference, user.org_id)
        if ref is None:
            record_denied(db, user, "AUTHZ_REFERRAL_INVALID", "referral", resource_type="referral")
            raise forbidden("No referral with that reference is addressed to your organisation.",
                            code="AUTHORIZATION_REFERENCE_INVALID")
        return ResolvedTarget(patient_id=ref.patient_id, custodian_org_id=ref.from_org_id,
                              encounter_id=ref.encounter_id, authorization_reference=ref.referral_reference)
    # reporting mandate
    if body.aggregate is None or not body.mandate_reference:
        raise unprocessable("This purpose requires an aggregate query and a mandate reference.",
                            code="AGGREGATE_REQUIRED")
    mandate = resolve_mandate(db, body.mandate_reference, user.org_id)
    if mandate is None:
        record_denied(db, user, "AUTHZ_MANDATE_INVALID", "mandate", resource_type="mandate")
        raise forbidden("No reporting mandate with that reference belongs to your organisation.",
                        code="AUTHORIZATION_REFERENCE_INVALID")
    a = body.aggregate
    return ResolvedTarget(
        authorization_reference=mandate.mandate_reference,
        aggregate=AggregateQuery(metric=a.metric, group_by=tuple(a.group_by),
                                 period_start=a.period_start, period_end=a.period_end),
    )


def _scope_check_shape(body: CreateRequestIn, policy: PolicyDocument) -> None:
    rules = policy.purposes.get(body.purpose)
    if rules is None:
        return
    if rules.scope == "aggregate" and body.aggregate is None:
        raise unprocessable("This purpose only supports aggregate queries.", code="SCOPE_MISMATCH")
    if rules.scope == "patient" and body.aggregate is not None:
        raise unprocessable("This purpose does not support aggregate queries.", code="SCOPE_MISMATCH")


# ------------------------------------------------------------------ create / preview
def preview(db: Session, user: CurrentUser, body: CreateRequestIn) -> tuple[PolicyResult, str, str]:
    policy = active_policy(db)
    _scope_check_shape(body, policy)
    target = _resolve_target(db, user, body, policy)
    ctx, consent = build_context(db, principal=principal_from_current(user), purpose=body.purpose,
                                 requested_fields=body.requested_fields, policy=policy, target=target)
    result = evaluate(ctx)
    append_event(db, actor=user.actor, action="POLICY_PREVIEWED", outcome="info", resource_type="policy",
                 related_org_ids=[uuid.UUID(str(target.custodian_org_id))] if target.custodian_org_id else None,
                 decision=result.status, policy_codes=result.policy_codes,
                 details={"purpose": body.purpose, "requested_fields": body.requested_fields,
                          "policy_version": result.policy_version})
    db.commit()
    return result, consent.status, target.authorization_reference or ""


def create(db: Session, user: CurrentUser, body: CreateRequestIn) -> DataRequest:
    if user.role not in REQUESTER_ROLES:
        record_denied(db, user, "ROLE_CANNOT_REQUEST", "requests:create")
        raise forbidden("Your role cannot create data-sharing requests.")
    policy = active_policy(db)
    _scope_check_shape(body, policy)
    target = _resolve_target(db, user, body, policy)
    ctx, consent = build_context(db, principal=principal_from_current(user), purpose=body.purpose,
                                 requested_fields=body.requested_fields, policy=policy, target=target)
    result = evaluate(ctx)

    req = DataRequest(
        requester_user_id=user.id,
        requester_org_id=user.org_id,
        custodian_org_id=target.custodian_org_id,
        scope_type="aggregate" if target.aggregate else "patient",
        patient_id=target.patient_id,
        encounter_id=target.encounter_id,
        claim_id=target.claim_id,
        aggregate_query=(
            {
                "metric": target.aggregate.metric,
                "group_by": list(target.aggregate.group_by),
                "period_start": target.aggregate.period_start.isoformat(),
                "period_end": target.aggregate.period_end.isoformat(),
            }
            if target.aggregate
            else None
        ),
        purpose=body.purpose,
        requested_fields=body.requested_fields,
        authorization_reference=target.authorization_reference,
        consent_reference=consent.reference,
        justification=body.justification,
        status="rejected",
        decision_explanation="",
        policy_version=result.policy_version,
        evaluated_at=result.evaluated_at,
    )
    _apply_result(req, result)
    db.add(req)
    db.flush()
    related = _related(req)
    append_event(db, actor=user.actor, action="REQUEST_SUBMITTED", outcome="success", resource_type="data_request",
                 resource_id=str(req.id), request_id=req.id, related_org_ids=related,
                 details={"request_number": req.request_number, "purpose": req.purpose, "scope": req.scope_type,
                          "requested_fields": req.requested_fields,
                          "authorization_reference": req.authorization_reference,
                          "aggregate_query": req.aggregate_query})
    append_event(db, actor=user.actor, action="POLICY_EVALUATED", outcome="denied" if result.status == "rejected"
                 else "success", resource_type="data_request", resource_id=str(req.id), request_id=req.id,
                 related_org_ids=related, decision=result.status, policy_codes=result.policy_codes,
                 details={"approved_fields": result.approved_fields, "denied_fields": result.denied_fields,
                          "review_fields": result.review_fields, "policy_version": result.policy_version,
                          "consent_status": consent.status, "stage": "submission"})
    db.commit()
    return req


# ------------------------------------------------------------------ visibility
def visible_requests_query(user: CurrentUser) -> Select[tuple[DataRequest]]:
    q = select(DataRequest)
    if user.role in ("insurance_officer", "government_officer"):
        return q.where(DataRequest.requester_org_id == user.org_id)
    if user.role == "hospital_staff":
        return q.where(or_(DataRequest.requester_org_id == user.org_id, DataRequest.custodian_org_id == user.org_id))
    if user.role == "privacy_reviewer":
        return q.where(DataRequest.custodian_org_id == user.org_id)
    if user.role == "platform_admin":
        return q  # metadata only; patient identity redacted at serialisation
    return q.where(false())


def scope_description(user: CurrentUser) -> str:
    return {
        "insurance_officer": f"Requests made by {user.org_name}",
        "government_officer": f"Requests made by {user.org_name}",
        "hospital_staff": f"Requests made by {user.org_name} or concerning records it holds",
        "privacy_reviewer": f"Requests concerning records held by {user.org_name}",
        "platform_admin": "All requests (operational metadata only; patient identities redacted)",
    }.get(user.role, "No requests")


def get_visible_request(db: Session, user: CurrentUser, request_id: uuid.UUID) -> DataRequest:
    req = db.get(DataRequest, request_id)
    if req is None:
        raise not_found("Request not found.")
    visible = db.execute(visible_requests_query(user).where(DataRequest.id == request_id)).scalar_one_or_none()
    if visible is None:
        record_denied(db, user, "REQUEST_OUT_OF_SCOPE", str(request_id), resource_type="data_request")
        raise forbidden("You do not have access to this request.")
    return req


# ------------------------------------------------------------------ serialisation
def _org_ref(o: Organization | None) -> OrgRef | None:
    return OrgRef(id=o.id, name=o.name, type=o.org_type, slug=o.slug) if o else None


def summary(db: Session, user: CurrentUser, req: DataRequest, policy: PolicyDocument | None = None) -> dict[str, Any]:
    policy = policy or active_policy(db)
    label = policy.purposes[req.purpose].label if req.purpose in policy.purposes else req.purpose
    released = db.execute(select(func.count()).select_from(DataRelease).where(
        DataRelease.request_id == req.id)).scalar_one() > 0
    redact = user.role == "platform_admin"
    return {
        "id": req.id,
        "request_number": req.request_number,
        "purpose": req.purpose,
        "purpose_label": label,
        "scope_type": req.scope_type,
        "status": req.status,
        "requester_name": req.requester.display_name,
        "requester_org": _org_ref(req.requester_org),
        "custodian_org": _org_ref(req.custodian_org),
        "patient_reference": None if (redact or req.patient is None) else req.patient.synthetic_ref,
        "patient_alias": None if (redact or req.patient is None) else req.patient.display_alias,
        "authorization_reference": req.authorization_reference,
        "requested_fields": req.requested_fields,
        "approved_fields": req.approved_fields,
        "denied_fields": req.denied_fields,
        "review_fields": req.review_fields,
        "review_required": req.review_required,
        "policy_codes": req.policy_codes,
        "decision_explanation": req.decision_explanation,
        "policy_version": req.policy_version,
        "created_at": req.created_at,
        "evaluated_at": req.evaluated_at,
        "released": released,
        "can_release": req.requester_user_id == user.id and req.status in ("approved", "approved_with_restrictions"),
    }


def serialize_summary(db: Session, user: CurrentUser, req: DataRequest,
                      policy: PolicyDocument | None = None) -> RequestSummaryOut:
    return RequestSummaryOut(**summary(db, user, req, policy))


def audit_out(db: Session, events: list[AuditEvent]) -> list[AuditEventOut]:
    org_ids = {e.actor_org_id for e in events if e.actor_org_id}
    names: dict[uuid.UUID | None, str] = {}
    if org_ids:
        names = {i: n for i, n in db.execute(select(Organization.id, Organization.name)
                                             .where(Organization.id.in_(org_ids))).all()}
    req_ids = {e.request_id for e in events if e.request_id}
    numbers: dict[uuid.UUID | None, int] = {}
    if req_ids:
        numbers = {i: n for i, n in db.execute(select(DataRequest.id, DataRequest.request_number)
                                               .where(DataRequest.id.in_(req_ids))).all()}
    return [
        AuditEventOut(
            id=e.id, seq=e.seq, occurred_at=e.occurred_at, actor_display=e.actor_display, actor_role=e.actor_role,
            actor_org_id=e.actor_org_id, actor_org_name=names.get(e.actor_org_id), action=e.action,
            outcome=e.outcome, resource_type=e.resource_type, resource_id=e.resource_id, request_id=e.request_id,
            request_number=numbers.get(e.request_id), decision=e.decision, policy_codes=list(e.policy_codes or []),
            details=e.details or {}, event_hash=e.event_hash, prev_hash=e.prev_hash,
            hash_valid=compute_hash(e) == e.event_hash,
        )
        for e in events
    ]


def can_review(user: CurrentUser, req: DataRequest) -> bool:
    return (
        user.role == "privacy_reviewer"
        and req.status == "pending_review"
        and req.custodian_org_id == user.org_id
        and req.requester_org_id != user.org_id
        and req.requester_user_id != user.id
    )


def detail(db: Session, user: CurrentUser, req: DataRequest) -> RequestDetailOut:
    base = summary(db, user, req)
    reviews = db.execute(select(ReviewDecision).where(ReviewDecision.request_id == req.id)
                         .order_by(ReviewDecision.created_at)).scalars().all()
    rev_orgs = {r.reviewer_org_id for r in reviews}
    org_names: dict[uuid.UUID, str] = {}
    if rev_orgs:
        org_names = {i: n for i, n in db.execute(select(Organization.id, Organization.name)
                                                 .where(Organization.id.in_(rev_orgs))).all()}
    releases = db.execute(select(DataRelease).where(DataRelease.request_id == req.id)
                          .order_by(DataRelease.released_at)).scalars().all()
    events = db.execute(select(AuditEvent).where(AuditEvent.request_id == req.id).order_by(AuditEvent.seq)
                        ).scalars().all()
    enc_sensitive = None
    if req.encounter_id and user.role in ("privacy_reviewer", "hospital_staff"):
        enc = db.get(Encounter, req.encounter_id)
        enc_sensitive = bool(enc and enc.sensitive_category)
    redact = user.role == "platform_admin"
    return RequestDetailOut(
        **base,
        justification=req.justification,
        consent_reference=None if redact else req.consent_reference,
        field_decisions=req.field_decisions,
        review_reasons=req.review_reasons,
        aggregate_query=req.aggregate_query,
        reviews=[ReviewOut(id=r.id, reviewer_name=r.reviewer.display_name, reviewer_org=org_names.get(
            r.reviewer_org_id), decision=r.decision, justification=r.justification,
            resulting_status=r.resulting_status, created_at=r.created_at) for r in reviews],
        releases=[ReleaseSummaryOut(id=r.id, released_fields=r.released_fields, released_at=r.released_at,
                                    metadata=r.release_metadata) for r in releases],
        audit_events=audit_out(db, list(events)),
        encounter_sensitive=enc_sensitive,
        can_review=can_review(user, req),
    )


# ------------------------------------------------------------------ release
def _lock_request(db: Session, request_id: uuid.UUID) -> DataRequest | None:
    """SELECT ... FOR UPDATE OF data_requests, serialising release/review/re-evaluation per request."""
    stmt = select(DataRequest).where(DataRequest.id == request_id).with_for_update(of=DataRequest)
    return db.execute(stmt).scalar_one_or_none()


def _money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def _patient_records(db: Session, req: DataRequest, fields: list[str]) -> list[dict[str, Any]]:
    """Build records containing ONLY the given approved field identifiers."""
    patient = db.get(Patient, req.patient_id)
    claim = db.get(InsuranceClaim, req.claim_id) if req.claim_id else None
    assert patient is not None
    if "full_medical_history" in fields:
        encounters = db.execute(select(Encounter).where(
            Encounter.patient_id == req.patient_id, Encounter.custodian_org_id == req.custodian_org_id
        ).order_by(Encounter.treatment_date)).scalars().all()
    else:
        enc = db.get(Encounter, req.encounter_id)
        encounters = [enc] if enc else []

    out: list[dict[str, Any]] = []
    for enc in encounters:
        rec: dict[str, Any] = {}
        for f in fields:
            if f == "full_medical_history":
                continue
            if f == "patient_reference":
                rec[f] = patient.synthetic_ref
            elif f == "claim_reference":
                rec[f] = claim.claim_reference if claim and claim.encounter_id == enc.id else None
            elif f == "age_band":
                rec[f] = aggregates.age_band(patient.birth_year, enc.treatment_date)
            elif f == "sex":
                rec[f] = patient.sex
            elif f == "region":
                rec[f] = patient.region
            elif f == "treatment_date":
                rec[f] = enc.treatment_date.isoformat()
            elif f == "billing_amount":
                rec[f] = _money(enc.billing_amount_cents)
            elif f in ("diagnosis_code", "diagnosis_description", "procedure_code", "medication",
                       "clinician_notes", "sensitive_notes"):
                rec[f] = getattr(enc, f)
            # Any other identifier is ignored: fail closed.
        out.append(rec)
    return out


def release(db: Session, user: CurrentUser, request_id: uuid.UUID) -> ReleaseOut:
    # Lock the request row so a concurrent review/re-evaluation cannot interleave.
    req = _lock_request(db, request_id)
    if req is None:
        raise not_found("Request not found.")
    if req.requester_user_id != user.id:
        record_denied(db, user, "RELEASE_NOT_REQUESTER", str(request_id), resource_type="data_request",
                      related=_related(req))
        raise forbidden("Data is released only to the user who made the request.")
    if req.status not in ("approved", "approved_with_restrictions"):
        append_event(db, actor=user.actor, action="RELEASE_BLOCKED", outcome="denied", resource_type="data_request",
                     resource_id=str(req.id), request_id=req.id, related_org_ids=_related(req), decision=req.status,
                     policy_codes=["NOT_RELEASABLE"], details={"status": req.status})
        db.commit()
        raise AppError(409, "NOT_RELEASABLE", "This request has no approved data to release "
                       f"(status: {req.status.replace('_', ' ')}).")

    # Re-authorise at release time against current identity, consent, references and policy.
    policy = active_policy(db)
    reviewed = db.execute(select(ReviewDecision).where(ReviewDecision.request_id == req.id,
                                                       ReviewDecision.decision == "approve")).first() is not None
    ctx, consent = build_context(db, principal=principal_for(db, user.id), purpose=req.purpose,
                                 requested_fields=list(req.requested_fields), policy=policy,
                                 target=target_from_request(req), review_approved=reviewed)
    result = evaluate(ctx)
    related = _related(req)

    # Never widen: releasable = previously approved ∩ approved now.
    still_ok = [f for f in req.approved_fields if f in set(result.approved_fields)]
    if not result.releasable or (req.scope_type == "patient" and not still_ok):
        new = result
        if result.status == "pending_review":
            # Conditions changed in a way that needs a fresh review: treat as rejection for this release.
            new.policy_codes = [*new.policy_codes, "REEVALUATION_DENIED"]
        _apply_result(req, new)
        if req.status not in ("rejected", "pending_review"):
            req.status = "rejected"
        if "REEVALUATION_DENIED" not in req.policy_codes:
            req.policy_codes = [*req.policy_codes, "REEVALUATION_DENIED"]
        req.decision_explanation = ("Authorisation was re-evaluated at release time and no longer holds. "
                                    + result.explanation)
        append_event(db, actor=user.actor, action="POLICY_REEVALUATED", outcome="denied",
                     resource_type="data_request", resource_id=str(req.id), request_id=req.id,
                     related_org_ids=related, decision=req.status, policy_codes=req.policy_codes,
                     details={"stage": "release", "policy_version": result.policy_version,
                              "consent_status": consent.status})
        db.commit()
        raise AppError(409, "REAUTHORIZATION_FAILED",
                       "Authorisation was re-checked at release time and no longer holds. Nothing was released.",
                       {"policy_codes": req.policy_codes})

    if set(still_ok) != set(req.approved_fields) or result.policy_version != req.policy_version:
        newly_denied = [f for f in req.approved_fields if f not in still_ok]
        req.approved_fields = still_ok
        req.denied_fields = list(dict.fromkeys([*req.denied_fields, *newly_denied]))
        if newly_denied:
            req.status = "approved_with_restrictions"
        req.policy_version = result.policy_version
        req.evaluated_at = result.evaluated_at
        append_event(db, actor=user.actor, action="POLICY_REEVALUATED", outcome="success",
                     resource_type="data_request", resource_id=str(req.id), request_id=req.id,
                     related_org_ids=related, decision=req.status, policy_codes=result.policy_codes,
                     details={"stage": "release", "newly_denied_fields": newly_denied,
                              "policy_version": result.policy_version})

    records = None
    agg = None
    meta: dict[str, Any] = {"policy_version": result.policy_version, "policy_codes": result.policy_codes,
                            "reevaluated": True}
    if req.scope_type == "patient":
        records = _patient_records(db, req, still_ok)
        meta["record_count"] = len(records)
        released_fields = still_ok
    else:
        target = target_from_request(req)
        assert target.aggregate is not None and result.min_group_size is not None
        agg = aggregates.compute(db, target.aggregate, result.min_group_size,
                                 bool(result.extras.get("secondary_suppression", True)))
        meta.update({"metric": agg.metric, "group_by": agg.group_by, "groups": len(agg.groups),
                     "suppressed_groups": agg.suppressed_groups,
                     "secondary_suppressions": agg.secondary_suppressions,
                     "min_group_size": agg.min_group_size})
        released_fields = []

    rel = DataRelease(request_id=req.id, released_to_user_id=user.id, released_fields=released_fields,
                      release_metadata=meta)
    db.add(rel)
    db.flush()
    append_event(db, actor=user.actor, action="DATA_RELEASED", outcome="success", resource_type="data_release",
                 resource_id=str(rel.id), request_id=req.id, related_org_ids=related, decision=req.status,
                 policy_codes=result.policy_codes,
                 details={"released_fields": released_fields, "withheld_fields": list(req.denied_fields),
                          **{k: v for k, v in meta.items() if k not in ("policy_codes",)}})
    db.commit()
    return ReleaseOut(
        request_id=req.id, release_id=rel.id, released_at=rel.released_at, policy_version=result.policy_version,
        scope_type=req.scope_type,  # type: ignore[arg-type]
        released_fields=released_fields, withheld_fields=list(req.denied_fields), records=records, aggregate=agg,
        reevaluation=decision_out(result),
    )


# ------------------------------------------------------------------ review
def review_queue_query(user: CurrentUser) -> Select[tuple[DataRequest]]:
    return select(DataRequest).where(
        DataRequest.status == "pending_review",
        DataRequest.custodian_org_id == user.org_id,
        DataRequest.requester_org_id != user.org_id,
        DataRequest.requester_user_id != user.id,
    )


def decide_review(db: Session, user: CurrentUser, request_id: uuid.UUID, decision: str,
                  justification: str) -> DataRequest:
    if user.role != "privacy_reviewer":
        record_denied(db, user, "ROLE_NOT_PERMITTED", f"review:{request_id}", resource_type="data_request")
        raise forbidden("Only privacy reviewers can decide reviews.")
    req = _lock_request(db, request_id)
    if req is None:
        raise not_found("Request not found.")
    related = _related(req)
    # Scope + separation of duties.
    if req.custodian_org_id != user.org_id:
        record_denied(db, user, "REVIEW_OUT_OF_SCOPE", str(request_id), resource_type="data_request",
                      related=related)
        raise forbidden("You can only review requests for records held by your organisation.")
    if req.requester_user_id == user.id or req.requester_org_id == user.org_id:
        record_denied(db, user, "SEPARATION_OF_DUTIES", str(request_id), resource_type="data_request",
                      related=related)
        raise forbidden("Separation of duties: you cannot review a request made by you or your organisation.",
                        code="SEPARATION_OF_DUTIES")
    if req.status != "pending_review":
        raise conflict("This request is not awaiting review.", code="NOT_PENDING_REVIEW")
    existing = db.execute(select(ReviewDecision).where(ReviewDecision.request_id == req.id)).first()
    if existing is not None:
        raise conflict("A review decision has already been recorded.", code="ALREADY_REVIEWED")

    policy = active_policy(db)
    if decision == "approve":
        # Re-evaluate with review approval: only review-required fields can flip; hard denials stand.
        ctx, _ = build_context(db, principal=principal_for(db, req.requester_user_id), purpose=req.purpose,
                                     requested_fields=list(req.requested_fields), policy=policy,
                                     target=target_from_request(req), review_approved=True)
        result = evaluate(ctx)
        _apply_result(req, result)
        if result.status == "rejected":
            req.decision_explanation = ("The reviewer approved, but re-evaluation rejected the request: "
                                        + result.explanation)
    else:
        req.status = "rejected"
        req.approved_fields = []
        req.denied_fields = list(req.requested_fields)
        req.review_fields = []
        req.field_decisions = {f: "denied:review_rejected" for f in req.requested_fields}
        req.policy_codes = [*req.policy_codes, "REVIEW_REJECTED"]
        req.decision_explanation = "Rejected by privacy review. Nothing will be released."
        req.evaluated_at = dt.datetime.now(dt.UTC)
        req.policy_version = policy.version

    rd = ReviewDecision(request_id=req.id, reviewer_user_id=user.id, reviewer_org_id=user.org_id,
                        decision=decision, justification=justification, resulting_status=req.status)
    db.add(rd)
    db.flush()
    append_event(db, actor=user.actor, action="REVIEW_DECIDED", outcome="success", resource_type="review_decision",
                 resource_id=str(rd.id), request_id=req.id, related_org_ids=related, decision=decision,
                 policy_codes=["REVIEW_APPROVED" if decision == "approve" else "REVIEW_REJECTED"],
                 details={"justification_length": len(justification), "resulting_status": req.status})
    append_event(db, actor=user.actor, action="POLICY_EVALUATED", outcome="denied" if req.status == "rejected"
                 else "success", resource_type="data_request", resource_id=str(req.id), request_id=req.id,
                 related_org_ids=related, decision=req.status, policy_codes=req.policy_codes,
                 details={"stage": "post_review", "approved_fields": req.approved_fields,
                          "denied_fields": req.denied_fields, "policy_version": req.policy_version})
    db.commit()
    return req


# ------------------------------------------------------------------ dashboard
def status_counts(db: Session, user: CurrentUser) -> dict[str, int]:
    sub = visible_requests_query(user).subquery()
    rows = db.execute(select(sub.c.status, func.count()).group_by(sub.c.status)).all()
    return {s: n for s, n in rows}


def release_count(db: Session, user: CurrentUser) -> int:
    sub = visible_requests_query(user).with_only_columns(DataRequest.id).subquery()
    return int(db.execute(select(func.count()).select_from(DataRelease).where(
        DataRelease.request_id.in_(select(sub.c.id)))).scalar_one())

