"""Typed request/response schemas.

Input models use `extra="forbid"`: a body containing `status`,
`organization_id`, `role`, `approved_fields` or any other unexpected key is
rejected (mass-assignment protection). Server-controlled values are never read
from the client.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.policy.catalog import AGGREGATE_GROUPINGS, AGGREGATE_METRICS, FIELD_CATALOG

REF_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,31}$")
PURPOSE_RE = re.compile(r"^[a-z][a-z_]{2,63}$")


class StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorBody(BaseModel):
    code: str
    message: str
    correlation_id: str | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---------------------------------------------------------------- identity
class MeOut(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    role: str
    organization: OrgRef
    permissions: list[str]


class OrgRef(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    slug: str | None = None


MeOut.model_rebuild()


# ---------------------------------------------------------------- requests
class AggregateQueryIn(StrictIn):
    metric: str
    group_by: list[str] = Field(min_length=1, max_length=3)
    period_start: dt.date
    period_end: dt.date

    @field_validator("metric")
    @classmethod
    def _metric(cls, v: str) -> str:
        if v not in AGGREGATE_METRICS:
            raise ValueError("unsupported metric")
        return v

    @field_validator("group_by")
    @classmethod
    def _group_by(cls, v: list[str]) -> list[str]:
        for g in v:
            if g not in AGGREGATE_GROUPINGS:
                raise ValueError("unsupported grouping")
        if len(set(v)) != len(v):
            raise ValueError("duplicate grouping")
        return v

    @model_validator(mode="after")
    def _period(self) -> AggregateQueryIn:
        if self.period_end < self.period_start:
            raise ValueError("period_end must not be before period_start")
        return self


class CreateRequestIn(StrictIn):
    purpose: str = Field(min_length=3, max_length=64)
    claim_reference: str | None = None
    referral_reference: str | None = None
    mandate_reference: str | None = None
    requested_fields: list[str] = Field(default_factory=list, max_length=len(FIELD_CATALOG))
    aggregate: AggregateQueryIn | None = None
    justification: str = Field(min_length=10, max_length=2000)

    @field_validator("purpose")
    @classmethod
    def _purpose(cls, v: str) -> str:
        if not PURPOSE_RE.fullmatch(v):
            raise ValueError("invalid purpose identifier")
        return v

    @field_validator("claim_reference", "referral_reference", "mandate_reference")
    @classmethod
    def _ref(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        v = v.upper()
        if not REF_RE.fullmatch(v):
            raise ValueError("invalid reference format")
        return v

    @field_validator("requested_fields")
    @classmethod
    def _fields(cls, v: list[str]) -> list[str]:
        unknown = [f for f in v if f not in FIELD_CATALOG]
        if unknown:
            # Do not echo arbitrary input back; report only how many were unknown.
            raise ValueError(f"{len(unknown)} unknown field identifier(s); use identifiers from /api/policy/fields")
        if len(set(v)) != len(v):
            raise ValueError("duplicate field identifiers")
        return v

    @model_validator(mode="after")
    def _one_target(self) -> CreateRequestIn:
        targets = [t for t in (self.claim_reference, self.referral_reference, self.aggregate) if t is not None]
        if len(targets) != 1:
            raise ValueError("specify exactly one of claim_reference, referral_reference or aggregate")
        if self.aggregate is not None and not self.mandate_reference:
            raise ValueError("aggregate requests need a mandate_reference")
        if self.aggregate is None and self.mandate_reference:
            raise ValueError("mandate_reference only applies to aggregate requests")
        return self


class PolicyDecisionOut(BaseModel):
    status: str
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


class ReviewOut(BaseModel):
    id: uuid.UUID
    reviewer_name: str
    reviewer_org: str | None = None
    decision: str
    justification: str
    resulting_status: str
    created_at: dt.datetime


class ReleaseSummaryOut(BaseModel):
    id: uuid.UUID
    released_fields: list[str]
    released_at: dt.datetime
    metadata: dict[str, Any]


class AuditEventOut(BaseModel):
    id: int
    seq: int
    occurred_at: dt.datetime
    actor_display: str | None
    actor_role: str | None
    actor_org_id: uuid.UUID | None
    actor_org_name: str | None
    action: str
    outcome: str
    resource_type: str | None
    resource_id: str | None
    request_id: uuid.UUID | None
    request_number: int | None = None
    decision: str | None
    policy_codes: list[str]
    details: dict[str, Any]
    event_hash: str
    prev_hash: str
    hash_valid: bool


class RequestSummaryOut(BaseModel):
    id: uuid.UUID
    request_number: int
    purpose: str
    purpose_label: str
    scope_type: str
    status: str
    requester_name: str
    requester_org: OrgRef
    custodian_org: OrgRef | None
    patient_reference: str | None
    patient_alias: str | None
    authorization_reference: str | None
    requested_fields: list[str]
    approved_fields: list[str]
    denied_fields: list[str]
    review_fields: list[str]
    review_required: bool
    policy_codes: list[str]
    decision_explanation: str
    policy_version: str
    created_at: dt.datetime
    evaluated_at: dt.datetime
    released: bool
    can_release: bool


class RequestDetailOut(RequestSummaryOut):
    justification: str
    consent_reference: str | None
    field_decisions: dict[str, str]
    review_reasons: list[str]
    aggregate_query: dict[str, Any] | None
    reviews: list[ReviewOut]
    releases: list[ReleaseSummaryOut]
    audit_events: list[AuditEventOut]
    encounter_sensitive: bool | None = None
    can_review: bool = False


class CreateRequestOut(BaseModel):
    request: RequestDetailOut


class PreviewOut(BaseModel):
    decision: PolicyDecisionOut
    consent_status: str
    authorization_reference: str


class ReleaseOut(BaseModel):
    request_id: uuid.UUID
    release_id: uuid.UUID
    released_at: dt.datetime
    policy_version: str
    scope_type: Literal["patient", "aggregate"]
    released_fields: list[str]
    withheld_fields: list[str]
    records: list[dict[str, Any]] | None = None
    aggregate: AggregateResultOut | None = None
    reevaluation: PolicyDecisionOut


class AggregateGroupOut(BaseModel):
    group: dict[str, str]
    value: int | None
    suppressed: bool
    suppression_reason: str | None = None


class AggregateResultOut(BaseModel):
    metric: str
    metric_label: str
    group_by: list[str]
    period_start: dt.date
    period_end: dt.date
    min_group_size: int
    groups: list[AggregateGroupOut]
    suppressed_groups: int
    secondary_suppressions: int
    notice: str


ReleaseOut.model_rebuild()


class ReviewDecisionIn(StrictIn):
    decision: Literal["approve", "reject"]
    justification: str = Field(min_length=20, max_length=2000)


class ReviewResultOut(BaseModel):
    request: RequestDetailOut


# ---------------------------------------------------------------- dashboard
class DashboardOut(BaseModel):
    scope_description: str
    total_requests: int
    pending_review: int
    approved: int
    approved_with_restrictions: int
    rejected: int
    releases: int
    review_queue: int | None
    recent_events: list[AuditEventOut]


# ---------------------------------------------------------------- patients
class EncounterOut(BaseModel):
    encounter_reference: str
    treatment_date: dt.date
    diagnosis_code: str
    diagnosis_description: str
    diagnosis_category: str
    procedure_code: str | None
    medication: str | None
    clinician_notes: str | None
    has_sensitive_notes: bool
    sensitive_category: bool
    billing_amount: str


class ConsentOut(BaseModel):
    consent_reference: str
    purpose: str
    grantee_org: str | None
    status: str
    excluded_fields: list[str]
    valid_until: dt.date | None


class PatientSummaryOut(BaseModel):
    id: uuid.UUID
    synthetic_ref: str
    display_alias: str
    birth_year: int
    sex: str
    region: str
    encounter_count: int


class PatientDetailOut(PatientSummaryOut):
    custodian_org: str
    encounters: list[EncounterOut]
    consents: list[ConsentOut]


class ClaimOut(BaseModel):
    claim_reference: str
    patient_reference: str
    patient_alias: str
    policy_number: str
    amount_claimed: str
    status: str
    lodged_at: dt.datetime
    custodian_org: str


class ReferralOut(BaseModel):
    referral_reference: str
    patient_reference: str
    patient_alias: str
    from_org: str
    status: str
    created_at: dt.datetime


class MandateOut(BaseModel):
    mandate_reference: str
    description: str
    allowed_metrics: list[str]
    valid_until: dt.date | None
    is_active: bool


# ---------------------------------------------------------------- audit
class AuditPageOut(BaseModel):
    items: list[AuditEventOut]
    total: int
    limit: int
    offset: int
    scope_description: str


class VerifyOut(BaseModel):
    valid: bool
    checked_events: int
    head_seq: int | None
    head_hash: str | None
    first_invalid_seq: int | None
    problem: str | None
    verified_at: dt.datetime
    notice: str


# ---------------------------------------------------------------- policy/admin
class FieldOut(BaseModel):
    id: str
    label: str
    sensitivity: str
    description: str


class PolicyOut(BaseModel):
    version: str
    activated_at: dt.datetime
    change_note: str
    document: dict[str, Any]
    fields: list[FieldOut]
    metrics: dict[str, str]
    groupings: dict[str, str]
    effective_min_group_size: int
    history: list[PolicyVersionOut]


class PolicyVersionOut(BaseModel):
    version: str
    is_active: bool
    change_note: str
    created_at: dt.datetime
    created_by: str | None


PolicyOut.model_rebuild()


class PolicyChangeIn(StrictIn):
    document: dict[str, Any]
    change_note: str = Field(min_length=10, max_length=1000)


class AdminUserOut(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str
    role: str
    organization: OrgRef
    is_active: bool
    identity_linked: bool
    last_seen_at: dt.datetime | None
    created_at: dt.datetime


class AdminUserCreateIn(StrictIn):
    username: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9._-]+$")
    display_name: str = Field(min_length=2, max_length=200)
    organization_slug: str = Field(min_length=2, max_length=64)
    role: Literal["hospital_staff", "insurance_officer", "government_officer", "privacy_reviewer"]
    external_subject: str | None = Field(default=None, max_length=255)


class AdminUserUpdateIn(StrictIn):
    is_active: bool | None = None
    display_name: str | None = Field(default=None, min_length=2, max_length=200)
    external_subject: str | None = Field(default=None, max_length=255)
    unlink_identity: bool = False


class AdminOrgOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    type: str
    is_active: bool
    user_count: int


class AdminOrgUpdateIn(StrictIn):
    is_active: bool


class HealthOut(BaseModel):
    status: str
    checks: dict[str, Any]
