#!/usr/bin/env bash
# Restore a backup into a NEW database, verify it, then switch over deliberately.
# Usage: deploy/backup/restore.sh backups/consentbridge-YYYYMMDDTHHMMSSZ.dump [target_db]
set -euo pipefail
DUMP="$1"; TARGET="${2:-consentbridge_restore}"
COMPOSE="docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env"
sha256sum -c "$DUMP.sha256"
$COMPOSE exec -T db sh -c "createdb -U postgres -O cb_owner $TARGET"
$COMPOSE exec -T db sh -c "pg_restore -U postgres -d $TARGET --no-owner --role=cb_owner" < "$DUMP"
# The custom-format dump includes grants, indexes and the audit append-only trigger.
# Verify the restored audit chain before switching over.
$COMPOSE run --rm -e CB_DATABASE_URL="postgresql+psycopg://cb_app:$(cat deploy/secrets/pg_app_password)@db:5432/$TARGET" \
  --entrypoint "" api python -m app.cli verify-audit
echo "Restored into $TARGET and verified. Compare the head hash above with deploy audit-checkpoints/heads.log,"
echo "then rename databases during a maintenance window (see docs/OPERATIONS.md)."
