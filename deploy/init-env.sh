#!/bin/sh
# Creates deploy/.env from deploy/.env.example with freshly generated secrets.
# Leaves an existing deploy/.env alone, except that it appends each of these lines that
# is missing: BRIEVIE_MASTER_KEY (a server from before phase 1b), then
# BRIEVIE_BACKUP_DB_PASSWORD, DATABASE_BACKUP_URL and BRIEVIE_BACKUP_PASSWORD (a server
# from before B1; the last is derived from the file's own master key). Safe to run
# again. Never prints a secret.
# Needs openssl or python3. POSTGRES_PORT in the environment sets the host port
# (default 5432) in a new file; an existing file's own POSTGRES_PORT is used for it.
set -eu

dir=$(cd "$(dirname "$0")" && pwd)
example="$dir/.env.example"
target="$dir/.env"

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

# The back-up password derived from the master key $1 (64 hex characters):
# HMAC-SHA256(key, "brievie/backup-password/v1" || 0x01), which is HKDF-Expand with that
# label (apps.common.crypto.derive_backup_password). Never change the label.
# python3 first: it reads the key from stdin. openssl only takes it on its command line,
# where another local user could see it, so it is the fallback for hosts without python3.
derive() {
	case $1 in
	*[!0-9a-fA-F]* | '') bad_key=1 ;;
	*) bad_key=$((${#1} != 64)) ;;
	esac
	if [ "$bad_key" -ne 0 ]; then
		echo "BRIEVIE_MASTER_KEY in deploy/.env is not 64 hexadecimal characters; the back-up password cannot be derived from it." >&2
		exit 1
	fi
	if command -v python3 >/dev/null 2>&1; then
		derived=$(printf '%s' "$1" | python3 -c "import hashlib, hmac, sys; key = bytes.fromhex(sys.stdin.read()); sys.stdout.write(hmac.new(key, b'brievie/backup-password/v1\x01', hashlib.sha256).hexdigest())")
	elif command -v openssl >/dev/null 2>&1; then
		derived=$(printf 'brievie/backup-password/v1\001' |
			openssl dgst -sha256 -mac HMAC -macopt "hexkey:$1" | sed 's/^.*= *//')
	else
		echo "init-env.sh needs openssl or python3." >&2
		exit 1
	fi
	if [ ${#derived} -ne 64 ]; then
		echo "init-env.sh could not derive the back-up password." >&2
		exit 1
	fi
	printf '%s' "$derived"
}

# The value of $1 in the existing deploy/.env (the last such line), or nothing.
value_of() {
	grep "^$1=" "$target" | tail -n 1 | cut -d= -f2- || true
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
	grep -q '^BRIEVIE_MASTER_KEY=' "$target" || append BRIEVIE_MASTER_KEY "$master"
	# Derived before anything else is added, so a bad master key changes nothing more.
	derived=""
	grep -q '^BRIEVIE_BACKUP_PASSWORD=' "$target" ||
		derived=$(derive "$(value_of BRIEVIE_MASTER_KEY)")
	grep -q '^BRIEVIE_BACKUP_DB_PASSWORD=' "$target" ||
		append BRIEVIE_BACKUP_DB_PASSWORD "$backup_db_pw"
	if ! grep -q '^DATABASE_BACKUP_URL=' "$target"; then
		port=$(value_of POSTGRES_PORT)
		append DATABASE_BACKUP_URL \
			"postgres://brievie_backup:$(value_of BRIEVIE_BACKUP_DB_PASSWORD)@localhost:${port:-5432}/brievie"
	fi
	[ -z "$derived" ] || append BRIEVIE_BACKUP_PASSWORD "$derived"
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
backup_pw=$(derive "$master")
port=${POSTGRES_PORT:-5432}

umask 077
tmp="$target.tmp.$$"
trap 'rm -f "$tmp"' EXIT

while IFS= read -r line || [ -n "$line" ]; do
	case $line in
	SECRET_KEY=*change-me*) line="SECRET_KEY=$secret" ;;
	POSTGRES_PASSWORD=*change-me*) line="POSTGRES_PASSWORD=$postgres_pw" ;;
	BRIEVIE_OWNER_PASSWORD=*change-me*) line="BRIEVIE_OWNER_PASSWORD=$owner_pw" ;;
	BRIEVIE_APP_PASSWORD=*change-me*) line="BRIEVIE_APP_PASSWORD=$app_pw" ;;
	BRIEVIE_BACKUP_DB_PASSWORD=*change-me*) line="BRIEVIE_BACKUP_DB_PASSWORD=$backup_db_pw" ;;
	DATABASE_URL=*) line=$(printf '%s\n' "$line" | sed "s|change-me-app|$app_pw|; s|:5432/|:$port/|") ;;
	DATABASE_OWNER_URL=*) line=$(printf '%s\n' "$line" | sed "s|change-me-owner|$owner_pw|; s|:5432/|:$port/|") ;;
	DATABASE_BACKUP_URL=*) line=$(printf '%s\n' "$line" | sed "s|change-me-backup|$backup_db_pw|; s|:5432/|:$port/|") ;;
	BRIEVIE_BACKUP_PASSWORD=*change-me*) line="BRIEVIE_BACKUP_PASSWORD=$backup_pw" ;;
	POSTGRES_PORT=*) line="POSTGRES_PORT=$port" ;;
	BRIEVIE_MASTER_KEY=*) line="BRIEVIE_MASTER_KEY=$master" ;; # exactly 32 bytes
	[A-Z_]*=*change-me*) line="${line%%=*}=$(random hex)" ;; # any other placeholder
	esac
	printf '%s\n' "$line"
done <"$example" >"$tmp"

chmod 600 "$tmp"
mv "$tmp" "$target"
trap - EXIT
echo "Created deploy/.env with new random secrets."
