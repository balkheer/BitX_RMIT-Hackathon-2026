"""Relational data model. All patient data in this project is synthetic."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

ORG_TYPES = ("hospital", "insurer", "government", "platform")
ROLES = ("hospital_staff", "insurance_officer", "government_officer", "privacy_reviewer", "platform_admin")
REQUEST_STATUSES = ("approved", "approved_with_restrictions", "pending_review", "rejected")
SCOPE_TYPES = ("patient", "aggregate")
CONSENT_STATUSES = ("granted", "withdrawn", "refused")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _in(col: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({quoted})"


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (CheckConstraint(_in("org_type", ORG_TYPES), name="ck_org_type"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    org_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("external_issuer", "external_subject", name="uq_user_identity"),
        CheckConstraint(_in("role", ROLES), name="ck_user_role"),
        CheckConstraint(
            "(external_subject IS NULL) = (external_issuer IS NULL)", name="ck_user_identity_pair"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # The verified `iss` + `sub` pair from TideCloak. NULL until an administrator links it.
    external_issuer: Mapped[str | None] = mapped_column(String(500))
    external_subject: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[Organization] = relationship(lazy="joined")


class Patient(TimestampMixin, Base):
    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    synthetic_ref: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    display_alias: Mapped[str] = mapped_column(String(120), nullable=False)
    birth_year: Mapped[int] = mapped_column(Integer, nullable=False)
    sex: Mapped[str] = mapped_column(String(16), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    custodian_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)


class PatientConsent(TimestampMixin, Base):
    """A recorded consent decision. Consent is one possible basis among several,
    not a universal requirement; the policy decides where it applies."""

    __tablename__ = "patient_consents"
    __table_args__ = (CheckConstraint(_in("status", CONSENT_STATUSES), name="ck_consent_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    consent_reference: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    grantee_org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    excluded_fields: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, server_default="{}")
    valid_until: Mapped[dt.date | None] = mapped_column(Date)


class Encounter(TimestampMixin, Base):
    """Patient data. Each column is a separately authorised field."""

    __tablename__ = "encounters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    encounter_reference: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    custodian_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    treatment_date: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    diagnosis_code: Mapped[str] = mapped_column(String(16), nullable=False)
    diagnosis_description: Mapped[str] = mapped_column(String(255), nullable=False)
    diagnosis_category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    procedure_code: Mapped[str | None] = mapped_column(String(32))
    medication: Mapped[str | None] = mapped_column(String(255))
    clinician_notes: Mapped[str | None] = mapped_column(Text)
    sensitive_notes: Mapped[str | None] = mapped_column(Text)
    sensitive_category: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    billing_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (CheckConstraint("billing_amount_cents >= 0", name="ck_encounter_billing_nonneg"),)


class InsuranceClaim(TimestampMixin, Base):
    __tablename__ = "insurance_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    claim_reference: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("encounters.id"), nullable=False, index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    insurer_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    policy_number: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_claimed_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="lodged")


class Referral(TimestampMixin, Base):
    """Treatment-relationship basis for continuity-of-care sharing between hospitals."""

    __tablename__ = "referrals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    referral_reference: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    encounter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("encounters.id"), nullable=False)
    from_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    to_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class ReportingMandate(TimestampMixin, Base):
    """Authorisation reference for government aggregate reporting."""

    __tablename__ = "reporting_mandates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    mandate_reference: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    allowed_metrics: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    valid_until: Mapped[dt.date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    __table_args__ = (
        Index("uq_policy_single_active", "is_active", unique=True, postgresql_where=text("is_active")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    version: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    change_note: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DataRequest(TimestampMixin, Base):
    __tablename__ = "data_requests"
    __table_args__ = (
        CheckConstraint(_in("status", REQUEST_STATUSES), name="ck_request_status"),
        CheckConstraint(_in("scope_type", SCOPE_TYPES), name="ck_request_scope"),
        CheckConstraint(
            "(scope_type = 'patient' AND patient_id IS NOT NULL AND aggregate_query IS NULL) OR "
            "(scope_type = 'aggregate' AND patient_id IS NULL AND aggregate_query IS NOT NULL)",
            name="ck_request_scope_shape",
        ),
        Index("ix_request_org_status", "requester_org_id", "status"),
        Index("ix_request_custodian_status", "custodian_org_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    request_number: Mapped[int] = mapped_column(BigInteger, Identity(start=1001), unique=True, nullable=False)
    requester_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    requester_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    custodian_org_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    patient_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patients.id"), index=True)
    encounter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("encounters.id"))
    claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("insurance_claims.id"))
    aggregate_query: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    purpose: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requested_fields: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, server_default="{}")
    authorization_reference: Mapped[str | None] = mapped_column(String(64))
    consent_reference: Mapped[str | None] = mapped_column(String(64))
    justification: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    approved_fields: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, server_default="{}")
    denied_fields: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, server_default="{}")
    review_fields: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, server_default="{}")
    field_decisions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    review_reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    policy_codes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, server_default="{}")
    decision_explanation: Mapped[str] = mapped_column(Text, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    requester: Mapped[User] = relationship(foreign_keys=[requester_user_id], lazy="joined")
    requester_org: Mapped[Organization] = relationship(foreign_keys=[requester_org_id], lazy="joined")
    custodian_org: Mapped[Organization | None] = relationship(foreign_keys=[custodian_org_id], lazy="joined")
    patient: Mapped[Patient | None] = relationship(lazy="joined")
    claim: Mapped[InsuranceClaim | None] = relationship(lazy="joined")


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        CheckConstraint("decision IN ('approve', 'reject')", name="ck_review_decision"),
        CheckConstraint("char_length(justification) >= 20", name="ck_review_justification_len"),
        UniqueConstraint("request_id", name="uq_review_one_per_request"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_requests.id"), nullable=False)
    reviewer_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    reviewer_org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    resulting_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    reviewer: Mapped[User] = relationship(lazy="joined")


class DataRelease(Base):
    __tablename__ = "data_releases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("data_requests.id"), nullable=False, index=True)
    released_to_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    released_fields: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)
    # Field identifiers, policy version, codes, suppression counts. Never patient values.
    release_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    released_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("seq", name="uq_audit_seq"),
        CheckConstraint("seq >= 1", name="ck_audit_seq_positive"),
        CheckConstraint("char_length(event_hash) = 64 AND char_length(prev_hash) = 64", name="ck_audit_hash_len"),
        Index("ix_audit_request", "request_id"),
        Index("ix_audit_actor_org", "actor_org_id"),
        Index("ix_audit_occurred", "occurred_at"),
        Index("ix_audit_related_orgs", "related_org_ids", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_display: Mapped[str | None] = mapped_column(String(200))
    actor_role: Mapped[str | None] = mapped_column(String(32))
    actor_org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # Organisations the event concerns (requester, custodian). Drives audit visibility.
    related_org_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    decision: Mapped[str | None] = mapped_column(String(32))
    policy_codes: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, server_default="{}")
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
