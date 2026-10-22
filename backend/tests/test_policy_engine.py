"""Unit tests for the pure policy engine and policy-document validation (no DB, no HTTP)."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from app.policy.document import INITIAL_POLICY, PolicyDocument
from app.policy.engine import (
    AggregateQuery,
    AggregateScope,
    ConsentFacts,
    EvaluationContext,
    PatientScope,
    Principal,
    evaluate,
)

INSURER = Principal("u1", "insurance_officer", "org-ins", "insurer", True, True)
HOSPITAL = Principal("u2", "hospital_staff", "org-h2", "hospital", True, True)
GOV = Principal("u3", "government_officer", "org-gov", "government", True, True)
GRANTED = ConsentFacts(status="granted", reference="CNS-1")


def claim_ctx(fields, *, consent=GRANTED, sensitive=False, claim_valid=True, review=False, principal=INSURER):
    return EvaluationContext(
        principal=principal, purpose="insurance_claim_assessment", requested_fields=tuple(fields),
        policy=INITIAL_POLICY, consent=consent, review_approved=review,
        patient=PatientScope(patient_found=True, custodian_org_id="org-h1", encounter_sensitive=sensitive,
                             claim_valid=claim_valid),
    )


def agg_ctx(metric="encounter_count", group_by=("region",), mandate=True, fields=(),
            start=dt.date(2025, 1, 1), end=dt.date(2025, 12, 31), principal=GOV):
    return EvaluationContext(
        principal=principal, purpose="public_health_reporting", requested_fields=tuple(fields),
        policy=INITIAL_POLICY,
        aggregate=AggregateScope(AggregateQuery(metric, tuple(group_by), start, end), mandate_valid=mandate),
    )


class TestInsuranceClaims:
    def test_minimal_claim_fields_are_approved(self):
        r = evaluate(claim_ctx(["patient_reference", "treatment_date", "diagnosis_code", "billing_amount"]))
        assert r.status == "approved"
        assert r.approved_fields == ["patient_reference", "treatment_date", "diagnosis_code", "billing_amount"]
        assert r.denied_fields == [] and not r.review_required
        assert {"AUTHZ_CLAIM_VERIFIED", "CONSENT_VERIFIED"} <= set(r.policy_codes)
        assert r.policy_version == INITIAL_POLICY.version and r.evaluated_at.tzinfo is not None

    def test_unneeded_fields_are_restricted_not_released(self):
        r = evaluate(claim_ctx(["patient_reference", "billing_amount", "clinician_notes"]))
        assert r.status == "approved_with_restrictions"
        assert r.approved_fields == ["patient_reference", "billing_amount"]
        assert r.denied_fields == ["clinician_notes"]
        assert r.field_decisions["clinician_notes"] == "denied:hard_deny"

    def test_full_history_rejects_whole_request(self):
        r = evaluate(claim_ctx(["patient_reference", "full_medical_history"]))
        assert r.status == "rejected" and r.approved_fields == []
        assert "EXCESSIVE_SCOPE" in r.policy_codes

    def test_too_many_hard_denied_fields_rejects(self):
        r = evaluate(claim_ctx(["billing_amount", "clinician_notes", "sensitive_notes", "region"]))
        assert r.status == "rejected" and "EXCESSIVE_SCOPE" in r.policy_codes

    @pytest.mark.parametrize("status", ["none", "withdrawn", "refused"])
    def test_consent_required_for_claims(self, status):
        r = evaluate(claim_ctx(["billing_amount"], consent=ConsentFacts(status=status)))
        assert r.status == "rejected" and "CONSENT_NOT_AVAILABLE" in r.policy_codes

    def test_expired_consent_rejects(self):
        r = evaluate(claim_ctx(["billing_amount"], consent=ConsentFacts(status="granted", expired=True)))
        assert r.status == "rejected"

    def test_consent_exclusions_deny_field(self):
        consent = ConsentFacts(status="granted", excluded_fields=("procedure_code",))
        r = evaluate(claim_ctx(["billing_amount", "procedure_code"], consent=consent))
        assert r.status == "approved_with_restrictions"
        assert r.field_decisions["procedure_code"] == "denied:consent_excluded"

    def test_claim_must_belong_to_requester(self):
        r = evaluate(claim_ctx(["billing_amount"], claim_valid=False))
        assert r.status == "rejected" and r.policy_codes == ["AUTHZ_CLAIM_INVALID"]

    def test_review_required_fields_go_pending(self):
        r = evaluate(claim_ctx(["billing_amount", "medication"]))
        assert r.status == "pending_review" and r.review_fields == ["medication"]
        assert r.approved_fields == ["billing_amount"]  # held, not released, until review

    def test_sensitive_category_puts_codes_under_review(self):
        r = evaluate(claim_ctx(["diagnosis_code", "billing_amount"], sensitive=True))
        assert r.status == "pending_review" and r.review_fields == ["diagnosis_code"]
        assert "REVIEW_SENSITIVE_CATEGORY" in r.policy_codes

    def test_review_cannot_override_hard_denials(self):
        r = evaluate(claim_ctx(["billing_amount", "medication", "clinician_notes"], review=True))
        assert r.status == "approved_with_restrictions"
        assert "medication" in r.approved_fields and "clinician_notes" in r.denied_fields
        r2 = evaluate(claim_ctx(["full_medical_history"], review=True))
        assert r2.status == "rejected"

    def test_unknown_field_fails_closed(self):
        r = evaluate(claim_ctx(["billing_amount", "ssn"]))
        assert r.status == "rejected" and "FIELD_UNKNOWN" in r.policy_codes and r.approved_fields == []

    def test_no_fields(self):
        assert evaluate(claim_ctx([])).status == "rejected"


class TestIdentityAndRole:
    def test_inactive_user_rejected(self):
        p = Principal("u1", "insurance_officer", "o", "insurer", False, True)
        assert evaluate(claim_ctx(["billing_amount"], principal=p)).policy_codes == ["IDENTITY_INACTIVE"]

    def test_inactive_org_rejected(self):
        p = Principal("u1", "insurance_officer", "o", "insurer", True, False)
        assert evaluate(claim_ctx(["billing_amount"], principal=p)).status == "rejected"

    def test_wrong_role_for_purpose(self):
        r = evaluate(claim_ctx(["billing_amount"], principal=HOSPITAL))
        assert r.policy_codes == ["ROLE_NOT_PERMITTED_FOR_PURPOSE"]

    def test_role_org_type_mismatch(self):
        p = Principal("u1", "insurance_officer", "o", "hospital", True, True)
        assert evaluate(claim_ctx(["billing_amount"], principal=p)).policy_codes == ["ORG_TYPE_MISMATCH"]

    @pytest.mark.parametrize("role", ["platform_admin", "privacy_reviewer"])
    def test_admins_and_reviewers_cannot_request(self, role):
        p = Principal("u1", role, "o", "platform", True, True)
        assert evaluate(claim_ctx(["billing_amount"], principal=p)).status == "rejected"

    def test_unknown_purpose(self):
        ctx = EvaluationContext(principal=INSURER, purpose="marketing", requested_fields=("sex",),
                                policy=INITIAL_POLICY)
        assert evaluate(ctx).policy_codes == ["PURPOSE_UNKNOWN"]


class TestContinuityOfCare:
    def ctx(self, fields, consent=ConsentFacts(status="none"), referral=True, review=False):
        return EvaluationContext(
            principal=HOSPITAL, purpose="continuity_of_care", requested_fields=tuple(fields), policy=INITIAL_POLICY,
            consent=consent, review_approved=review,
            patient=PatientScope(patient_found=True, custodian_org_id="org-h1", referral_valid=referral))

    def test_referral_is_legal_basis_without_consent_record(self):
        r = evaluate(self.ctx(["diagnosis_code", "medication"]))
        assert r.status == "approved" and "LEGAL_BASIS_REFERRAL" in r.policy_codes

    def test_refusal_blocks(self):
        r = evaluate(self.ctx(["diagnosis_code"], consent=ConsentFacts(status="refused")))
        assert r.status == "rejected" and "CONSENT_REFUSED" in r.policy_codes

    def test_missing_referral(self):
        assert evaluate(self.ctx(["diagnosis_code"], referral=False)).policy_codes == ["AUTHZ_REFERRAL_INVALID"]

    def test_billing_not_needed_for_care(self):
        r = evaluate(self.ctx(["diagnosis_code", "billing_amount"]))
        assert r.status == "approved_with_restrictions" and r.denied_fields == ["billing_amount"]


class TestAggregate:
    def test_valid_aggregate(self):
        r = evaluate(agg_ctx())
        assert r.status == "approved" and r.min_group_size == 5
        assert "AGGREGATE_ONLY" in r.policy_codes and r.approved_fields == []

    def test_patient_fields_never_allowed(self):
        r = evaluate(agg_ctx(fields=["patient_reference"]))
        assert r.status == "rejected" and r.policy_codes == ["PATIENT_LEVEL_NOT_PERMITTED"]

    def test_multi_dimension_grouping_rejected(self):
        assert evaluate(agg_ctx(group_by=("region", "sex"))).policy_codes == ["AGGREGATE_QUERY_NOT_PERMITTED"]

    def test_period_limit(self):
        r = evaluate(agg_ctx(start=dt.date(2020, 1, 1), end=dt.date(2026, 1, 1)))
        assert r.status == "rejected"

    def test_mandate_required(self):
        assert evaluate(agg_ctx(mandate=False)).policy_codes == ["AUTHZ_MANDATE_INVALID"]

    def test_floor_raises_threshold(self):
        ctx = agg_ctx()
        ctx = EvaluationContext(**{**ctx.__dict__, "min_group_size_floor": 11})
        assert evaluate(ctx).min_group_size == 11

    def test_insurer_cannot_run_aggregate(self):
        assert evaluate(agg_ctx(principal=INSURER)).status == "rejected"


def test_evaluation_errors_fail_closed(monkeypatch):
    import app.policy.engine as eng

    def boom(*a, **k):
        raise RuntimeError("simulated fault")

    monkeypatch.setattr(eng, "_evaluate_patient", boom)
    r = evaluate(claim_ctx(["billing_amount"]))
    assert r.status == "rejected" and r.policy_codes == ["EVALUATION_ERROR"] and r.approved_fields == []


class TestPolicyDocumentValidation:
    def doc(self):
        return INITIAL_POLICY.model_dump(mode="json")

    def test_initial_policy_valid(self):
        PolicyDocument.model_validate(self.doc())

    def test_threshold_below_five_rejected(self):
        d = self.doc()
        d["purposes"]["public_health_reporting"]["aggregate"]["min_group_size"] = 3
        with pytest.raises(ValidationError):
            PolicyDocument.model_validate(d)

    def test_sensitive_notes_cannot_be_freely_allowed(self):
        d = self.doc()
        d["purposes"]["continuity_of_care"]["fields"]["review_required"].remove("sensitive_notes")
        d["purposes"]["continuity_of_care"]["fields"]["allowed"].append("sensitive_notes")
        with pytest.raises(ValidationError):
            PolicyDocument.model_validate(d)

    def test_field_in_two_buckets_rejected(self):
        d = self.doc()
        d["purposes"]["insurance_claim_assessment"]["fields"]["allowed"].append("clinician_notes")
        with pytest.raises(ValidationError):
            PolicyDocument.model_validate(d)

    def test_unknown_field_rejected(self):
        d = self.doc()
        d["purposes"]["insurance_claim_assessment"]["fields"]["allowed"].append("ssn")
        with pytest.raises(ValidationError):
            PolicyDocument.model_validate(d)

    def test_admin_cannot_be_granted_a_purpose(self):
        d = self.doc()
        d["purposes"]["insurance_claim_assessment"]["allowed_roles"].append("platform_admin")
        with pytest.raises(ValidationError):
            PolicyDocument.model_validate(d)

    def test_aggregate_purpose_cannot_release_fields(self):
        d = self.doc()
        d["purposes"]["public_health_reporting"]["fields"] = {"allowed": ["sex"], "review_required": [],
                                                              "hard_denied": []}
        with pytest.raises(ValidationError):
            PolicyDocument.model_validate(d)

    def test_extra_keys_rejected(self):
        d = self.doc()
        d["purposes"]["insurance_claim_assessment"]["bypass"] = True
        with pytest.raises(ValidationError):
            PolicyDocument.model_validate(d)
