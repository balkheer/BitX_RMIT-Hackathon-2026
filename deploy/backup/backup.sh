#!/bin/sh
# Logical backups with retention. `backup.sh once` takes one backup; `backup.sh loop` runs on an interval.
set -eu
export PGPASSWORD="$(cat /run/secrets/pg_superuser_password)"
DIR=/backups
mkdir -p "$DIR"
take() {
  ts=$(date -u +%Y%m%dT%H%M%SZ)
  f="$DIR/consentbridge-$ts.dump"
  pg_dump -h db -U postgres -d consentbridge --format=custom --no-owner --file="$f.partial"
  mv "$f.partial" "$f"
  sha256sum "$f" > "$f.sha256"
  find "$DIR" -name 'consentbridge-*.dump*' -mtime +"${BACKUP_RETENTION_DAYS:-14}" -delete
  echo "backup ok $f"
}
case "${1:-once}" in
  once) take ;;
  loop) while true; do take || echo "backup FAILED" >&2; sleep "${BACKUP_INTERVAL_SECONDS:-86400}"; done ;;
esac
