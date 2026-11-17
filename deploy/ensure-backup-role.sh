#!/bin/sh
# Creates the read-only back-up role brievie_backup on a running PostgreSQL, or sets
# its attributes and password again (deploy/postgres-init/backup-role.psql). A new
# server gets the role from 01-roles.sh; this script brings an existing one up to date
# and is safe to run any number of times. Run it from the repository root.
#
# The password comes from deploy/.env and goes to psql on stdin, so it is in no
# command line. Never prints a secret.
set -eu

env_file=deploy/.env
compose="docker compose --project-directory deploy -f deploy/docker-compose.yml"

password=$(grep '^BRIEVIE_BACKUP_DB_PASSWORD=' "$env_file" 2>/dev/null | tail -n 1 | cut -d= -f2-) || true
if [ -z "$password" ]; then
	echo "deploy/.env has no BRIEVIE_BACKUP_DB_PASSWORD; run sh deploy/init-env.sh first." >&2
	exit 1
fi

if [ -z "$($compose ps --status running --quiet postgres 2>/dev/null)" ]; then
	echo "PostgreSQL is not running; start it first (make dev)." >&2
	exit 1
fi

# Inside psql's single quotes a quote is doubled and a backslash escaped.
quoted=$(printf '%s' "$password" | sed -e 's/\\/\\\\/g' -e "s/'/''/g")
printf "\\\\set backup_password '%s'\n\\\\i /docker-entrypoint-initdb.d/backup-role.psql\n" "$quoted" |
	$compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 -q
