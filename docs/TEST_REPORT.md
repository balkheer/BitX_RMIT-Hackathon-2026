# Test report

Recorded on 22 October 2026 in the build environment (Ubuntu 24.04, Python 3.13.16, Node 22.22.0,
PostgreSQL 16.15). Every result below was produced by running the command; nothing is estimated.

## Summary

| Check | Command | Result |
|---|---|---|
| Backend tests | `pytest -q` | **134 passed** |
| Backend lint | `ruff check .` | clean |
| Backend types | `mypy app` | no issues in 28 files |
| Backend dependencies | `pip-audit -r requirements.txt` | no known vulnerabilities |
| Migrations | fresh DB: `alembic upgrade head` → `downgrade base` → `upgrade head`; `alembic check` | identical schema each time (14 tables, 2 audit triggers, GIN index); no model drift |
| Frontend lint | `npm run lint` | clean |
| Frontend types | `npm run typecheck` | clean |
| Frontend tests | `npm test` (Vitest + Testing Library, jsdom) | **11 passed** |
| Frontend build | `npm run build` | succeeds |
| Frontend dependencies | `npm audit --omit=dev` | 0 vulnerabilities |
| Live smoke (workflows A–F) | `python scripts/smoke_e2e.py` against uvicorn + `vite preview` proxy + PostgreSQL | **14/14 pass** |
| Edge config | `caddy validate`, plus a local functional run | valid; headers, SPA fallback, `/api` proxy, adapter route work; logs contain no query strings or auth headers |
| Compose file | `docker compose config` | valid |
| Backup / restore | `pg_dump` → checksum → `pg_restore` into a new DB → `verify-audit` | restored chain valid; runtime role still denied DELETE; trigger still blocks owner UPDATE |
| DB role init script | run against a scratch DB with quote characters in passwords | roles created and can log in |
| Log hygiene | scan of API and edge logs after the smoke run | no JWTs, patient references, aliases or clinical text |
| Bundle hygiene | grep of `dist/` for secrets | none |

## Backend test inventory (`backend/tests`)

| File | Tests | Covers |
|---|---|---|
| `test_policy_engine.py` | 42 | Every decision outcome; consent required/refused/expired/excluded; claim and referral bases; hard denials; excessive scope; review and sensitive-category review; reviewer cannot override hard denials; unknown fields; inactive identity; role/org-type mismatch; admins and reviewers can't request; aggregate rules; fail-closed on internal error; policy-document guard rails |
| `test_auth.py` | 23 | Missing/non-Bearer/malformed tokens; ES256 and EdDSA; wrong signing key; expired (and within tolerance); untrusted issuer; wrong `azp`; ID token as access token; unknown kid; `alg: none`; HS256 public-key confusion; missing claims; unmapped identity (403 + audit, no raw subject); token claims and headers can't grant role/org; disabled user/org; issuer must match; auth not configured → 503; session start audited without token material; health endpoints |
| `test_authorization.py` | 23 | Custodian-only patient directory; cross-org patient access 403 + audit; no patient access for insurer/government/admin/reviewer; sensitive notes withheld; IDOR on requests; unknown 404; foreign vs non-existent claim give identical 403 and nothing stored; purpose not permitted for role; reviewers/admins can't request; mass assignment (6 variants); org headers ignored; only requester can release; admin redaction; admin separation of duties; disable/enable user audited; no admin creation via API; role/org mismatch; admin and review endpoints restricted; audit verification restricted; audit listing org-scoped; unknown API path; validation errors don't echo input |
| `test_workflows.py` | 25 | Workflow A (preview, create, release exactly the approved fields, audit identifiers only); B (excessive rejected, overreach restricted, denied values absent from responses and audit, consent exclusion, missing consent, unknown fields); C (aggregate only, primary and complementary suppression with exact cells, patient fields refused, expired mandate, multi-dimension refused, non-government refused); D (403 logged without token or patient data); E (pending holds data, other custodian's reviewer blocked, short justification rejected, requester can't review, approval keeps hard denials, duplicate decision 409, reviewed fields released, rejection records reviewer + justification, same-org SoD); re-authorisation at release after consent withdrawal; review approval re-evaluates current consent; release shrinks after a new consent exclusion; F (verify endpoint, per-row hashes); dashboard scoping; filters; reference lists scoped; rate limiting 429; policy change validation, version bump, audit diff, new evaluations use new version |
| `test_audit.py` | 15 | Valid chain; modified decision; modified details; single event edited and re-hashed (breaks next link); deleted event; swapped events; forged inserted event; runtime role can't UPDATE/DELETE/TRUNCATE audit; trigger blocks owner; runtime role can't DELETE business rows; 8 threads × 15 concurrent appends stay contiguous and valid; rolled-back transaction leaves no audit row; no credentials, clinical text or aliases in any audit row; secret keys and floats rejected; deterministic canonical serialisation and timestamp normalisation |
| `test_config.py` | 6 | CSV and JSON list settings; symmetric/`none` algorithms refused; production guards (trusted hosts, CORS wildcard, k < 5); production requires an HTTPS identity provider; issuer and JWKS URL derivation |

Tests run against a real PostgreSQL database created, migrated (as the owner role) and seeded for each run,
using the runtime role for the app. Tokens are signed with keys generated per session and published to the
verifier through an adapter `jwk` block, so signature, issuer, expiry, `azp` and `typ` checks run for real.

## Frontend test inventory (`frontend/src/test/app.test.tsx`)

Signed-out users only see TideCloak sign-in (no other controls, no API calls); missing adapter explained
instead of offering sign-in; session-expired message; unprovisioned identity blocked with the user's own
identifier shown; 401 triggers re-authentication; Bearer header sent and cookies omitted; dashboard renders
server metrics and role-appropriate navigation; new-request form validates before sending and the preview
body contains no organisation, role or status; field ledger dispositions; aggregate table suppression;
audit verification call and failure display.

## Live smoke run (`backend/scripts/smoke_e2e.py`)

Servers: `uvicorn app.main:app` on the persistent development database; `vite preview` serving the
production build and proxying `/api`. Output of the final run:

```
PASS A1 sign-in maps to insurer
PASS A2 valid claim approved
PASS A3 release returns only approved fields
PASS B1 excessive request rejected ['AUTHZ_CLAIM_VERIFIED', 'CONSENT_VERIFIED', 'EXCESSIVE_SCOPE']
PASS B2 nothing releasable
PASS E1 sensitive request pending ['diagnosis_code', 'medication']
PASS E2 no release before review
PASS E3 out-of-scope reviewer blocked
PASS E4 reviewer approves, hard-deny stays approved_with_restrictions
PASS E5 reviewed fields released
PASS C1 aggregate with suppression {'Central': False, 'Coastal': True, 'Inland': True, 'North': False}
PASS D1 insurer reading a patient record is 403
PASS F1 audit chain verifies
PASS Auth: no token is 401
ALL PASS
```

Tokens for this run were signed by a throwaway key that the API was explicitly configured to trust, standing
in for TideCloak. This verifies the application, not TideCloak sign-in.

## Defects found and fixed during testing

1. `aggregate_query` stored JSON `null` instead of SQL `NULL`, violating the scope constraint → `JSONB(none_as_null=True)`.
2. `SELECT … FOR UPDATE` failed with eager outer joins → lock only `data_requests` (`FOR UPDATE OF`).
3. Comma-separated list settings (`CB_TRUSTED_HOSTS=a,b`) crashed at startup → parse with `NoDecode`; regression tests added.
4. Test isolation: consent is per patient + purpose + organisation, so tests now use a fresh synthetic patient per claim.
5. Edge logs kept query strings (which can include search terms) → Caddy `regexp` filter; verified in a local run.
6. GIN index existed only in a migration → added to the model; `alembic check` clean.
7. Frontend: React hook lint findings (setState in effects, ref reads in render) → refactored data hook.

## Not tested, and why

| Item | Reason | What to do |
|---|---|---|
| Sign-in against a real TideCloak realm | No TideCloak server reachable: Docker Hub (for `tideorg/tidecloak-dev`) is blocked here and no hosted realm was provided | Follow the checklist in [TIDECLOAK.md](TIDECLOAK.md) |
| CSP origins required by the Tide enclave | Depends on the live realm | Watch for CSP violations during first real sign-in |
| Browser rendering, responsive layouts, keyboard walkthrough | No headless browser could run in this environment (Playwright CDN blocked; bundled Chromium hung at GPU initialisation) | Manual pass at 1440, 768 and 375 px; keyboard-only run of workflows A–F |
| Docker image builds and full compose start-up | Docker Hub blocked, so base images could not be pulled | `docker compose -f deploy/docker-compose.prod.yml up --build` on a host with registry access |
| Public deployment, TLS issuance, external health checks | No hosting target or domain was provided | See [OPERATIONS.md](OPERATIONS.md) |
| Frontend accessibility audit tooling (axe) | No browser available | Run axe or Lighthouse during the manual pass |
