# Operations

## Production topology

`deploy/docker-compose.prod.yml` runs:

| Service | Role | Network |
|---|---|---|
| `db` | PostgreSQL 16, roles created from Docker secrets on first start | internal only, not published |
| `migrate` | One-shot `alembic upgrade head` as `cb_owner` | internal |
| `bootstrap` | One-shot `python -m app.cli bootstrap` (initial policy, platform organisation) | internal |
| `api` | FastAPI as `cb_app`, read-only filesystem, no capabilities, healthcheck on `/readyz` | internal + edge |
| `web` | Caddy: HTTPS (ACME), SPA, `/api` proxy, CSP and security headers, scrubbed JSON logs | edge, ports 80/443 |
| `backup` | `pg_dump` custom format every 24 h with SHA-256 and retention | internal |
| `audit-checkpoint` | Hourly full-chain verification, appends head hash to `audit-checkpoints/heads.log` | internal |

## First deployment

Requirements: a Linux host with Docker Engine and Compose v2, a DNS record for your domain pointing at it,
ports 80 and 443 open, and a TideCloak realm with an app whose redirect URI is `https://<domain>/*`.

```bash
git clone <repo> consentbridge && cd consentbridge
cp deploy/.env.example deploy/.env && $EDITOR deploy/.env
sudo install -d -m 750 /etc/consentbridge
sudo install -m 640 ~/Downloads/tidecloak.json /etc/consentbridge/tidecloak.json   # path from .env
install -d -m 700 deploy/secrets
for s in pg_superuser_password pg_owner_password pg_app_password; do
  openssl rand -base64 32 | tr -d '\n' > deploy/secrets/$s; done
chmod 600 deploy/secrets/*
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env ps
curl -fsS https://<domain>/readyz
```

Create the first administrator and link users (the API image contains the CLI):

```bash
C="docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env"
$C run --rm --entrypoint sh api -c 'export CB_DATABASE_URL=postgresql+psycopg://cb_app:$(cat /run/secrets/pg_app_password)@db:5432/consentbridge; python -m app.cli create-admin --username ops.admin --display-name "Ops Admin" --subject <sub>'
```

Do **not** run `seed-synthetic` in production (it refuses). For a hackathon demo environment, set
`CB_ENVIRONMENT=development` in a separate, clearly-labelled deployment instead.

## Secrets

- Database passwords live only in `deploy/secrets/*` (mode 600), mounted as Docker secrets. They never appear
  in images, compose files, environment listings or logs.
- The TideCloak adapter is mounted read-only; it holds public client configuration only. No client secret is
  used by the SPA.
- Rotate DB passwords with `ALTER ROLE … PASSWORD …`, update the secret file, and restart `api`,
  `audit-checkpoint` and `backup`.

## TLS

Caddy obtains and renews certificates automatically for `CB_DOMAIN` using `CB_ACME_EMAIL`. HSTS is set for one
year. Certificates persist in the `caddy_data` volume. Behind another TLS-terminating load balancer, change
the site address in `frontend/deploy/Caddyfile` to `:80` and keep HSTS at the outer layer.

## Updates and migrations

```bash
git pull
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build
```

`migrate` runs before `api` on every start. Migrations are versioned in `backend/migrations/versions` and have
been run up, down and up again in testing. Take a backup before applying new migrations.

## Backups

- Automatic: daily `pg_dump --format=custom` into `CB_BACKUP_DIR` with a `.sha256` file, pruned after
  `CB_BACKUP_RETENTION_DAYS`.
- On demand: `$C run --rm --entrypoint sh backup /usr/local/bin/backup.sh once`.
- Copy backups off the host to encrypted, access-controlled storage. They contain (synthetic or real) patient
  data and must be protected like the database.

## Restore

```bash
deploy/backup/restore.sh deploy/backups/consentbridge-<timestamp>.dump consentbridge_restore
```

The script checks the SHA-256, restores into a **new** database, and runs a full audit verification against
it. Compare the reported head hash with the matching line in `audit-checkpoints/heads.log`. To switch over in a
maintenance window:

```bash
$C stop api audit-checkpoint
$C exec db psql -U postgres -c "ALTER DATABASE consentbridge RENAME TO consentbridge_old" \
  -c "ALTER DATABASE consentbridge_restore RENAME TO consentbridge"
$C start api audit-checkpoint
```

This procedure was exercised in the build environment with `pg_dump`/`pg_restore` directly: the restored
database verified (55 events), the runtime role was still denied `DELETE`, and the append-only trigger still
blocked owner updates.

## Monitoring and alerts

| Signal | Source | Alert when |
|---|---|---|
| Availability | `GET /healthz`, `GET /readyz` via an external uptime check | non-200 for 2 minutes |
| Identity provider reachability | `503 AUTH_PROVIDER_UNAVAILABLE` in API logs | any occurrence for 5 minutes |
| Audit integrity | `audit-checkpoint` lines with `"valid":false`; `AUDIT_VERIFIED` events with outcome `failure` | any occurrence (page someone) |
| Abuse | `429` rate, spikes of `ACCESS_DENIED` / `AUTH_DENIED` events per user | above baseline |
| Errors | `500` rate in API logs (correlation IDs included) | above baseline |
| Backups | `backup FAILED` in backup logs, or no new dump in 26 hours | any |
| Certificates | Caddy logs | renewal errors |
| Container health | `docker compose ps` / Docker healthchecks | unhealthy |

Logs: the API logs one JSON line per request with method, route template, status, duration, correlation ID
and user ID; no bodies, headers or query strings. Caddy logs JSON with query strings and `Authorization`
removed. Ship both to your log platform and alert on the patterns above.

## Troubleshooting

| Symptom | Check |
|---|---|
| `api` unhealthy | `$C logs api`; `/readyz` lists which check failed (database, policy, auth) |
| `migrate` fails with "runtime role cb_app does not exist" | The `db` volume was created before the init script ran. Create the roles manually (see `deploy/postgres/init-roles.sh`) |
| Sign-in loops or blank page after sign-in | Redirect URI in tide-console; `silent-check-sso.html` served; browser console CSP errors → add origins to `CB_CSP_IDP_ORIGINS` |
| All requests 401 | Adapter mismatch between browser and API (realm, URL, client ID) or clock skew |
| `403 IDENTITY_NOT_MAPPED` | Link the user's subject |
| Certificate not issued | DNS not pointing at the host, or ports 80/443 blocked |
| Restore verification fails | Do not switch over. Compare with checkpoints, investigate, try an earlier dump |

## Not yet done

No deployment target was provided, so the stack has not been deployed or tested on a public host. The compose
file was validated with `docker compose config`, the Caddyfile with `caddy validate` and a local functional
run, and the scripts were exercised locally. Container images were not built because the build environment
could not reach Docker Hub.
