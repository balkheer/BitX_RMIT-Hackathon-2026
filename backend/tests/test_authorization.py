"""Authorisation: 403s, organisation scoping, IDOR, mass assignment, separation of duties."""

from __future__ import annotations

import json

from sqlalchemy import text

from tests.conftest import audit_rows, auth, make_claim, owner_engine


def _patient_id(ref: str) -> str:
    with owner_engine().connect() as c:
        return str(c.execute(text("SELECT id FROM patients WHERE synthetic_ref=:r"), {"r": ref}).scalar_one())


def _request_id(number: int) -> str:
    with owner_engine().connect() as c:
        return str(c.execute(text("SELECT id FROM data_requests WHERE request_number=:n"), {"n": number}).scalar_one())


def claim_body(ref: str, fields: list[str], **extra):
    return {"purpose": "insurance_claim_assessment", "claim_reference": ref, "requested_fields": fields,
            "justification": "Assessing the lodged claim for cover.", **extra}


def test_hospital_sees_only_its_own_patients(client):
    r = client.get("/api/patients", headers=auth("h.nguyen"))
    assert r.status_code == 200
    refs = {p["synthetic_ref"] for p in r.json()}
    assert "SYN-P-0001" in refs and "SYN-P-0011" not in refs  # 0011 is held by Harbourview


def test_cross_org_patient_access_is_403_and_audited(client):
    pid = _patient_id("SYN-P-0011")
    r = client.get(f"/api/patients/{pid}", headers=auth("h.nguyen"))
    assert r.status_code == 403
    assert "SYN-P-0011" not in r.text and "Linden" not in r.text
    ev = audit_rows()[-1]
    assert ev["action"] == "ACCESS_DENIED" and ev["policy_codes"] == ["PATIENT_OUT_OF_SCOPE"]


def test_insurer_and_government_have_no_patient_directory(client):
    pid = _patient_id("SYN-P-0001")
    for user in ("j.walker", "t.brennan", "ops.admin", "m.chen"):
        assert client.get("/api/patients", headers=auth(user)).status_code == 403
        assert client.get(f"/api/patients/{pid}", headers=auth(user)).status_code == 403


def test_patient_view_withholds_sensitive_notes(client):
    pid = _patient_id("SYN-P-0002")  # has a mental-health encounter
    r = client.get(f"/api/patients/{pid}", headers=auth("h.nguyen"))
    assert r.status_code == 200
    assert "psychosocial" not in r.text
    assert any(e["has_sensitive_notes"] for e in r.json()["encounters"])


def test_insurer_cannot_read_another_insurers_request(client):
    rid = _request_id(1004)  # Kestrel's request
    r = client.get(f"/api/requests/{rid}", headers=auth("j.walker"))
    assert r.status_code == 403
    listing = client.get("/api/requests", headers=auth("j.walker")).json()
    assert all(x["requester_org"]["slug"] == "meridian-insurance" for x in listing)


def test_unknown_request_is_404(client):
    r = client.get("/api/requests/00000000-0000-4000-8000-000000000000", headers=auth("j.walker"))
    assert r.status_code == 404


def test_using_another_insurers_claim_is_403_and_not_persisted(client):
    before = client.get("/api/requests", headers=auth("j.walker")).json()
    r = client.post("/api/requests", headers=auth("j.walker"), json=claim_body("CLM-K-2001", ["billing_amount"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "AUTHORIZATION_REFERENCE_INVALID"
    # Same answer for a reference that does not exist at all (no existence oracle).
    r2 = client.post("/api/requests", headers=auth("j.walker"), json=claim_body("CLM-X-9999", ["billing_amount"]))
    assert r2.status_code == 403
    assert r2.json()["error"]["code"] == r.json()["error"]["code"]
    assert r2.json()["error"]["message"] == r.json()["error"]["message"]
    after = client.get("/api/requests", headers=auth("j.walker")).json()
    assert len(after) == len(before)


def test_purpose_not_permitted_for_role(client):
    r = client.post("/api/requests", headers=auth("j.walker"), json={
        "purpose": "continuity_of_care", "referral_reference": "REF-3001", "requested_fields": ["diagnosis_code"],
        "justification": "Trying a purpose not available to insurers."})
    assert r.status_code == 403 and r.json()["error"]["code"] == "PURPOSE_NOT_PERMITTED"


def test_reviewers_and_admins_cannot_create_requests(client):
    ref = make_claim()
    for user in ("m.chen", "ops.admin"):
        r = client.post("/api/requests", headers=auth(user), json=claim_body(ref, ["billing_amount"]))
        assert r.status_code == 403


def test_mass_assignment_is_rejected(client):
    ref = make_claim()
    for extra in ({"status": "approved"}, {"approved_fields": ["clinician_notes"]},
                  {"organization_id": "00000000-0000-0000-0000-000000000000"}, {"requester_user_id": "x"},
                  {"policy_version": "9999.01.1"}, {"review_required": False}):
        r = client.post("/api/requests", headers=auth("j.walker"), json=claim_body(ref, ["billing_amount"], **extra))
        assert r.status_code == 422, extra


def test_client_supplied_org_headers_are_ignored(client):
    ref = make_claim()
    r = client.post("/api/requests", headers={**auth("j.walker"), "X-Organization-Id": "kestrel-mutual"},
                    json=claim_body(ref, ["billing_amount"]))
    assert r.status_code == 201
    assert r.json()["request"]["requester_org"]["slug"] == "meridian-insurance"


def test_only_requester_can_release(client):
    ref = make_claim()
    rid = client.post("/api/requests", headers=auth("j.walker"),
                      json=claim_body(ref, ["billing_amount"])).json()["request"]["id"]
    for user in ("s.lim", "ops.admin", "m.chen", "h.nguyen"):
        r = client.post(f"/api/requests/{rid}/release", headers=auth(user))
        assert r.status_code in (403, 404), user
        assert "512345" not in r.text and "5123.45" not in r.text


def test_admin_sees_request_metadata_without_patient_identity(client):
    rows = client.get("/api/requests", headers=auth("ops.admin")).json()
    assert rows and all(r["patient_reference"] is None and r["patient_alias"] is None for r in rows)
    detail = client.get(f"/api/requests/{rows[0]['id']}", headers=auth("ops.admin")).json()
    assert detail["consent_reference"] is None


def test_admin_cannot_modify_self_or_other_admins(client):
    users = client.get("/api/admin/users", headers=auth("ops.admin")).json()
    me = next(u for u in users if u["username"] == "ops.admin")
    r = client.patch(f"/api/admin/users/{me['id']}", headers=auth("ops.admin"), json={"is_active": False})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SEPARATION_OF_DUTIES"


def test_admin_can_disable_and_reenable_user_with_audit(client):
    users = client.get("/api/admin/users", headers=auth("ops.admin")).json()
    target = next(u for u in users if u["username"] == "r.okafor")
    r = client.patch(f"/api/admin/users/{target['id']}", headers=auth("ops.admin"), json={"is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.get("/api/me", headers=auth("r.okafor")).status_code == 403
    client.patch(f"/api/admin/users/{target['id']}", headers=auth("ops.admin"), json={"is_active": True})
    assert client.get("/api/me", headers=auth("r.okafor")).status_code == 200
    assert any(e["action"] == "USER_UPDATED" for e in audit_rows()[-6:])


def test_admin_cannot_create_admin_via_api(client):
    r = client.post("/api/admin/users", headers=auth("ops.admin"), json={
        "username": "evil.admin", "display_name": "Evil", "organization_slug": "consentbridge-platform",
        "role": "platform_admin"})
    assert r.status_code == 422


def test_admin_role_org_mismatch_rejected(client):
    r = client.post("/api/admin/users", headers=auth("ops.admin"), json={
        "username": "odd.user", "display_name": "Odd", "organization_slug": "meridian-insurance",
        "role": "hospital_staff"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "ROLE_ORG_MISMATCH"


def test_non_admin_admin_endpoints_403(client):
    for user in ("j.walker", "m.chen", "h.nguyen", "t.brennan"):
        assert client.get("/api/admin/users", headers=auth(user)).status_code == 403
        assert client.post("/api/admin/policy", headers=auth(user),
                           json={"document": {}, "change_note": "attempted change"}).status_code == 403


def test_review_queue_requires_reviewer_role(client):
    for user in ("j.walker", "h.nguyen", "ops.admin", "t.brennan"):
        assert client.get("/api/reviews/queue", headers=auth(user)).status_code == 403


def test_audit_verify_restricted(client):
    for user in ("j.walker", "h.nguyen", "t.brennan"):
        assert client.post("/api/audit/verify", headers=auth(user)).status_code == 403


def test_audit_listing_is_org_scoped(client):
    page = client.get("/api/audit?limit=200", headers=auth("s.lim")).json()
    with owner_engine().connect() as c:
        kestrel = str(c.execute(text("SELECT id FROM organizations WHERE slug='kestrel-mutual'")).scalar_one())
    for e in page["items"]:
        row = next(r for r in audit_rows() if r["seq"] == e["seq"])
        assert kestrel in [str(o) for o in row["related_org_ids"]]


def test_unknown_api_path_is_404_json(client):
    r = client.get("/api/everything", headers=auth("j.walker"))
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_validation_errors_do_not_echo_input(client):
    r = client.post("/api/requests", headers=auth("j.walker"), json=claim_body(
        "CLM-M-1001", ["billing_amount", "<script>alert(1)</script>"]))
    assert r.status_code == 422
    assert "<script>" not in r.text
    assert "unknown field identifier" in json.dumps(r.json())
