#!/usr/bin/env bash
# Creates the two Brievie roles and the database. Runs once, as the PostgreSQL
# superuser, from /docker-entrypoint-initdb.d (and in CI against the service).
#
# brievie_owner owns the schema and runs migrations. It is not a superuser and
# has no BYPASSRLS, so forced row-level security applies to it as well.
# brievie_app serves requests and may only read and write rows.
set -euo pipefail

: "${BRIEVIE_OWNER_PASSWORD:?BRIEVIE_OWNER_PASSWORD is required}"
: "${BRIEVIE_APP_PASSWORD:?BRIEVIE_APP_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 \
    --username "${POSTGRES_USER:-postgres}" \
    --dbname "${POSTGRES_DB:-postgres}" \
    -v owner_password="$BRIEVIE_OWNER_PASSWORD" \
    -v app_password="$BRIEVIE_APP_PASSWORD" <<'SQL'
CREATE ROLE brievie_owner LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOBYPASSRLS
    PASSWORD :'owner_password';
CREATE ROLE brievie_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
    PASSWORD :'app_password';

CREATE DATABASE brievie OWNER brievie_owner;
REVOKE ALL ON DATABASE brievie FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE brievie TO brievie_app;

\connect brievie
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO brievie_app;
ALTER DEFAULT PRIVILEGES FOR ROLE brievie_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO brievie_app;
ALTER DEFAULT PRIVILEGES FOR ROLE brievie_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO brievie_app;
SQL
