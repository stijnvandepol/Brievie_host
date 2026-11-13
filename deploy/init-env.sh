#!/bin/sh
# Creates deploy/.env from deploy/.env.example with freshly generated secrets.
# Never touches an existing deploy/.env. Never prints a secret.
# Needs openssl or python3. POSTGRES_PORT in the environment sets the host port
# (default 5432) in the new file.
set -eu

dir=$(cd "$(dirname "$0")" && pwd)
example="$dir/.env.example"
target="$dir/.env"

if [ -e "$target" ]; then
	echo "deploy/.env exists; left unchanged."
	exit 0
fi

# $1 = "base64" (48 random bytes) or "hex" (24 random bytes)
random() {
	if command -v openssl >/dev/null 2>&1; then
		if [ "$1" = base64 ]; then openssl rand -base64 48 | tr -d '\n'; else openssl rand -hex 24; fi
	elif command -v python3 >/dev/null 2>&1; then
		python3 -c "import base64, secrets, sys; b = secrets.token_bytes(48); sys.stdout.write(base64.b64encode(b).decode() if sys.argv[1] == 'base64' else b[:24].hex())" "$1"
	else
		echo "init-env.sh needs openssl or python3." >&2
		exit 1
	fi
}

secret=$(random base64)
postgres_pw=$(random hex)
owner_pw=$(random hex)
app_pw=$(random hex)
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
	DATABASE_URL=*) line=$(printf '%s\n' "$line" | sed "s|change-me-app|$app_pw|; s|:5432/|:$port/|") ;;
	DATABASE_OWNER_URL=*) line=$(printf '%s\n' "$line" | sed "s|change-me-owner|$owner_pw|; s|:5432/|:$port/|") ;;
	POSTGRES_PORT=*) line="POSTGRES_PORT=$port" ;;
	[A-Z_]*=*change-me*) line="${line%%=*}=$(random hex)" ;; # any other placeholder
	esac
	printf '%s\n' "$line"
done <"$example" >"$tmp"

chmod 600 "$tmp"
mv "$tmp" "$target"
trap - EXIT
echo "Created deploy/.env with new random secrets."
