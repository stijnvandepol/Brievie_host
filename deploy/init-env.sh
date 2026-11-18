#!/bin/sh
# Creates deploy/.env from deploy/.env.example with freshly generated secrets.
# Leaves an existing deploy/.env alone, except that it appends each of these lines that
# is missing: BRIEVIE_MASTER_KEY (a server from before phase 1b), then
# BRIEVIE_BACKUP_DB_PASSWORD and DATABASE_BACKUP_URL (a server from before B1). Safe to
# run again. Never prints a secret, and never passes one as an argument to a program
# (another local user could read it from the process list): only shell built-ins
# handle the values. BRIEVIE_BACKUP_PASSWORD is not made here; it is derived from the
# master key inside the container (manage.py derive_backup_password).
# Needs openssl or python3. POSTGRES_PORT in the environment sets the host port
# (default 5432) in a new file; an existing file's own POSTGRES_PORT is used for it.
set -eu

dir=$(cd "$(dirname "$0")" && pwd)
# Every file this script writes, appends to, moves or removes is a fixed name in its
# own directory: refuse to go on unless that directory is a real absolute path.
case ${dir:?} in
/?*) ;;
*)
	echo "init-env.sh cannot tell which folder it is in." >&2
	exit 1
	;;
esac
example="$dir/.env.example"
target="$dir/.env"

# Never write through a link: appending would change the file it points to, and a
# dangling link would make a fresh .env appear somewhere else.
if [ -L "$target" ]; then
	echo "deploy/.env is a symbolic link; init-env.sh leaves it alone. Make it a normal file first." >&2
	exit 1
fi

# $1 = "base64" (48 random bytes), "hex" (24 random bytes) or "hex32" (32 random bytes)
random() {
	if command -v openssl >/dev/null 2>&1; then
		case $1 in
		base64) openssl rand -base64 48 | tr -d '\n' ;;
		hex32) openssl rand -hex 32 ;;
		*) openssl rand -hex 24 ;;
		esac
	elif command -v python3 >/dev/null 2>&1; then
		python3 -c "import base64, secrets, sys; k = sys.argv[1]; sys.stdout.write(base64.b64encode(secrets.token_bytes(48)).decode() if k == 'base64' else secrets.token_hex(32 if k == 'hex32' else 24))" "$1"
	else
		echo "init-env.sh needs openssl or python3." >&2
		exit 1
	fi
}

# $1 with the first $2 replaced by $3, by shell expansion only (no sed: its argument
# would show the secret in the process list).
replace() {
	case $1 in
	*"$2"*) printf '%s\n' "${1%%"$2"*}$3${1#*"$2"}" ;;
	*) printf '%s\n' "$1" ;;
	esac
}

# A line of deploy/.env that sets $1, in every spelling docker compose and sh accept:
# leading spaces, `export`, a quoted key, spaces around `=`. Same matcher everywhere,
# so an `export BRIEVIE_MASTER_KEY=...` line is never answered with a second key.
key_line() {
	printf '^[[:space:]]*(export[[:space:]]+)?["'"'"']?%s["'"'"']?[[:space:]]*=' "$1"
}

has_key() {
	grep -Eq "$(key_line "$1")" "$target"
}

# The value of $1 in the existing deploy/.env (the last such line, surrounding quotes
# removed), or nothing. The value leaves sed on stdout, never as an argument.
value_of() {
	found=$(sed -n -E "s/$(key_line "$1")[[:space:]]*//p" "$target" | tail -n 1) || true
	case $found in
	\"*\")
		found=${found#\"}
		found=${found%\"}
		;;
	\'*\')
		found=${found#\'}
		found=${found%\'}
		;;
	esac
	printf '%s\n' "$found"
}

master=$(random hex32)
backup_db_pw=$(random hex)

if [ -e "$target" ]; then
	added=""
	append() {
		# Start on a new line when the file does not end with one.
		[ -z "$(tail -c 1 "$target")" ] || printf '\n' >>"$target"
		printf '%s=%s\n' "$1" "$2" >>"$target"
		added="$added $1"
	}
	has_key BRIEVIE_MASTER_KEY || append BRIEVIE_MASTER_KEY "$master"
	has_key BRIEVIE_BACKUP_DB_PASSWORD ||
		append BRIEVIE_BACKUP_DB_PASSWORD "$backup_db_pw"
	if ! has_key DATABASE_BACKUP_URL; then
		port=$(value_of POSTGRES_PORT)
		append DATABASE_BACKUP_URL \
			"postgres://brievie_backup:$(value_of BRIEVIE_BACKUP_DB_PASSWORD)@localhost:${port:-5432}/brievie"
	fi
	case $added in
	'') echo "deploy/.env exists; left unchanged." ;;
	*BRIEVIE_MASTER_KEY*) echo "deploy/.env exists; added$added (keep a copy of BRIEVIE_MASTER_KEY: without it documents cannot be read)." ;;
	*) echo "deploy/.env exists; added$added." ;;
	esac
	exit 0
fi

secret=$(random base64)
postgres_pw=$(random hex)
owner_pw=$(random hex)
app_pw=$(random hex)
port=${POSTGRES_PORT:-5432}

umask 077
tmp="${dir:?}/.env.tmp.$$"
trap 'rm -f "$tmp"' EXIT

while IFS= read -r line || [ -n "$line" ]; do
	case $line in
	SECRET_KEY=*change-me*) line="SECRET_KEY=$secret" ;;
	POSTGRES_PASSWORD=*change-me*) line="POSTGRES_PASSWORD=$postgres_pw" ;;
	BRIEVIE_OWNER_PASSWORD=*change-me*) line="BRIEVIE_OWNER_PASSWORD=$owner_pw" ;;
	BRIEVIE_APP_PASSWORD=*change-me*) line="BRIEVIE_APP_PASSWORD=$app_pw" ;;
	BRIEVIE_BACKUP_DB_PASSWORD=*change-me*) line="BRIEVIE_BACKUP_DB_PASSWORD=$backup_db_pw" ;;
	DATABASE_URL=*) line=$(replace "$(replace "$line" change-me-app "$app_pw")" :5432/ ":$port/") ;;
	DATABASE_OWNER_URL=*) line=$(replace "$(replace "$line" change-me-owner "$owner_pw")" :5432/ ":$port/") ;;
	DATABASE_BACKUP_URL=*) line=$(replace "$(replace "$line" change-me-backup "$backup_db_pw")" :5432/ ":$port/") ;;
	POSTGRES_PORT=*) line="POSTGRES_PORT=$port" ;;
	BRIEVIE_MASTER_KEY=*) line="BRIEVIE_MASTER_KEY=$master" ;; # exactly 32 bytes
	[A-Z_]*=*change-me*) line="${line%%=*}=$(random hex)" ;; # any other placeholder
	esac
	printf '%s\n' "$line"
done <"$example" >"$tmp"

chmod 600 "$tmp"
# `ln` fails when deploy/.env exists by now (another run made it): never replace a
# .env, whose master key may already be in use. `mv` would replace it silently.
if ! ln "$tmp" "$target" 2>/dev/null; then
	echo "deploy/.env was created by something else meanwhile; left unchanged." >&2
	exit 1
fi
rm -f "$tmp"
trap - EXIT
echo "Created deploy/.env with new random secrets."
