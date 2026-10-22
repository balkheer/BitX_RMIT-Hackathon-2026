#!/bin/sh
# Runs once on first database initialisation. Creates the schema-owner and runtime roles
# with passwords read from Docker secrets (never from the image or the compose file).
set -eu
OWNER_PW="$(cat /run/secrets/pg_owner_password)"
APP_PW="$(cat /run/secrets/pg_app_password)"
psql -v ON_ERROR_STOP=1 --username postgres --dbname consentbridge \
  -v owner_pw="$OWNER_PW" -v app_pw="$APP_PW" <<'SQL'
CREATE ROLE cb_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE cb_app LOGIN PASSWORD :'app_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
ALTER DATABASE consentbridge OWNER TO cb_owner;
ALTER SCHEMA public OWNER TO cb_owner;
REVOKE ALL ON DATABASE consentbridge FROM PUBLIC;
GRANT CONNECT ON DATABASE consentbridge TO cb_app;
SQL
