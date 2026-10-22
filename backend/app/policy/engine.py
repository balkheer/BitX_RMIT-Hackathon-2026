"""Pure, deterministic policy evaluation.

`evaluate()` takes a fully-resolved `EvaluationContext` (built server-side from
verified identity and database state) and returns a `PolicyResult`. It performs
no I/O, so it is exhaustively unit-testable, and it fails closed: any missing
fact or unexpected error produces a rejection.

Decision vocabulary
-------------------
approved                    every requested field is releasable
approved_with_restrictions  some fields denied; the rest releasable
pending_review              some fields need a privacy reviewer; nothing is
                            releasable until a decision is recorded
rejected                    nothing is releasable

Hard denials (`hard_reject` and per-field hard denials) cannot be overridden by
a reviewer: review only converts `review_required` fields into approvals, and
the request is always re-evaluated afterwards.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Literal

from app.policy.catalog import AGGREGATE_GROUPINGS, AGGREGATE_METRICS, FIELD_CATALOG
from app.policy.document import PolicyDocument, PurposeRules

Status = Literal["approved", "approved_with_restrictions", "pending_review", "rejected"]

CODE_MESSAGES: dict[str, str] = {
    "IDENTITY_INACTIVE": "The requester or their organisation is not active.",
    "PURPOSE_UNKNOWN": "The declared purpose is not defined in the active policy.",
    "ROLE_NOT_PERMITTED_FOR_PURPOSE": "The requester's role may not make requests for this purpose.",
    "ORG_TYPE_MISMATCH": "The requester's organisation type cannot use this purpose.",
    "SCOPE_MISMATCH": "The request scope does not match what this purpose allows.",
    "NO_FIELDS_REQUESTED": "No fields were requested.",
    "FIELD_UNKNOWN": "One or more field identifiers are not recognised.",
    "PATIENT_NOT_FOUND": "The requested patient scope could not be resolved.",
    "AUTHZ_CLAIM_INVALID": "No matching claim lodged with the requesting insurer exists for this patient.",
    "AUTHZ_REFERRAL_INVALID": "No active referral to the requesting hospital exists for this patient.",
    "AUTHZ_MANDATE_INVALID": "No active reporting mandate covers this metric for the requesting agency.",
    "CONSENT_NOT_AVAILABLE": (
        "The patient's recorded consent for this purpose is missing, refused, withdrawn or expired."
    ),
    "CONSENT_REFUSED": "The patient has refused or withdrawn consent for this purpose.",
    "CONSENT_FIELD_EXCLUDED": "The patient's consent excludes one or more requested fields.",
    "CONSENT_VERIFIED": "A current consent record for this purpose and organisation was found.",
    "LEGAL_BASIS_REFERRAL": "Sharing relies on an active referral (treatment relationship).",
    "LEGAL_BASIS_MANDATE": "Sharing relies on an active public-health reporting mandate.",
    "AUTHZ_CLAIM_VERIFIED": "The claim is lodged with the requesting insurer for this patient.",
    "EXCESSIVE_SCOPE": "The request asks for more than this purpose can justify and is rejected as a whole.",
    "FIELD_HARD_DENIED": "One or more fields are never released for this purpose.",
    "FIELD_NOT_PERMITTED_FOR_PURPOSE": "One or more fields are not necessary for this purpose (data minimisation).",
    "REVIEW_FIELD_SENSITIVE": "One or more fields require a privacy reviewer's approval for this purpose.",
    "REVIEW_SENSITIVE_CATEGORY": "The encounter is in a sensitive category, so clinical codes require review.",
    "REVIEW_APPROVED": "A privacy reviewer approved the review-required fields.",
    "NO_PERMITTED_FIELDS": "None of the requested fields can be released.",
    "AGGREGATE_ONLY": "Only aggregate results are released; no patient-level records.",
    "PATIENT_LEVEL_NOT_PERMITTED": "Aggregate purposes cannot request patient-level fields.",
    "AGGREGATE_QUERY_NOT_PERMITTED": "The metric, grouping or period is not permitted by the active policy.",
    "SMALL_GROUP_SUPPRESSION": "Groups below the minimum size are suppressed.",
    "EVALUATION_ERROR": "Authorisation could not be established, so the request is rejected (fail closed).",
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    org_id: str
    org_type: str
    user_active: bool
    org_active: bool


@dataclass(frozen=True)
class ConsentFacts:
    status: Literal["granted", "withdrawn", "refused", "none"]
    reference: str | None = None
    expired: bool = False
    excluded_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class PatientScope:
    patient_found: bool
    custodian_org_id: str | None = None
    encounter_sensitive: bool = False
    claim_valid: bool = False  # claim lodged with requester for this patient
    referral_valid: bool = False  # active referral custodian -> requester for this patient


@dataclass(frozen=True)
class AggregateQuery:
    metric: str
    group_by: tuple[str, ...]
    period_start: dt.date
    period_end: dt.date


@dataclass(frozen=True)
class AggregateScope:
    query: AggregateQuery
    mandate_valid: bool  # active, unexpired mandate for requester org covering metric


@dataclass(frozen=True)
class EvaluationContext:
    principal: Principal
    purpose: str
    requested_fields: tuple[str, ...]
    policy: PolicyDocument
    patient: PatientScope | None = None
    aggregate: AggregateScope | None = None
    consent: ConsentFacts = ConsentFacts(status="none")
    review_approved: bool = False
    min_group_size_floor: int = 5
    now: dt.datetime | None = None


@dataclass
class PolicyResult:
    status: Status
    approved_fields: list[str]
    denied_fields: list[str]
    review_fields: list[str]
    field_decisions: dict[str, str]
    review_required: bool
    review_reasons: list[str]
    policy_codes: list[str]
    explanation: str
    policy_version: str
    evaluated_at: dt.datetime
    hard_reject: bool = False
    min_group_size: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def releasable(self) -> bool:
        return self.status in ("approved", "approved_with_restrictions")


ORG_TYPE_FOR_ROLE = {
    "insurance_officer": "insurer",
    "hospital_staff": "hospital",
    "government_officer": "government",
}


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _explain(status: Status, codes: list[str], denied: list[str], review: list[str]) -> str:
    head = {
        "approved": "Approved: every requested field is permitted for this purpose.",
        "approved_with_restrictions": "Approved with restrictions: only the permitted fields will be released.",
        "pending_review": "Pending review: a privacy reviewer must decide before anything is released.",
        "rejected": "Rejected: nothing will be released.",
    }[status]
    parts = [head]
    for c in _uniq(codes):
        msg = CODE_MESSAGES.get(c)
        if msg:
            parts.append(msg)
    if denied:
        parts.append("Denied field identifiers: " + ", ".join(denied) + ".")
    if review and status == "pending_review":
        parts.append("Fields awaiting review: " + ", ".join(review) + ".")
    return " ".join(p for p in parts if p)


def _reject(ctx: EvaluationContext, now: dt.datetime, codes: list[str], requested: list[str]) -> PolicyResult:
    decisions = {f: "denied" for f in requested}
    return PolicyResult(
        status="rejected",
        approved_fields=[],
        denied_fields=list(requested),
        review_fields=[],
        field_decisions=decisions,
        review_required=False,
        review_reasons=[],
        policy_codes=_uniq(codes),
        explanation=_explain("rejected", codes, list(requested), []),
        policy_version=ctx.policy.version,
        evaluated_at=now,
        hard_reject=True,
    )


def evaluate(ctx: EvaluationContext) -> PolicyResult:
    now = ctx.now or dt.datetime.now(dt.UTC)
    try:
        return _evaluate(ctx, now)
    except Exception:  # noqa: BLE001 - fail closed on any evaluation fault
        return _reject(ctx, now, ["EVALUATION_ERROR"], list(ctx.requested_fields))


def _evaluate(ctx: EvaluationContext, now: dt.datetime) -> PolicyResult:
    requested = _uniq([str(f) for f in ctx.requested_fields])
    p = ctx.principal

    if not (p.user_active and p.org_active):
        return _reject(ctx, now, ["IDENTITY_INACTIVE"], requested)

    rules: PurposeRules | None = ctx.policy.purposes.get(ctx.purpose)
    if rules is None:
        return _reject(ctx, now, ["PURPOSE_UNKNOWN"], requested)

    if p.role not in rules.allowed_roles:
        return _reject(ctx, now, ["ROLE_NOT_PERMITTED_FOR_PURPOSE"], requested)
    expected_org_type = ORG_TYPE_FOR_ROLE.get(p.role)
    if expected_org_type is None or expected_org_type != p.org_type:
        return _reject(ctx, now, ["ORG_TYPE_MISMATCH"], requested)

    if rules.scope == "aggregate":
        return _evaluate_aggregate(ctx, rules, requested, now)
    if ctx.patient is None or ctx.aggregate is not None:
        return _reject(ctx, now, ["SCOPE_MISMATCH"], requested)
    return _evaluate_patient(ctx, rules, requested, now)


def _evaluate_patient(
    ctx: EvaluationContext, rules: PurposeRules, requested: list[str], now: dt.datetime
) -> PolicyResult:
    scope = ctx.patient
    assert scope is not None
    codes: list[str] = []

    if not requested:
        return _reject(ctx, now, ["NO_FIELDS_REQUESTED"], requested)
    unknown = [f for f in requested if f not in FIELD_CATALOG]
    if unknown:
        return _reject(ctx, now, ["FIELD_UNKNOWN"], requested)
    # --- Authorisation reference (distinct from consent) ---------------------
    # Checked before patient resolution so a missing patient and a foreign
    # claim/referral produce the same answer (no existence oracle).
    if rules.authorization == "insurance_claim":
        if not scope.claim_valid:
            return _reject(ctx, now, ["AUTHZ_CLAIM_INVALID"], requested)
        codes.append("AUTHZ_CLAIM_VERIFIED")
    elif rules.authorization == "referral":
        if not scope.referral_valid:
            return _reject(ctx, now, ["AUTHZ_REFERRAL_INVALID"], requested)
        codes.append("LEGAL_BASIS_REFERRAL")
    else:
        return _reject(ctx, now, ["SCOPE_MISMATCH"], requested)
    if not scope.patient_found or not scope.custodian_org_id:
        return _reject(ctx, now, ["PATIENT_NOT_FOUND"], requested)

    # --- Consent, where the policy says it applies ---------------------------
    consent = ctx.consent
    consent_ok = consent.status == "granted" and not consent.expired
    if rules.consent == "required":
        if not consent_ok:
            return _reject(ctx, now, [*codes, "CONSENT_NOT_AVAILABLE"], requested)
        codes.append("CONSENT_VERIFIED")
    elif rules.consent == "refusal_blocks":
        if consent.status in ("refused", "withdrawn"):
            return _reject(ctx, now, [*codes, "CONSENT_REFUSED"], requested)
    excluded = set(consent.excluded_fields) if consent.status == "granted" else set()

    # --- Whole-request excess -------------------------------------------------
    if any(f in rules.reject_if_requested for f in requested):
        return _reject(ctx, now, [*codes, "EXCESSIVE_SCOPE"], requested)
    hard_denied_requested = [f for f in requested if f in rules.fields.hard_denied]
    if len(hard_denied_requested) > rules.max_hard_denied_before_reject:
        return _reject(ctx, now, [*codes, "EXCESSIVE_SCOPE", "FIELD_HARD_DENIED"], requested)

    # --- Per-field classification --------------------------------------------
    approved: list[str] = []
    denied: list[str] = []
    review: list[str] = []
    decisions: dict[str, str] = {}
    review_reasons: list[str] = []

    for f in requested:
        if f in excluded:
            denied.append(f)
            decisions[f] = "denied:consent_excluded"
            codes.append("CONSENT_FIELD_EXCLUDED")
        elif f in rules.fields.hard_denied:
            denied.append(f)
            decisions[f] = "denied:hard_deny"
            codes.append("FIELD_HARD_DENIED")
        elif f in rules.fields.review_required:
            if ctx.review_approved:
                approved.append(f)
                decisions[f] = "approved:reviewed"
            else:
                review.append(f)
                decisions[f] = "review"
                codes.append("REVIEW_FIELD_SENSITIVE")
                review_reasons.append(f"{f} requires privacy review for {rules.label.lower()}")
        elif f in rules.fields.allowed:
            if scope.encounter_sensitive and f in rules.sensitive_category_review_fields:
                if ctx.review_approved:
                    approved.append(f)
                    decisions[f] = "approved:reviewed"
                else:
                    review.append(f)
                    decisions[f] = "review"
                    codes.append("REVIEW_SENSITIVE_CATEGORY")
                    review_reasons.append(f"{f} relates to a sensitive-category encounter")
            else:
                approved.append(f)
                decisions[f] = "approved"
        else:
            denied.append(f)
            decisions[f] = "denied:not_necessary_for_purpose"
            codes.append("FIELD_NOT_PERMITTED_FOR_PURPOSE")

    if ctx.review_approved and any(v == "approved:reviewed" for v in decisions.values()):
        codes.append("REVIEW_APPROVED")

    status: Status
    if not approved and not review:
        status = "rejected"
        codes.append("NO_PERMITTED_FIELDS")
    elif review:
        status = "pending_review"
    elif denied:
        status = "approved_with_restrictions"
    else:
        status = "approved"

    codes = _uniq(codes)
    return PolicyResult(
        status=status,
        approved_fields=approved,
        denied_fields=denied,
        review_fields=review,
        field_decisions=decisions,
        review_required=bool(review),
        review_reasons=_uniq(review_reasons),
        policy_codes=codes,
        explanation=_explain(status, codes, denied, review),
        policy_version=ctx.policy.version,
        evaluated_at=now,
        hard_reject=status == "rejected",
    )


def _evaluate_aggregate(
    ctx: EvaluationContext, rules: PurposeRules, requested: list[str], now: dt.datetime
) -> PolicyResult:
    agg_rules = rules.aggregate
    if agg_rules is None or ctx.aggregate is None or ctx.patient is not None:
        return _reject(ctx, now, ["SCOPE_MISMATCH"], requested)
    if requested:
        return _reject(ctx, now, ["PATIENT_LEVEL_NOT_PERMITTED"], requested)

    q = ctx.aggregate.query
    period_days = (q.period_end - q.period_start).days
    if (
        q.metric not in agg_rules.metrics
        or q.metric not in AGGREGATE_METRICS
        or len(q.group_by) == 0
        or len(q.group_by) > agg_rules.max_group_dimensions
        or any(g not in agg_rules.group_by or g not in AGGREGATE_GROUPINGS for g in q.group_by)
        or period_days < 0
        or period_days > agg_rules.max_period_days
    ):
        return _reject(ctx, now, ["AGGREGATE_QUERY_NOT_PERMITTED"], requested)
    if not ctx.aggregate.mandate_valid:
        return _reject(ctx, now, ["AUTHZ_MANDATE_INVALID"], requested)

    k = max(agg_rules.min_group_size, ctx.min_group_size_floor)
    codes = ["LEGAL_BASIS_MANDATE", "AGGREGATE_ONLY", "SMALL_GROUP_SUPPRESSION"]
    return PolicyResult(
        status="approved",
        approved_fields=[],
        denied_fields=[],
        review_fields=[],
        field_decisions={},
        review_required=False,
        review_reasons=[],
        policy_codes=codes,
        explanation=_explain("approved", codes, [], []) + f" Minimum group size: {k}.",
        policy_version=ctx.policy.version,
        evaluated_at=now,
        min_group_size=k,
        extras={"secondary_suppression": agg_rules.secondary_suppression},
    )
