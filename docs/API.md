# API reference

Base path `/api`. JSON in and out. Machine-readable schema: [openapi.json](openapi.json) (also served at
`/api/docs` outside production).

## Conventions

- **Authentication.** Every `/api` route requires `Authorization: Bearer <TideCloak access token>`.
  Missing, malformed, expired, wrongly-issued or wrongly-signed tokens → `401 INVALID_TOKEN`.
  A valid token whose subject isn't linked → `403 IDENTITY_NOT_MAPPED`. Disabled user or organisation →
  `403 ACCOUNT_DISABLED`. Identity provider unreachable → `503 AUTH_PROVIDER_UNAVAILABLE`.
- **Authorisation** is enforced in every route. Lacking permission → `403`, and the attempt is audited.
- **Errors** share one shape and never contain patient values, tokens or stack traces:

  ```json
  { "error": { "code": "FORBIDDEN", "message": "You do not have access to this request.",
               "correlation_id": "7c1e…", "details": { } } }
  ```

- **Validation.** Input models forbid unknown keys (mass-assignment protection); unknown field identifiers
  are rejected without echoing them; responses to `422` list locations and messages only.
- **Rate limits.** Create, preview, release, review, audit verification and admin writes are limited per
  user (default 30/minute). Exceeding returns `429 RATE_LIMITED` with `Retry-After`.
- Every response carries `X-Correlation-Id`, `Cache-Control: no-store` and security headers.

## Endpoints

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/healthz` | anyone | Liveness |
| GET | `/readyz` | anyone | Readiness: database, active policy, auth configured (503 if not) |
| GET | `/me` | any linked user | Current user, organisation, role, permission list |
| GET | `/dashboard` | any linked user | Status counts, release count, review queue size, recent visible audit events |
| GET | `/requests` | any linked user | Visible requests. Filters: `status`, `purpose`, `q` (CB number, reference, patient), `limit` |
| POST | `/requests/preview` | requester roles | Evaluate without storing (audited as `POLICY_PREVIEWED`) |
| POST | `/requests` | requester roles | Create and evaluate a request |
| GET | `/requests/{id}` | visibility-scoped | Details, field decisions, reviews, releases, request audit events |
| POST | `/requests/{id}/release` | the original requester | Re-authorise and release approved fields or aggregate results |
| GET | `/reviews/queue` | privacy reviewer | Pending requests for records held by the reviewer's organisation, excluding its own |
| GET | `/reviews/history` | privacy reviewer | Reviewed requests for the organisation |
| POST | `/reviews/{id}` | privacy reviewer | `{decision: approve|reject, justification (≥20 chars)}` |
| GET | `/patients` | hospital staff | Patients whose records the caller's hospital holds |
| GET | `/patients/{id}` | hospital staff of the custodian | Encounters (sensitive notes withheld), consents. Audited |
| GET | `/claims` | insurance officer | Claims lodged with the caller's insurer |
| GET | `/referrals` | hospital staff | Referrals addressed to the caller's hospital |
| GET | `/mandates` | government officer | The agency's reporting mandates |
| GET | `/releases` | requester roles | The caller's requests with releasable data |
| GET | `/audit` | any linked user | Paged events; admins see all, others see events involving their organisation. Filters: `action`, `outcome`, `request_id` |
| POST | `/audit/verify` | admin, privacy reviewer | Verify the entire chain (audited) |
| GET | `/policy` | any linked user | Active policy, field catalogue, metrics, groupings, history |
| GET | `/policy/fields` | any linked user | Field catalogue |
| POST | `/admin/policy` | platform admin | `{document, change_note}` → validate, require higher version, activate (audited with a diff) |
| GET/POST | `/admin/users` | platform admin | List; create non-admin users (role must match organisation type) |
| PATCH | `/admin/users/{id}` | platform admin | Enable/disable, rename, link or unlink identity. Not self, not other admins |
| GET | `/admin/organizations` | platform admin | List with user counts |
| PATCH | `/admin/organizations/{id}` | platform admin | Enable/disable (not own organisation) |

Requester roles: `hospital_staff`, `insurance_officer`, `government_officer`. Which purpose each may use is
set by the policy (`allowed_roles`).

## Creating a request

Patient-scoped (insurer):

```json
POST /api/requests
{
  "purpose": "insurance_claim_assessment",
  "claim_reference": "CLM-M-1001",
  "requested_fields": ["patient_reference", "treatment_date", "diagnosis_code", "billing_amount"],
  "justification": "Assessing claim CLM-M-1001 for an inpatient admission."
}
```

Continuity of care (hospital): replace `claim_reference` with `"referral_reference": "REF-3001"`.

Aggregate (government):

```json
{
  "purpose": "public_health_reporting",
  "mandate_reference": "MAN-PH-2026",
  "aggregate": { "metric": "encounter_count", "group_by": ["diagnosis_category"],
                 "period_start": "2025-01-01", "period_end": "2026-09-30" },
  "justification": "Quarterly surveillance report."
}
```

Exactly one of `claim_reference`, `referral_reference` or `aggregate` is required. A reference that does not
exist and one belonging to another organisation both return the same `403 AUTHORIZATION_REFERENCE_INVALID`,
and nothing is stored.

The response's `request` contains `status`, `approved_fields`, `denied_fields`, `review_fields`,
`field_decisions` (per field: `approved`, `approved:reviewed`, `review`, `denied:hard_deny`,
`denied:not_necessary_for_purpose`, `denied:consent_excluded`, `denied:review_rejected`), `policy_codes`,
`decision_explanation`, `policy_version` and `evaluated_at`.

## Release

`POST /api/requests/{id}/release` re-evaluates the request now. Possible results:

- `200` with `records` (patient scope: one object per encounter containing only released field identifiers)
  or `aggregate` (groups, `value: null` when suppressed, suppression reasons, threshold), plus `reevaluation`.
- `409 NOT_RELEASABLE` when the request is pending, rejected or otherwise has nothing approved.
- `409 REAUTHORIZATION_FAILED` when consent, references, identity or policy no longer allow it. The request
  is marked rejected with `REEVALUATION_DENIED`.
- `403` when the caller isn't the original requester.

## Policy codes

| Code | Meaning |
|---|---|
| `AUTHZ_CLAIM_VERIFIED`, `LEGAL_BASIS_REFERRAL`, `LEGAL_BASIS_MANDATE` | Basis established |
| `CONSENT_VERIFIED` | Current consent found where required |
| `CONSENT_NOT_AVAILABLE`, `CONSENT_REFUSED` | Consent missing/withdrawn/refused/expired (hard) |
| `CONSENT_FIELD_EXCLUDED` | Consent excludes a field |
| `FIELD_HARD_DENIED` | Field never released for this purpose |
| `FIELD_NOT_PERMITTED_FOR_PURPOSE` | Field not needed for the purpose (minimisation) |
| `EXCESSIVE_SCOPE` | Request rejected as a whole |
| `REVIEW_FIELD_SENSITIVE`, `REVIEW_SENSITIVE_CATEGORY` | Needs human review |
| `REVIEW_APPROVED`, `REVIEW_REJECTED` | Review outcome |
| `REEVALUATION_DENIED` | Failed re-authorisation at release |
| `AGGREGATE_ONLY`, `SMALL_GROUP_SUPPRESSION` | Aggregate controls applied |
| `PATIENT_LEVEL_NOT_PERMITTED`, `AGGREGATE_QUERY_NOT_PERMITTED`, `AUTHZ_MANDATE_INVALID` | Aggregate rejection reasons |
| `IDENTITY_INACTIVE`, `ROLE_NOT_PERMITTED_FOR_PURPOSE`, `ORG_TYPE_MISMATCH`, `PURPOSE_UNKNOWN`, `SCOPE_MISMATCH`, `FIELD_UNKNOWN`, `NO_FIELDS_REQUESTED`, `NO_PERMITTED_FIELDS`, `PATIENT_NOT_FOUND`, `EVALUATION_ERROR` | Fail-closed rejections |
