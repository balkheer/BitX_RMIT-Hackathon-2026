"""End-to-end workflows A-F through the real API and database."""

from __future__ import annotations

import json

from sqlalchemy import text

from tests.conftest import audit_rows, auth, make_claim, owner_engine, set_consent

SECRET_MARKERS = ("NOTE-SECRET", "SENSITIVE-SECRET", "SECRET-MED")


def claim_body(ref, fields, justification="Assessing the lodged claim for cover."):
    return {"purpose": "insurance_claim_assessment", "claim_reference": ref, "requested_fields": fields,
            "justification": justification}


def create(client, user, body):
    r = client.post("/api/requests", headers=auth(user), json=body)
    assert r.status_code == 201, r.text
    return r.json()["request"]


def assert_no_secrets(text_blob: str):
    for m in SECRET_MARKERS:
        assert m not in text_blob, m


# ------------------------------------------------------------------ Workflow A
def test_workflow_a_valid_claim_releases_only_approved_fields(client):
    ref = make_claim()
    fields = ["patient_reference", "claim_reference", "treatment_date", "diagnosis_code", "billing_amount"]
    preview = client.post("/api/requests/preview", headers=auth("j.walker"), json=claim_body(ref, fields))
    assert preview.status_code == 200 and preview.json()["decision"]["status"] == "approved"

    req = create(client, "j.walker", claim_body(ref, fields))
    assert req["status"] == "approved" and req["approved_fields"] == fields and req["can_release"]

    rel = client.post(f"/api/requests/{req['id']}/release", headers=auth("j.walker"))
    assert rel.status_code == 200
    body = rel.json()
    assert body["released_fields"] == fields
    assert len(body["records"]) == 1
    rec = body["records"][0]
    assert set(rec) == set(fields)
    assert rec["claim_reference"] == ref and rec["billing_amount"] == "5123.45"
    assert body["reevaluation"]["status"] == "approved"
    assert_no_secrets(rel.text)

    actions = [e["action"] for e in audit_rows(req["id"])]
    assert actions[:2] == ["REQUEST_SUBMITTED", "POLICY_EVALUATED"] and "DATA_RELEASED" in actions
    released = next(e for e in audit_rows(req["id"]) if e["action"] == "DATA_RELEASED")
    assert released["details"]["released_fields"] == fields
    assert "5123.45" not in json.dumps(released["details"])  # identifiers only, never values


# ------------------------------------------------------------------ Workflow B
def test_workflow_b_excessive_request_rejected(client):
    ref = make_claim()
    req = create(client, "j.walker", claim_body(ref, ["patient_reference", "full_medical_history", "clinician_notes"]))
    assert req["status"] == "rejected" and "EXCESSIVE_SCOPE" in req["policy_codes"]
    assert req["approved_fields"] == []
    r = client.post(f"/api/requests/{req['id']}/release", headers=auth("j.walker"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_RELEASABLE"
    assert_no_secrets(r.text)
    assert any(e["action"] == "RELEASE_BLOCKED" for e in audit_rows(req["id"]))


def test_workflow_b_overreach_is_restricted_and_denied_values_never_returned(client):
    ref = make_claim()
    req = create(client, "j.walker", claim_body(ref, ["patient_reference", "billing_amount", "clinician_notes"]))
    assert req["status"] == "approved_with_restrictions"
    assert req["denied_fields"] == ["clinician_notes"]
    rel = client.post(f"/api/requests/{req['id']}/release", headers=auth("j.walker"))
    assert rel.status_code == 200
    rec = rel.json()["records"][0]
    assert "clinician_notes" not in rec and set(rec) == {"patient_reference", "billing_amount"}
    assert rel.json()["withheld_fields"] == ["clinician_notes"]
    detail = client.get(f"/api/requests/{req['id']}", headers=auth("j.walker"))
    assert_no_secrets(rel.text + detail.text + json.dumps([e["details"] for e in audit_rows(req["id"])]))


def test_consent_excluded_field_is_withheld(client):
    ref = make_claim(excluded=["procedure_code"])
    req = create(client, "j.walker", claim_body(ref, ["billing_amount", "procedure_code"]))
    assert req["status"] == "approved_with_restrictions"
    assert req["field_decisions"]["procedure_code"] == "denied:consent_excluded"


def test_missing_consent_rejects(client):
    ref = make_claim(consent_status=None)
    req = create(client, "j.walker", claim_body(ref, ["billing_amount"]))
    assert req["status"] == "rejected" and "CONSENT_NOT_AVAILABLE" in req["policy_codes"]


def test_unknown_fields_are_rejected_by_validation(client):
    ref = make_claim()
    r = client.post("/api/requests", headers=auth("j.walker"), json=claim_body(ref, ["billing_amount", "dna_seq"]))
    assert r.status_code == 422 and "dna_seq" not in r.text


# ------------------------------------------------------------------ Workflow C
def agg_body(group_by, metric="encounter_count", mandate="MAN-PH-2026", start="2025-01-01", end="2026-09-30",
             fields=None):
    b = {"purpose": "public_health_reporting", "mandate_reference": mandate,
         "aggregate": {"metric": metric, "group_by": group_by, "period_start": start, "period_end": end},
         "justification": "Quarterly surveillance reporting under mandate."}
    if fields is not None:
        b["requested_fields"] = fields
    return b


def test_workflow_c_aggregate_with_small_group_suppression(client):
    req = create(client, "t.brennan", agg_body(["diagnosis_category"]))
    assert req["status"] == "approved" and req["scope_type"] == "aggregate"
    assert req["patient_reference"] is None
    rel = client.post(f"/api/requests/{req['id']}/release", headers=auth("t.brennan"))
    assert rel.status_code == 200
    body = rel.json()
    assert body["records"] is None and body["released_fields"] == []
    agg = body["aggregate"]
    assert agg["min_group_size"] == 5
    groups = {g["group"]["diagnosis_category"]: g for g in agg["groups"]}
    for small in ("mental_health", "sexual_health"):
        assert groups[small]["suppressed"] and groups[small]["value"] is None
    for g in agg["groups"]:
        if not g["suppressed"]:
            assert g["value"] >= 5
    # No patient-level identifiers anywhere in the response.
    assert "SYN-P-" not in rel.text and "ENC-" not in rel.text
    ev = next(e for e in audit_rows(req["id"]) if e["action"] == "DATA_RELEASED")
    assert ev["details"]["suppressed_groups"] >= 2


def test_secondary_suppression_protects_lone_small_cell(client):
    # Seeded regions in the period: North 13, Central 11, Coastal 9, Inland 3.
    req = create(client, "t.brennan", agg_body(["region"]))
    agg = client.post(f"/api/requests/{req['id']}/release", headers=auth("t.brennan")).json()["aggregate"]
    by = {g["group"]["region"]: g for g in agg["groups"]}
    assert by["Inland"]["suppressed"] and by["Inland"]["suppression_reason"] == "Fewer than 5"
    # Inland alone could be derived from a total, so the next-smallest cell is also suppressed.
    assert by["Coastal"]["suppressed"] and by["Coastal"]["suppression_reason"] == "Complementary suppression"
    assert agg["secondary_suppressions"] == 1
    assert by["North"]["value"] == 13 and by["Central"]["value"] == 11


def test_aggregate_cannot_request_patient_fields(client):
    req = create(client, "t.brennan", agg_body(["region"], fields=["patient_reference"]))
    assert req["status"] == "rejected" and req["policy_codes"] == ["PATIENT_LEVEL_NOT_PERMITTED"]


def test_expired_mandate_rejected(client):
    req = create(client, "t.brennan", agg_body(["region"], mandate="MAN-PH-2024"))
    assert req["status"] == "rejected" and "AUTHZ_MANDATE_INVALID" in req["policy_codes"]


def test_multi_dimension_grouping_rejected(client):
    req = create(client, "t.brennan", agg_body(["region", "sex"]))
    assert req["status"] == "rejected"


def test_non_government_cannot_query_aggregates(client):
    r = client.post("/api/requests", headers=auth("j.walker"), json=agg_body(["region"]))
    assert r.status_code == 403


# ------------------------------------------------------------------ Workflow D
def test_workflow_d_unauthorized_access_logged_without_sensitive_data(client):
    with owner_engine().connect() as c:
        pid = str(c.execute(text("SELECT id FROM patients WHERE synthetic_ref='SYN-P-0001'")).scalar_one())
    token_headers = auth("j.walker")
    r = client.get(f"/api/patients/{pid}", headers=token_headers)
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    ev = audit_rows()[-1]
    assert ev["action"] == "ACCESS_DENIED" and ev["outcome"] == "denied"
    blob = json.dumps({k: str(v) for k, v in ev.items()})
    assert token_headers["Authorization"].split()[1] not in blob
    assert "Aster" not in blob


# ------------------------------------------------------------------ Workflow E
def test_workflow_e_sensitive_review(client):
    ref = make_claim(sensitive=True)  # custodian: St Aurelia
    req = create(client, "j.walker", claim_body(ref, ["patient_reference", "diagnosis_code", "medication",
                                                       "billing_amount", "clinician_notes"]))
    assert req["status"] == "pending_review"
    assert set(req["review_fields"]) == {"diagnosis_code", "medication"}
    assert req["denied_fields"] == ["clinician_notes"]
    rid = req["id"]

    # Nothing is released before a decision.
    r = client.post(f"/api/requests/{rid}/release", headers=auth("j.walker"))
    assert r.status_code == 409 and "SECRET" not in r.text

    queue = client.get("/api/reviews/queue", headers=auth("m.chen")).json()
    assert any(q["id"] == rid for q in queue)
    detail = client.get(f"/api/requests/{rid}", headers=auth("m.chen")).json()
    assert detail["can_review"] and detail["encounter_sensitive"] is True
    assert_no_secrets(json.dumps(detail))

    # Reviewer at a different custodian cannot decide.
    other = client.post(f"/api/reviews/{rid}", headers=auth("a.patel"),
                        json={"decision": "approve", "justification": "Looks fine to me, approving now."})
    assert other.status_code == 403
    # Justification is mandatory and substantive.
    short = client.post(f"/api/reviews/{rid}", headers=auth("m.chen"), json={"decision": "approve",
                                                                            "justification": "ok"})
    assert short.status_code == 422
    # Requester cannot review.
    assert client.post(f"/api/reviews/{rid}", headers=auth("j.walker"), json={
        "decision": "approve", "justification": "Approving my own request here."}).status_code == 403

    ok = client.post(f"/api/reviews/{rid}", headers=auth("m.chen"), json={
        "decision": "approve",
        "justification": "Claim relates to a covered specialist episode; diagnosis and medication are necessary."})
    assert ok.status_code == 200, ok.text
    after = ok.json()["request"]
    # Hard-denied field stays denied even after approval.
    assert after["status"] == "approved_with_restrictions"
    assert "clinician_notes" in after["denied_fields"]
    assert {"diagnosis_code", "medication"} <= set(after["approved_fields"])
    assert after["reviews"][0]["reviewer_name"] == "Mei Chen"
    assert after["reviews"][0]["justification"].startswith("Claim relates")

    dup = client.post(f"/api/reviews/{rid}", headers=auth("m.chen"), json={
        "decision": "reject", "justification": "Changing my mind after approval."})
    assert dup.status_code == 409

    rel = client.post(f"/api/requests/{rid}/release", headers=auth("j.walker"))
    assert rel.status_code == 200
    rec = rel.json()["records"][0]
    assert rec["medication"] == "Cefazolin 2g SECRET-MED"  # reviewed field now released
    assert "clinician_notes" not in rec
    actions = [e["action"] for e in audit_rows(rid)]
    assert "REVIEW_DECIDED" in actions and actions.count("POLICY_EVALUATED") >= 2


def test_review_rejection_records_reviewer_and_reason(client):
    ref = make_claim()
    req = create(client, "j.walker", claim_body(ref, ["billing_amount", "diagnosis_description"]))
    assert req["status"] == "pending_review"
    r = client.post(f"/api/reviews/{req['id']}", headers=auth("m.chen"), json={
        "decision": "reject", "justification": "Diagnosis narrative is not needed to assess this claim type."})
    out = r.json()["request"]
    assert out["status"] == "rejected" and "REVIEW_REJECTED" in out["policy_codes"]
    with owner_engine().connect() as c:
        row = c.execute(text("SELECT rd.decision, rd.justification, u.username FROM review_decisions rd "
                             "JOIN users u ON u.id = rd.reviewer_user_id WHERE rd.request_id=:r"),
                        {"r": req["id"]}).one()
    assert row.decision == "reject" and row.username == "m.chen" and row.justification.startswith("Diagnosis")
    assert client.post(f"/api/requests/{req['id']}/release", headers=auth("j.walker")).status_code == 409


def test_separation_of_duties_same_org_reviewer(client):
    # A continuity request from Harbourview about a St Aurelia patient: Harbourview's reviewer may not decide.
    r = create(client, "r.okafor", {"purpose": "continuity_of_care", "referral_reference": "REF-3001",
                                    "requested_fields": ["patient_reference", "sensitive_notes"],
                                    "justification": "Need psychosocial history for referral."})
    assert r["status"] == "pending_review"
    resp = client.post(f"/api/reviews/{r['id']}", headers=auth("a.patel"), json={
        "decision": "approve", "justification": "Approving a request from my own organisation."})
    assert resp.status_code == 403
    assert any(e["policy_codes"] == ["REVIEW_OUT_OF_SCOPE"] or e["policy_codes"] == ["SEPARATION_OF_DUTIES"]
               for e in audit_rows()[-3:])


def test_reauthorization_at_release_blocks_after_consent_withdrawal(client):
    ref = make_claim()
    req = create(client, "j.walker", claim_body(ref, ["billing_amount"]))
    assert req["status"] == "approved"
    set_consent(ref, "withdrawn")
    r = client.post(f"/api/requests/{req['id']}/release", headers=auth("j.walker"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "REAUTHORIZATION_FAILED"
    assert "5123" not in r.text
    after = client.get(f"/api/requests/{req['id']}", headers=auth("j.walker")).json()
    assert after["status"] == "rejected" and "REEVALUATION_DENIED" in after["policy_codes"]
    assert any(e["action"] == "POLICY_REEVALUATED" and e["outcome"] == "denied" for e in audit_rows(req["id"]))


def test_reviewer_approval_reevaluates_current_conditions(client):
    ref = make_claim()
    req = create(client, "j.walker", claim_body(ref, ["billing_amount", "medication"]))
    assert req["status"] == "pending_review"
    set_consent(ref, "withdrawn")
    out = client.post(f"/api/reviews/{req['id']}", headers=auth("m.chen"), json={
        "decision": "approve", "justification": "Medication is relevant to the claimed treatment."}).json()["request"]
    assert out["status"] == "rejected" and "CONSENT_NOT_AVAILABLE" in out["policy_codes"]


def test_release_shrinks_when_consent_adds_exclusion(client):
    ref = make_claim()
    req = create(client, "j.walker", claim_body(ref, ["billing_amount", "procedure_code"]))
    assert req["status"] == "approved"
    with owner_engine().begin() as c:
        c.execute(text("UPDATE patient_consents SET excluded_fields='{procedure_code}' WHERE consent_reference=:r"),
                  {"r": f"CNS-{ref}"})
    rel = client.post(f"/api/requests/{req['id']}/release", headers=auth("j.walker")).json()
    assert rel["released_fields"] == ["billing_amount"] and "procedure_code" not in rel["records"][0]


# ------------------------------------------------------------------ Workflow F (API level)
def test_workflow_f_audit_verify(client):
    r = client.post("/api/audit/verify", headers=auth("ops.admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True and body["checked_events"] > 10 and len(body["head_hash"]) == 64
    assert "tamper-evident" in body["notice"]
    assert audit_rows()[-1]["action"] == "AUDIT_VERIFIED"
    page = client.get("/api/audit?limit=5", headers=auth("ops.admin")).json()
    assert all(e["hash_valid"] for e in page["items"])


# ------------------------------------------------------------------ dashboard & listing
def test_dashboard_is_scoped(client):
    walker = client.get("/api/dashboard", headers=auth("j.walker")).json()
    lim = client.get("/api/dashboard", headers=auth("s.lim")).json()
    admin = client.get("/api/dashboard", headers=auth("ops.admin")).json()
    with owner_engine().connect() as c:
        mer = c.execute(text("SELECT count(*) FROM data_requests r JOIN organizations o ON o.id=r.requester_org_id "
                             "WHERE o.slug='meridian-insurance'")).scalar_one()
        total = c.execute(text("SELECT count(*) FROM data_requests")).scalar_one()
    assert walker["total_requests"] == mer
    assert lim["total_requests"] < walker["total_requests"]
    assert admin["total_requests"] == total
    reviewer = client.get("/api/dashboard", headers=auth("m.chen")).json()
    assert reviewer["review_queue"] is not None


def test_request_filters(client):
    rows = client.get("/api/requests?status=rejected", headers=auth("j.walker")).json()
    assert rows and all(r["status"] == "rejected" for r in rows)
    rows = client.get("/api/requests?q=CLM-M-1001", headers=auth("j.walker")).json()
    assert rows and all(r["authorization_reference"] == "CLM-M-1001" for r in rows)
    assert client.get("/api/requests?status=bogus", headers=auth("j.walker")).status_code == 422


def test_reference_lists_are_org_scoped(client):
    claims = client.get("/api/claims", headers=auth("s.lim")).json()
    assert claims and all(c["claim_reference"].startswith("CLM-K") or c["claim_reference"].startswith("CLM-T")
                          for c in claims)
    refs = client.get("/api/referrals", headers=auth("r.okafor")).json()
    assert {r["referral_reference"] for r in refs} == {"REF-3001", "REF-3003"}
    mandates = client.get("/api/mandates", headers=auth("t.brennan")).json()
    assert {m["mandate_reference"] for m in mandates} == {"MAN-PH-2024", "MAN-PH-2026"}


def test_rate_limit_on_sensitive_endpoints(client, monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_enabled", True)
    monkeypatch.setattr(s, "rate_limit_sensitive_per_minute", 3)
    ref = make_claim()
    codes = [client.post("/api/requests/preview", headers=auth("j.walker"),
                         json=claim_body(ref, ["billing_amount"])).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429


def test_policy_change_workflow(client):
    pol = client.get("/api/policy", headers=auth("j.walker")).json()
    doc = pol["document"]
    bad = json.loads(json.dumps(doc))
    bad["version"] = "2026.10.2"
    bad["purposes"]["public_health_reporting"]["aggregate"]["min_group_size"] = 2
    r = client.post("/api/admin/policy", headers=auth("ops.admin"),
                    json={"document": bad, "change_note": "Lower threshold for testing"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "POLICY_INVALID"

    same_version = client.post("/api/admin/policy", headers=auth("ops.admin"),
                               json={"document": doc, "change_note": "Re-submit without a version bump"})
    assert same_version.status_code == 409

    good = json.loads(json.dumps(doc))
    good["version"] = "2026.10.2"
    good["purposes"]["insurance_claim_assessment"]["fields"]["review_required"].remove("medication")
    good["purposes"]["insurance_claim_assessment"]["fields"]["hard_denied"].append("medication")
    r = client.post("/api/admin/policy", headers=auth("ops.admin"),
                    json={"document": good, "change_note": "Insurers no longer receive medication details."})
    assert r.status_code == 200 and r.json()["version"] == "2026.10.2"
    ev = audit_rows()[-1]
    assert ev["action"] == "POLICY_CHANGED" and "insurance_claim_assessment.hard_denied +medication" in \
        ev["details"]["changes"]
    # New evaluations use the new version.
    ref = make_claim()
    req = create(client, "j.walker", claim_body(ref, ["billing_amount", "medication"]))
    assert req["policy_version"] == "2026.10.2" and "medication" in req["denied_fields"]

    # Restore original rules under a newer version so later tests see the initial policy.
    restore = json.loads(json.dumps(doc))
    restore["version"] = "2026.10.3"
    r = client.post("/api/admin/policy", headers=auth("ops.admin"),
                    json={"document": restore, "change_note": "Restore initial field rules after test."})
    assert r.status_code == 200
