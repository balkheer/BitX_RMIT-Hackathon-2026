"""Versioned policy document: schema, validation and the initial policy.

The policy is data, stored in `policy_versions`, so administrators can change it
through an audited workflow. Validation keeps edits inside safe bounds: only
catalogue fields, no field in two buckets, no aggregate threshold below 5, and
hard-deny lists that cannot be emptied for fields marked as never shareable.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.policy.catalog import AGGREGATE_GROUPINGS, AGGREGATE_METRICS, FIELD_CATALOG

ConsentMode = Literal["required", "refusal_blocks", "not_applicable"]
AuthorizationMode = Literal["insurance_claim", "referral", "reporting_mandate"]
ROLE_NAMES = {"hospital_staff", "insurance_officer", "government_officer", "privacy_reviewer", "platform_admin"}

# Roles that may never be granted data-request purposes regardless of edits.
# Administration and review are separate duties from requesting data.
NEVER_REQUESTER_ROLES = {"platform_admin", "privacy_reviewer"}


class FieldRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: list[str] = []
    review_required: list[str] = []
    hard_denied: list[str] = []

    @model_validator(mode="after")
    def _validate(self) -> FieldRules:
        seen: dict[str, str] = {}
        for bucket in ("allowed", "review_required", "hard_denied"):
            for f in getattr(self, bucket):
                if f not in FIELD_CATALOG:
                    raise ValueError(f"unknown field identifier in policy: {f}")
                if f in seen:
                    raise ValueError(f"field {f} appears in both {seen[f]} and {bucket}")
                seen[f] = bucket
        return self


class AggregateRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[str]
    group_by: list[str]
    min_group_size: int = Field(ge=5)
    max_group_dimensions: int = Field(ge=1, le=1)
    max_period_days: int = Field(ge=1, le=1100)
    secondary_suppression: bool = True

    @field_validator("metrics")
    @classmethod
    def _metrics(cls, v: list[str]) -> list[str]:
        for m in v:
            if m not in AGGREGATE_METRICS:
                raise ValueError(f"unknown aggregate metric: {m}")
        return v

    @field_validator("group_by")
    @classmethod
    def _group_by(cls, v: list[str]) -> list[str]:
        for g in v:
            if g not in AGGREGATE_GROUPINGS:
                raise ValueError(f"unknown aggregate grouping: {g}")
        return v


class PurposeRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    description: str
    scope: Literal["patient", "aggregate"]
    allowed_roles: list[str]
    authorization: AuthorizationMode
    consent: ConsentMode
    fields: FieldRules = FieldRules()
    # Requesting any of these rejects the whole request (excessive scope).
    reject_if_requested: list[str] = []
    # Requesting more than this many hard-denied fields rejects instead of restricting.
    max_hard_denied_before_reject: int = Field(default=2, ge=0)
    # Allowed fields that need review when the encounter is in a sensitive category.
    sensitive_category_review_fields: list[str] = []
    aggregate: AggregateRules | None = None

    @field_validator("allowed_roles")
    @classmethod
    def _roles(cls, v: list[str]) -> list[str]:
        for r in v:
            if r not in ROLE_NAMES:
                raise ValueError(f"unknown role: {r}")
            if r in NEVER_REQUESTER_ROLES:
                raise ValueError(f"role {r} cannot be granted a data-request purpose (separation of duties)")
        if not v:
            raise ValueError("a purpose needs at least one allowed role")
        return v

    @model_validator(mode="after")
    def _shape(self) -> PurposeRules:
        for f in self.reject_if_requested + self.sensitive_category_review_fields:
            if f not in FIELD_CATALOG:
                raise ValueError(f"unknown field identifier in policy: {f}")
        if self.scope == "aggregate":
            if self.aggregate is None:
                raise ValueError("aggregate purposes need aggregate rules")
            if self.fields.allowed or self.fields.review_required:
                raise ValueError("aggregate purposes cannot release patient-level fields")
            if self.authorization != "reporting_mandate":
                raise ValueError("aggregate purposes require a reporting mandate")
        else:
            if self.aggregate is not None:
                raise ValueError("patient purposes cannot carry aggregate rules")
            # Sensitive notes may be reviewed but are never freely allowed.
            if "sensitive_notes" in self.fields.allowed:
                raise ValueError("sensitive_notes can never be in the allowed bucket")
        return self


class PolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    purposes: dict[str, PurposeRules]

    @field_validator("version")
    @classmethod
    def _version(cls, v: str) -> str:
        if not re.fullmatch(r"\d{4}\.\d{2}\.\d{1,4}", v):
            raise ValueError("version must look like YYYY.MM.N")
        return v

    @field_validator("purposes")
    @classmethod
    def _purpose_keys(cls, v: dict[str, PurposeRules]) -> dict[str, PurposeRules]:
        for k in v:
            if not re.fullmatch(r"[a-z][a-z_]{2,63}", k):
                raise ValueError(f"invalid purpose identifier: {k}")
        if not v:
            raise ValueError("policy must define at least one purpose")
        return v


INITIAL_POLICY = PolicyDocument.model_validate(
    {
        "version": "2026.10.1",
        "purposes": {
            "insurance_claim_assessment": {
                "label": "Insurance claim assessment",
                "description": (
                    "An insurer assesses a specific claim lodged with it. Requires a claim held by the "
                    "requesting insurer for this patient and the patient's recorded consent for this purpose."
                ),
                "scope": "patient",
                "allowed_roles": ["insurance_officer"],
                "authorization": "insurance_claim",
                "consent": "required",
                "fields": {
                    "allowed": [
                        "patient_reference",
                        "claim_reference",
                        "treatment_date",
                        "diagnosis_code",
                        "procedure_code",
                        "billing_amount",
                    ],
                    "review_required": ["diagnosis_description", "medication"],
                    "hard_denied": [
                        "clinician_notes",
                        "sensitive_notes",
                        "full_medical_history",
                        "age_band",
                        "sex",
                        "region",
                    ],
                },
                "reject_if_requested": ["full_medical_history"],
                "max_hard_denied_before_reject": 2,
                "sensitive_category_review_fields": ["diagnosis_code"],
            },
            "continuity_of_care": {
                "label": "Continuity of care",
                "description": (
                    "A treating hospital receives records for a patient referred to it. The legal basis is the "
                    "active referral (treatment relationship); a recorded refusal or withdrawal of consent blocks it."
                ),
                "scope": "patient",
                "allowed_roles": ["hospital_staff"],
                "authorization": "referral",
                "consent": "refusal_blocks",
                "fields": {
                    "allowed": [
                        "patient_reference",
                        "age_band",
                        "sex",
                        "treatment_date",
                        "diagnosis_code",
                        "diagnosis_description",
                        "procedure_code",
                        "medication",
                        "clinician_notes",
                    ],
                    "review_required": ["sensitive_notes", "full_medical_history"],
                    "hard_denied": ["billing_amount", "claim_reference"],
                },
                "reject_if_requested": [],
                "max_hard_denied_before_reject": 2,
                "sensitive_category_review_fields": [],
            },
            "public_health_reporting": {
                "label": "Public-health reporting",
                "description": (
                    "A government health agency receives aggregate statistics under an active reporting mandate. "
                    "No patient-level records are released; small groups are suppressed."
                ),
                "scope": "aggregate",
                "allowed_roles": ["government_officer"],
                "authorization": "reporting_mandate",
                "consent": "not_applicable",
                "fields": {
                    "allowed": [],
                    "review_required": [],
                    "hard_denied": list(FIELD_CATALOG.keys()),
                },
                "aggregate": {
                    "metrics": ["encounter_count", "distinct_patient_count"],
                    "group_by": ["diagnosis_category", "region", "age_band", "sex"],
                    "min_group_size": 5,
                    "max_group_dimensions": 1,
                    "max_period_days": 1100,
                    "secondary_suppression": True,
                },
            },
        },
    }
)
