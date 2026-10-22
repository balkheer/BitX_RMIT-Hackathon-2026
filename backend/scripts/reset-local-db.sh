#!/usr/bin/env bash
# Development only: drop and recreate a local database, migrate, and load synthetic data.
# Usage: PGSUPERUSER_URL=postgresql://postgres:...@127.0.0.1:5432/postgres scripts/reset-local-db.sh consentbridge
set -euo pipefail
DB="${1:-consentbridge}"
: "${PGSUPERUSER_URL:?set PGSUPERUSER_URL to a superuser connection string}"
if [[ "${CB_ENVIRONMENT:-development}" == "production" ]]; then echo "refusing in production" >&2; exit 1; fi
psql "$PGSUPERUSER_URL" -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS \"$DB\" WITH (FORCE)" -c "CREATE DATABASE \"$DB\" OWNER cb_owner"
alembic upgrade head
python -m app.cli seed-synthetic
