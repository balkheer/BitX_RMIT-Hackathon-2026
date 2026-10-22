# Implementation report

## Technologies

React 19 + TypeScript + Vite 8 + React Router 7 + `@tidecloak/react` 0.14.33; Python 3.13 + FastAPI +
Pydantic 2 + SQLAlchemy 2 + Alembic + PyJWT/cryptography; PostgreSQL 16; Caddy 2; Docker Compose; GitHub
Actions.

## Features implemented

- **Authentication:** TideCloak sign-in/out via the official React SDK, redirect route, silent SSO file,
  session-expiry and re-authentication handling, protected routes. Backend verifies every token itself
  (signature, pinned algorithms, issuer, expiry, `azp`, `typ`, required claims) and fails closed.
- **Trusted identity mapping:** issuer + subject → `users` row linked by an operator or administrator.
  Roles and organisations never come from the client or token claims.
- **Policy engine:** pure, versioned, server-side. Purposes for insurance claims (claim + consent),
  continuity of care (referral; refusal blocks) and public-health reporting (mandate; aggregate only).
  Outcomes: approved, approved with restrictions, pending review, rejected. Returns approved, denied and
  review fields, per-field reasons, codes, explanation, version and timestamp.
- **Release with re-authorisation:** every release re-evaluates against current state and never widens.
- **Privacy review:** custodian-scoped queue, justification required, separation of duties, re-evaluation on
  decision, hard denials cannot be overridden.
- **Aggregate statistics:** mandate-gated, one dimension, k ≥ 5 with complementary suppression, no totals.
- **Audit:** hash-linked, append-only at the database level, verified via API, CLI and hourly checkpoints.
- **Administration:** users, identity links, organisations, policy versions with validation and diffs; all audited.
- **UI:** all required screens with loading, empty, error, permission-denied and success states; responsive
  layout; visible focus; status conveyed by text and shape as well as colour.
- **Operations:** least-privilege DB roles, Dockerfiles, production compose with internal network, Caddy
  TLS/CSP/log scrubbing, backups with checksums and retention, tested restore procedure, CI.

## Workflows verified

A (valid claim), B (excessive request), C (aggregate with suppression), D (unauthorised access), E
(sensitive review) and F (audit integrity), each in automated tests against PostgreSQL and in a live smoke
run through the running servers. Details: [TEST_REPORT.md](TEST_REPORT.md).

## TideCloak integration status

Implemented against the official SDK and Tide's own verifier logic. **Not tested against a live realm**:
no TideCloak server was reachable from the build environment. No live connection is claimed. The checklist
in [TIDECLOAK.md](TIDECLOAK.md) covers what must be verified.

## Test results

Backend 134 passed; frontend 11 passed; lint, type checks and production build clean; no known
vulnerabilities in either dependency set; migrations reproducible; live smoke 14/14.

## Build and deployment status

Production build succeeds. Deployment configuration is complete and validated locally (compose config,
Caddy validation and functional run, backup/restore exercise). **Not deployed**: no hosting environment,
domain or registry access was available, and container images could not be built because Docker Hub was
unreachable. No deployment URL exists.

## Running it

See [README.md](../README.md): local development, tests, production deployment, troubleshooting.

## Required environment variables

Backend (`backend/.env.example`): `CB_ENVIRONMENT`, `CB_DATABASE_URL`, `CB_MIGRATION_DATABASE_URL`,
`CB_TIDECLOAK_CONFIG_PATH` (or `CB_TIDECLOAK_AUTH_SERVER_URL` + `CB_TIDECLOAK_REALM` +
`CB_TIDECLOAK_RESOURCE`), `CB_CORS_ALLOWED_ORIGINS`, `CB_TRUSTED_HOSTS`, optional `CB_TIDECLOAK_AUDIENCE`,
`CB_TIDECLOAK_REQUIRED_ROLE`, `CB_TIDECLOAK_REQUIRE_BEARER_TYP`, `CB_AGGREGATE_MIN_GROUP_SIZE`,
`CB_RATE_LIMIT_SENSITIVE_PER_MINUTE`, `CB_RATE_LIMIT_ENABLED`, `CB_JWKS_CACHE_SECONDS`, `CB_LOG_LEVEL`.
Tests: `CB_TEST_DATABASE_URL`, `CB_TEST_MIGRATION_DATABASE_URL`, `CB_TEST_SUPERUSER_URL`.
Deployment (`deploy/.env.example`): `CB_DOMAIN`, `CB_ACME_EMAIL`, `CB_TIDECLOAK_ADAPTER_PATH`,
`CB_CSP_IDP_ORIGINS`, `CB_AGGREGATE_MIN_GROUP_SIZE`, `CB_BACKUP_DIR`, `CB_BACKUP_RETENTION_DAYS`,
`CB_CHECKPOINT_DIR`; plus three Docker secret files in `deploy/secrets/`.

## Remaining external dependencies and limitations

1. Live TideCloak realm and verified sign-in, including the CSP origins the Tide enclave needs.
2. A hosting target (host, domain, DNS) and registry access to build images and deploy.
3. A manual browser pass for visual layout, responsiveness and keyboard accessibility.
4. Production hardening listed in [THREAT_MODEL.md](THREAT_MODEL.md): externally anchored audit checkpoints,
   shared rate limiting for multiple replicas, encryption at rest, and stronger aggregate protections where risk is higher.
5. The policy models example legal bases for a demonstration. It is not legal advice and does not establish
   regulatory compliance.
