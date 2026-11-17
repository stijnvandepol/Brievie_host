#!/usr/bin/env bash
# Creates the Brievie roles and the database. Runs once, as the PostgreSQL
# superuser, from /docker-entrypoint-initdb.d (and in CI against the service).
#
# brievie_owner owns the schema and runs migrations. It is not a superuser and
# has no BYPASSRLS, so forced row-level security applies to it as well.
# brievie_app serves requests and may only read and write rows.
# brievie_backup only reads, past row-level security, for pg_dump (backup-role.psql).
set -euo pipefail

: "${BRIEVIE_OWNER_PASSWORD:?BRIEVIE_OWNER_PASSWORD is required}"
: "${BRIEVIE_APP_PASSWORD:?BRIEVIE_APP_PASSWORD is required}"
: "${BRIEVIE_BACKUP_DB_PASSWORD:?BRIEVIE_BACKUP_DB_PASSWORD is required}"

# The entrypoint may source this file, so $0 is not this script.
here=$(dirname "${BASH_SOURCE[0]}")
superuser=(--username "${POSTGRES_USER:-postgres}" --dbname "${POSTGRES_DB:-postgres}")

psql -v ON_ERROR_STOP=1 "${superuser[@]}" \
    -v owner_password="$BRIEVIE_OWNER_PASSWORD" \
    -v app_password="$BRIEVIE_APP_PASSWORD" <<'SQL'
CREATE ROLE brievie_owner LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOBYPASSRLS
    PASSWORD :'owner_password';
CREATE ROLE brievie_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
    PASSWORD :'app_password';
SQL
psql -v ON_ERROR_STOP=1 "${superuser[@]}" -f "$here/database.psql"
psql -v ON_ERROR_STOP=1 "${superuser[@]}" \
    -v backup_password="$BRIEVIE_BACKUP_DB_PASSWORD" -f "$here/backup-role.psql"
