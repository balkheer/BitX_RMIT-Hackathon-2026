"""Live smoke test of workflows A-F against a RUNNING stack (API + database), via the frontend's /api proxy.

TEST ENVIRONMENTS ONLY. It signs access tokens with a throwaway ES256 key. The API accepts them only because
the operator deliberately configured it to trust that key: an adapter file whose `jwk` block holds the test
public key, with issuer https://smoke-issuer.invalid/realms/smoke and resource consentbridge-web, and seeded
users linked to `smoke-<username>` subjects under that issuer. Production refuses non-HTTPS identity
providers; never point a production deployment at a test adapter.

Usage: python scripts/smoke_e2e.py <dir containing key.pem> [api_base_url]
Exit code 0 when every check passes.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import httpx
import jwt

ISSUER = "https://smoke-issuer.invalid/realms/smoke"
KEY = Path(sys.argv[1], "key.pem").read_text()
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:4173/api"
results: list[bool] = []


def token(user: str) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "sub": f"smoke-{user}", "azp": "consentbridge-web", "typ": "Bearer",
              "iat": now, "exp": now + 300}
    return jwt.encode(claims, KEY, algorithm="ES256", headers={"kid": "smoke"})


def call(user: str, method: str, path: str, **kw: Any) -> tuple[int, Any]:
    r = httpx.request(method, BASE + path, headers={"Authorization": f"Bearer {token(user)}"}, timeout=20, **kw)
    return r.status_code, r.json()


def check(name: str, cond: object, extra: object = "") -> None:
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name, extra)


def main() -> int:
    status, me = call("j.walker", "GET", "/me")
    check("A1 sign-in maps to insurer", status == 200 and me["role"] == "insurance_officer")

    fields = ["patient_reference", "claim_reference", "treatment_date", "diagnosis_code", "billing_amount"]
    body = {"purpose": "insurance_claim_assessment", "claim_reference": "CLM-M-1001", "requested_fields": fields,
            "justification": "Live smoke: assessing claim CLM-M-1001."}
    status, r = call("j.walker", "POST", "/requests", json=body)
    check("A2 valid claim approved", status == 201 and r["request"]["status"] == "approved")
    status, rel = call("j.walker", "POST", f"/requests/{r['request']['id']}/release")
    check("A3 release returns only approved fields", status == 200 and set(rel["records"][0]) == set(fields),
          rel.get("records"))

    excessive = {**body, "requested_fields": ["patient_reference", "full_medical_history", "sensitive_notes"],
                 "justification": "Live smoke: excessive request."}
    status, r = call("j.walker", "POST", "/requests", json=excessive)
    check("B1 excessive request rejected", r["request"]["status"] == "rejected", r["request"]["policy_codes"])
    status, _ = call("j.walker", "POST", f"/requests/{r['request']['id']}/release")
    check("B2 nothing releasable", status == 409)

    sensitive = {**body, "claim_reference": "CLM-M-1002",
                 "requested_fields": ["patient_reference", "diagnosis_code", "medication", "clinician_notes"],
                 "justification": "Live smoke: sensitive claim."}
    status, r = call("j.walker", "POST", "/requests", json=sensitive)
    pid = r["request"]["id"]
    check("E1 sensitive request pending", r["request"]["status"] == "pending_review", r["request"]["review_fields"])
    status, _ = call("j.walker", "POST", f"/requests/{pid}/release")
    check("E2 no release before review", status == 409)
    status, _ = call("a.patel", "POST", f"/reviews/{pid}",
                     json={"decision": "approve", "justification": "Wrong custodian should be blocked."})
    check("E3 out-of-scope reviewer blocked", status == 403)
    status, r = call("m.chen", "POST", f"/reviews/{pid}", json={
        "decision": "approve", "justification": "Specialist episode is covered; codes and medication needed."})
    check("E4 reviewer approves, hard-deny stays", status == 200 and "clinician_notes" in r["request"]["denied_fields"],
          r["request"]["status"])
    status, rel = call("j.walker", "POST", f"/requests/{pid}/release")
    record = rel["records"][0] if status == 200 else {}
    check("E5 reviewed fields released", "medication" in record and "clinician_notes" not in record)

    agg = {"purpose": "public_health_reporting", "mandate_reference": "MAN-PH-2026",
           "justification": "Live smoke: surveillance.",
           "aggregate": {"metric": "encounter_count", "group_by": ["region"],
                         "period_start": "2025-01-01", "period_end": "2026-09-30"}}
    status, r = call("t.brennan", "POST", "/requests", json=agg)
    status, rel = call("t.brennan", "POST", f"/requests/{r['request']['id']}/release")
    suppressed = {g["group"]["region"]: g["suppressed"] for g in rel["aggregate"]["groups"]}
    check("C1 aggregate with suppression",
          rel["records"] is None and suppressed.get("Inland") and suppressed.get("Coastal"), suppressed)

    status, patients = call("h.nguyen", "GET", "/patients")
    status, _ = call("j.walker", "GET", f"/patients/{patients[0]['id']}")
    check("D1 insurer reading a patient record is 403", status == 403)

    status, v = call("m.chen", "POST", "/audit/verify")
    check("F1 audit chain verifies", status == 200 and v["valid"], f"{v.get('checked_events')} events")
    check("Auth: no token is 401", httpx.get(BASE + "/me", timeout=10).status_code == 401)

    print("ALL PASS" if all(results) else "SOME FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
