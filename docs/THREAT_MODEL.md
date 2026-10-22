# Threat model and security assumptions

Scope: the ConsentBridge SPA, API, PostgreSQL database, Caddy edge, operator CLI and the integration with
TideCloak. Method: assets, trust boundaries and attack surfaces, then STRIDE-style threats with the
mitigation implemented and how it is tested. Controls reduce risk; they do not make the system "secure" in
an absolute sense, and nothing here constitutes regulatory compliance.

## Assets

| Asset | Why it matters |
|---|---|
| Patient-level clinical, financial and sensitive fields | Primary harm if disclosed beyond purpose |
| Aggregate statistics | Can re-identify individuals when groups are small or queries overlap |
| Authorisation state: user↔identity links, roles, organisations, consents, claims, referrals, mandates, policy versions | Altering any of these changes who can see what |
| Audit trail | Accountability; must reveal tampering |
| Credentials: DB passwords, ACME account, TideCloak admin | Full compromise of a tier |
| Access tokens | Impersonation for their lifetime |

## Trust boundaries and attack surfaces

1. Internet → Caddy (HTTPS): static SPA, `/api/*`, `/healthz`, `/readyz`, `/tidecloak.json`.
2. Browser ↔ TideCloak / Tide network: sign-in, silent SSO iframe, token refresh.
3. Caddy → API: all REST endpoints (see [API.md](API.md)).
4. API → TideCloak: JWKS fetch (unless the adapter carries `jwk`).
5. API → PostgreSQL as `cb_app`; migration job as `cb_owner`; operator CLI on the host.
6. Backups and audit checkpoint files on the host volume.

## Threats and mitigations

| # | Threat | Mitigation | Evidence |
|---|---|---|---|
| S1 | Forged or tampered token | Signature verified against realm keys; pinned asymmetric algorithms (no `none`/HS*); issuer, `exp`/`nbf`/`iat`, `azp`, `typ=Bearer` and `sub` required | `test_auth.py`: wrong key, alg none, HS256 key confusion, unknown kid, wrong issuer, wrong `azp`, ID token, missing claims, expired |
| S2 | Valid TideCloak user who was never provisioned | Must map to a linked `users` row by issuer + subject; otherwise 403 and audited (subject fingerprint only) | `test_verified_but_unmapped_identity_is_403_and_audited`, `test_identity_must_match_issuer_too` |
| S3 | Departed user / suspended organisation | `is_active` checked on every request for user and organisation; also re-checked in the policy engine at release | `test_disabled_user_is_403`, `test_disabled_organization_is_403` |
| S4 | Demo shortcut or bypass in production | None exists: no demo login, no role switcher, no unauthenticated data route; seed users are unlinked; seed refused in production | `test_sends_signed_out_users…` (UI); code review |
| T1 | Client asserts role/org/status (mass assignment, header spoofing) | Role and organisation only from DB; input schemas forbid extra keys; server sets requester, org, status, decision | `test_mass_assignment_is_rejected`, `test_token_claims_cannot_grant_roles_or_org`, `test_client_supplied_org_headers_are_ignored` |
| T2 | Tampering with audit records | Runtime role cannot UPDATE/DELETE; trigger blocks UPDATE/DELETE/TRUNCATE for all roles; hash chain detects modification, deletion, insertion, reordering; hourly external head checkpoints | `test_audit.py` |
| T3 | Unauthorised policy change | Admin only, schema guard rails, version must increase, audited with diff; admins and reviewers can never be granted request purposes | `test_policy_change_workflow`, `TestPolicyDocumentValidation` |
| R1 | Repudiation of decisions or releases | Submissions, evaluations, previews, reviews (with justification), releases, denials, admin and policy changes all audited in the same transaction as the change | workflow tests assert audit events |
| I1 | Over-collection by a legitimate requester | Purpose-specific allowed/review/hard-deny buckets; whole-request rejection for excessive scope; consent exclusions; only approved fields serialised | `TestInsuranceClaims`, `test_workflow_b_*` |
| I2 | IDOR on requests, patients, claims, referrals, mandates | Every lookup scoped to the caller's organisation; references resolved with org filters; same 403 for "missing" and "not yours" | `test_insurer_cannot_read_another_insurers_request`, `test_using_another_insurers_claim…`, `test_cross_org_patient_access…` |
| I3 | Denied values leaking through errors, logs or audit | Errors are generic; validation errors don't echo input; audit details are identifiers only and reject keys like `token`/`password` and floats; app logs route templates without query strings; uvicorn access log disabled; Caddy strips query strings and auth headers from logs | `test_validation_errors_do_not_echo_input`, `test_no_credentials_or_patient_values_in_audit`, smoke log scan |
| I4 | Release after conditions change (consent withdrawn, referral closed, policy tightened) | Re-evaluation at review and at every release; never widens; marks request rejected when it fails | `test_reauthorization_at_release…`, `test_release_shrinks…`, `test_reviewer_approval_reevaluates…` |
| I5 | Re-identification from aggregates | Mandate required; one grouping dimension; period cap; k ≥ 5 (production floor); complementary suppression; no totals; patient fields refused | `test_workflow_c…`, `test_secondary_suppression…`; limits in [PRIVACY.md](PRIVACY.md) |
| I6 | Administrator browsing patient data | Admin has no patient routes, cannot release or review, sees request metadata with patient identity redacted | `test_admin_sees_request_metadata_without_patient_identity`, `test_insurer_and_government_have_no_patient_directory` |
| I7 | Existence oracle via policy outcomes | Authorisation reference checked before patient resolution; unknown and foreign references give identical responses | `test_using_another_insurers_claim_is_403_and_not_persisted` |
| E1 | Reviewer approves own or own-org request, or outside custodian scope | Reviewer must belong to the custodian; requester user and requester org excluded; one decision per request (row lock + unique constraint) | `test_workflow_e_sensitive_review`, `test_separation_of_duties_same_org_reviewer` |
| E2 | Reviewer overrides a hard denial | Review only flips review-required fields; request re-evaluated; hard rejects stay rejected | `test_review_cannot_override_hard_denials`, workflow E |
| E3 | Admin escalates self or creates admins | Cannot modify own account or organisation; cannot create/modify admins via API (operator CLI only) | `test_admin_cannot_*` |
| D1 | Request flooding / audit-log flooding | Per-user rate limits on sensitive endpoints; Caddy in front; validation size limits | `test_rate_limit_on_sensitive_endpoints` |
| D2 | Identity provider outage | Fails closed with 503 (distinct from 401) so outages are visible to monitoring | `test_auth_not_configured_fails_closed` |
| X1 | XSS stealing tokens from memory | React escaping, ESLint ban on `dangerouslySetInnerHTML`, strict CSP (`script-src 'self'`), no inline scripts | lint rule, Caddy test |
| X2 | CSRF | API authenticates with a Bearer header only, never cookies; CORS allowlist without credentials | design |
| X3 | Clickjacking | `frame-ancestors 'none'`, `X-Frame-Options: DENY` | Caddy test |

## Assumptions

- TideCloak and the Tide network correctly authenticate users and protect realm signing keys. ConsentBridge
  trusts identity assertions from the configured realm only.
- Administrators who link identities verify that a subject belongs to the intended person.
- The host running Docker, the PostgreSQL superuser and the backup volume are protected by the operator.
  A superuser can disable triggers; the hash chain then makes changes evident but cannot prevent them.
- Clocks are synchronised (token checks allow 5 seconds of skew).
- The rate limiter is per process. With several API replicas, add shared or edge-level limits.

## Residual risks and hardening recommendations

1. **Live TideCloak verification outstanding.** Run the sign-in checklist in [TIDECLOAK.md](TIDECLOAK.md)
   against your realm before any real use, including the CSP origins the Tide enclave needs.
2. **Insider with database superuser access** can rewrite the whole chain consistently. Ship
   `audit-checkpoints/heads.log` to write-once storage (object lock) or sign checkpoints with a key held
   outside the database host; consider streaming audit events to a separate append-only store.
3. **Token theft from the browser** remains possible via a compromised device or extension. Keep token
   lifetimes short in the realm; consider TideCloak's hybrid mode or DPoP if your deployment supports it.
4. **Aggregate differencing** across many overlapping queries. Add per-agency query budgets or noise
   (differential privacy) for higher-risk datasets.
5. **Encryption at rest** depends on the host. Use encrypted volumes and encrypted, access-controlled backups.
6. **Field-level encryption** with Tide's `doEncrypt`/`doDecrypt` could protect sensitive notes even from
   database operators; not implemented here.
7. **Rate limiting state** is in memory and resets on restart.
8. **Dependency drift.** CI runs `pip-audit` and `npm audit`; review results on every change.
