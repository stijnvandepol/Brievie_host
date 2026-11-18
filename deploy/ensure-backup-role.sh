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

# The compose project: COMPOSE_PROJECT_NAME from the environment, else from deploy/.env,
# else brievie; always passed with -p, so this never reaches another installation.
project=${COMPOSE_PROJECT_NAME:-}
if [ -z "$project" ]; then
	# The same expression as the Makefile's (a text test compares them): the plain and
	# the `export` form, the last line wins.
	project=$(sed -n 's/^[[:space:]]*\(export[[:space:]][[:space:]]*\)\{0,1\}COMPOSE_PROJECT_NAME=//p' "$env_file" 2>/dev/null | tail -n 1) || true
fi
project=${project:-brievie}
case $project in
[!a-z0-9]* | *[!a-z0-9_-]*)
	echo "COMPOSE_PROJECT_NAME may only hold lower-case letters, digits, - and _." >&2
	exit 1
	;;
esac

compose() {
	docker compose -p "$project" --project-directory deploy -f deploy/docker-compose.yml "$@"
}

password=$(grep '^BRIEVIE_BACKUP_DB_PASSWORD=' "$env_file" 2>/dev/null | tail -n 1 | cut -d= -f2-) || true
if [ -z "$password" ]; then
	echo "deploy/.env has no BRIEVIE_BACKUP_DB_PASSWORD; run sh deploy/init-env.sh first." >&2
	exit 1
fi

if [ -z "$(compose ps --status running --quiet postgres 2>/dev/null)" ]; then
	echo "PostgreSQL is not running; start it first (make dev)." >&2
	exit 1
fi

# Inside psql's single quotes a quote is doubled and a backslash escaped.
quoted=$(printf '%s' "$password" | sed -e 's/\\/\\\\/g' -e "s/'/''/g")
printf "\\\\set backup_password '%s'\n\\\\i /docker-entrypoint-initdb.d/backup-role.psql\n" "$quoted" |
	compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 -q
