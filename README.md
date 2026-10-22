# ConsentBridge

Privacy-first healthcare data sharing for RMIT Hackathon 2026, Challenge 2: *Build Without Blind Trust*.

Hospitals, insurers, a government health agency, privacy reviewers and platform administrators exchange
health information through one rule: **a valid identity is necessary but never sufficient.** TideCloak proves
who someone is. ConsentBridge then decides, field by field, what that person may see for the purpose they
declare, using their organisation, role, the authorisation reference (claim, referral or mandate), the
patient's recorded consent where it applies, and human review for sensitive requests. Every decision and
release is written to a hash-linked, tamper-evident audit trail.

> All patient, organisation and clinical data in this repository is **synthetic**. Organisation names carry
> "(synthetic)". Do not load real patient data.

## Contents

- [Status at a glance](#status-at-a-glance)
- [Technology](#technology)
- [Repository layout](#repository-layout)
- [Quick start with Docker Desktop only](#quick-start-with-docker-desktop-only)
- [Local development](#local-development)
- [Configuring TideCloak](#configuring-tidecloak)
- [Running tests](#running-tests)
- [Production deployment](#production-deployment)
- [Troubleshooting](#troubleshooting)
- [Documentation index](#documentation-index)

## Status at a glance

| Area | State |
|---|---|
| Backend API, policy engine, audit chain, migrations, seed data | Implemented; 134 automated tests pass against PostgreSQL 16 |
| Frontend (all required screens) | Implemented; lint, type check, 11 UI tests and production build pass |
| Workflows A–F | Verified end to end through the running API and the frontend server's proxy (see [docs/TEST_REPORT.md](docs/TEST_REPORT.md)) |
| TideCloak integration | Implemented against the official SDKs and verifier logic. **Not yet tested against a live TideCloak realm**: no TideCloak server was reachable from the build environment. See [docs/TIDECLOAK.md](docs/TIDECLOAK.md) |
| Production deployment | Compose stack, Caddy TLS edge, backups and audit checkpoints written and validated; **not deployed** (no hosting target was provided) |
| Visual QA in a browser | Not performed: no headless browser could run in the build environment |

## Technology

- **Frontend**: React 19, TypeScript, Vite 8, React Router 7, `@tidecloak/react` 0.14.33, Public Sans (self-hosted), lucide icons.
- **Backend**: Python 3.13, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PyJWT + cryptography (ES256/384/512, EdDSA), psycopg 3.
- **Database**: PostgreSQL 16 with versioned migrations, least-privilege runtime role, append-only audit table.
- **Edge**: Caddy 2 (automatic HTTPS, security headers, CSP, `/api` reverse proxy, log scrubbing).
- **Ops**: Docker Compose, `pg_dump` backups with retention, hourly audit-chain verification with externalised head hashes, GitHub Actions CI.

## Repository layout

```
backend/            FastAPI service
  app/auth/         TideCloak token verification, identity mapping
  app/policy/       field catalogue, versioned policy document, pure policy engine
  app/audit/        hash-linked audit chain and verifier
  app/services/     request lifecycle, release re-authorisation, review, aggregates
  app/api/          REST routes (requests, reviews, patients, audit, policy, admin)
  migrations/       Alembic migrations (schema, grants, append-only trigger, indexes)
  tests/            pytest suite (real Postgres, real JWT verification)
frontend/           React SPA, Caddyfile, Dockerfile
deploy/             production and dev compose files, Postgres init, backup/restore
docs/               architecture, API, threat model, audit, privacy, TideCloak, operations, demo, test report
```

## Quick start with Docker Desktop only

No Python or Node needed. Works the same in macOS Terminal and Windows PowerShell.

```bash
docker compose -f deploy/docker-compose.local.yml up -d --build
```

1. Open `http://localhost:8080`, sign in as `admin` / `change-me-local-only`, complete **Set up this realm**
   and link your Tide account.
2. In tide-console: **Apps → Add an app**, redirect URI `http://localhost:5173/*`, web origin
   `http://localhost:5173`. Then **Credentials → Tide adapter → Download tidecloak.json** and save it as
   `deploy/local-config/tidecloak.json`.
3. `docker compose -f deploy/docker-compose.local.yml restart api`
4. Open `http://localhost:5173` and sign in. The "not provisioned" screen shows your identifier; link it:
   `docker compose -f deploy/docker-compose.local.yml exec api python -m app.cli link-identity --username j.walker --subject <identifier>`
   and refresh.

Stop with `docker compose -f deploy/docker-compose.local.yml down` (add `-v` to wipe the data).

## Local development

Prerequisites: Python 3.12+, Node 18.17+ (22 recommended), PostgreSQL 16 (or Docker), and a TideCloak realm
for signing in.

**1. Database.** Either run `docker compose -f deploy/docker-compose.dev.yml up -d db`, which creates the
`cb_owner` and `cb_app` roles and both databases from `deploy/postgres/dev-init.sql`, or run that SQL against
your own PostgreSQL as a superuser.

**2. Backend.**

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # fill in the two database URLs and the adapter path
export $(grep -v '^#' .env | xargs)
alembic upgrade head              # runs as cb_owner (CB_MIGRATION_DATABASE_URL)
python -m app.cli seed-synthetic  # policy + synthetic dataset (refused when CB_ENVIRONMENT=production)
uvicorn app.main:app --reload --port 8000
```

API docs are served at `http://localhost:8000/api/docs` outside production.

**3. Frontend.**

```bash
cd frontend
npm install
cp /path/to/downloaded/tidecloak.json public/tidecloak.json   # see "Configuring TideCloak"
npm run dev                      # http://localhost:5173, proxies /api to :8000
```

**4. Link identities.** Seeded users have no identity link, so nobody can sign in until you link them.
Sign in once with each TideCloak account: the "not provisioned" screen shows that account's identifier
(the token's `sub` claim). Then link it:

```bash
python -m app.cli link-identity --username j.walker --subject <tidecloak-user-id>
python -m app.cli create-admin --username ops.admin --display-name "Ops Admin" --subject <id>
python -m app.cli list-users
```

The seeded users are listed in [docs/DEMO.md](docs/DEMO.md). There is no demo login, role switcher or
bypass: every session goes through TideCloak.

## Configuring TideCloak

Summary (full steps in [docs/TIDECLOAK.md](docs/TIDECLOAK.md)):

1. Start TideCloak (`docker compose -f deploy/docker-compose.dev.yml up -d tidecloak` uses the
   `tideorg/tidecloak-dev` image from the official quickstart), open `http://localhost:8080`, set up the realm
   and link your Tide account.
2. In tide-console add an app for ConsentBridge. Valid redirect URI `http://localhost:5173/*`, web origin
   `http://localhost:5173`.
3. Download the adapter: **Apps → your app → Credentials → Tide adapter → Download tidecloak.json**.
   Put it at `frontend/public/tidecloak.json` and point `CB_TIDECLOAK_CONFIG_PATH` at the same file.
4. Create TideCloak users, then link each to a ConsentBridge user (step 4 above).

The backend verifies every access token itself: signature against the realm keys (adapter `jwk` block or the
realm's `/protocol/openid-connect/certs`), pinned algorithms (ES256/384/512, EdDSA), issuer, expiry, `azp`
equal to the client ID, and `typ` of `Bearer`.

## Running tests

Backend (needs PostgreSQL; the test database is dropped and recreated each run):

```bash
cd backend && . .venv/bin/activate
export CB_TEST_DATABASE_URL=postgresql+psycopg://cb_app:<pw>@127.0.0.1:5432/consentbridge_test
export CB_TEST_MIGRATION_DATABASE_URL=postgresql+psycopg://cb_owner:<pw>@127.0.0.1:5432/consentbridge_test
export CB_TEST_SUPERUSER_URL=postgresql+psycopg://postgres:<pw>@127.0.0.1:5432/postgres
pytest -q            # 134 tests
ruff check . && mypy app
pip-audit -r requirements.txt
```

Frontend:

```bash
cd frontend
npm run lint && npm run typecheck && npm test && npm run build
npm audit --omit=dev
```

Results from the build environment are recorded in [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

## Production deployment

```bash
cp deploy/.env.example deploy/.env         # domain, ACME email, adapter path, CSP origins
mkdir -p deploy/secrets && chmod 700 deploy/secrets
for s in pg_superuser_password pg_owner_password pg_app_password; do
  openssl rand -base64 32 > deploy/secrets/$s; done
chmod 600 deploy/secrets/*
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build
```

This starts PostgreSQL (internal network only), runs migrations as the owner role, bootstraps the policy,
starts the API as the least-privileged role, serves the SPA through Caddy with automatic HTTPS, and runs
daily backups and hourly audit verification. Then create the first administrator with the CLI and link users.
Details, TLS, monitoring, backup and restore: [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Sign-in screen says sign-in is unavailable | `/tidecloak.json` is missing or invalid. Place the downloaded adapter at `frontend/public/tidecloak.json` (dev) or mount it via `CB_TIDECLOAK_ADAPTER_PATH` (prod). |
| Signed in, then "identity verified but not provisioned" | The TideCloak `sub` isn't linked. Run `python -m app.cli link-identity`. The attempt is in the audit trail as `AUTH_DENIED`. |
| Every API call returns 401 `INVALID_TOKEN` | Issuer or client mismatch. `auth-server-url` + `realm` in the backend's adapter must match the token's `iss`, and `resource` must equal the token's `azp`. Check server clocks (5 s tolerance). |
| API returns 503 `AUTH_PROVIDER_UNAVAILABLE` | The backend can't fetch the realm's signing keys. Check network access from the API to TideCloak, or ship the adapter with its `jwk` block. |
| API returns 503 `AUTH_NOT_CONFIGURED` | `CB_TIDECLOAK_CONFIG_PATH` doesn't point at a readable adapter file. |
| `/readyz` returns 503 | Database unreachable, no active policy (run `python -m app.cli bootstrap`), or auth not configured. |
| Silent SSO or sign-in loops | `public/silent-check-sso.html` missing, redirect URI not registered in tide-console, or the CSP lacks your TideCloak origins (`CB_CSP_IDP_ORIGINS`). |
| `permission denied for table audit_events` | Expected for the runtime role on UPDATE/DELETE. The audit table is append-only by design. |
| Tests exit immediately | Set the three `CB_TEST_*` URLs. |

## Documentation index

- [Architecture and data flows](docs/ARCHITECTURE.md)
- [API reference](docs/API.md) and machine-readable [OpenAPI](docs/openapi.json)
- [Threat model and security assumptions](docs/THREAT_MODEL.md)
- [Audit chain design](docs/AUDIT.md)
- [Privacy model: consent, legal basis, minimisation, aggregate limits](docs/PRIVACY.md)
- [TideCloak setup and integration status](docs/TIDECLOAK.md)
- [Operations: deployment, TLS, backups, restore, monitoring](docs/OPERATIONS.md)
- [Two-minute demo script](docs/DEMO.md)
- [Test report](docs/TEST_REPORT.md)
- [Implementation report](docs/IMPLEMENTATION_REPORT.md)

ConsentBridge implements technical controls. It does not by itself make any data sharing lawful or certify
compliance with any regulation.
