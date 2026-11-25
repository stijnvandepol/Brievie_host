#!/usr/bin/env bash
# The CI jobs `stack` and `compose-stack` ask the running server for the recovery sheet
# through the web, as a person would: set the server up with the installation code, sign
# in, POST createRecoverySheet.
#
#   SETUP_TOKEN=<code> recovery-sheet-check.sh 409     a ./brievie install: refused
#   SETUP_TOKEN=<code> MASTER_KEY_GROUPS="ab12 ..." recovery-sheet-check.sh 200
#                                                      a compose install: the sheet, with the key
#
# The password is made here and never printed; neither is the sheet. Needs RUNNER_TEMP.
set -euo pipefail
umask 077

expected=${1:?usage: recovery-sheet-check.sh STATUS}
base=https://localhost/api/v1
work=$(mktemp -d "${RUNNER_TEMP:?}/sheet-check.XXXXXX")
trap 'rm -rf "${work:?}"' EXIT
CI_PASSWORD=$(openssl rand -hex 16)
export CI_PASSWORD
echo "::add-mask::$CI_PASSWORD"
: "${SETUP_TOKEN:?set SETUP_TOKEN}"
export SETUP_TOKEN

curl_web() {
	curl --silent --show-error --insecure --cookie "$work/jar" --cookie-jar "$work/jar" \
		--header 'Content-Type: application/json' --header 'Referer: https://localhost/' \
		--header 'Origin: https://localhost' "$@"
}

jq -n '{setup_token: $ENV.SETUP_TOKEN, email: "ci@example.invalid", password: $ENV.CI_PASSWORD,
        name: "CI", workspace_name: "CI", workspace_kind: "home"}' > "$work/setup.json"
status=$(curl_web --output /dev/null --write-out '%{http_code}' --data @"$work/setup.json" "$base/setup")
test "$status" = 201 || { echo "Setting the server up answered $status." >&2; exit 1; }

csrf() { awk '$6 == "csrftoken" { print $7 }' "$work/jar" | tail -n 1; }
if [ -z "$(csrf)" ]; then
	curl_web --output /dev/null "$base/me" || true
fi
token=$(csrf)
test -n "$token" || { echo "No CSRF cookie after setting up." >&2; exit 1; }

status=$(curl_web --request POST --header "X-CSRFToken: $token" --dump-header "$work/headers" \
	--output "$work/body" --write-out '%{http_code}' "$base/server/recovery-sheet")
if [ "$status" != "$expected" ]; then
	echo "The recovery sheet answered $status, expected $expected." >&2
	exit 1
fi
if [ "$expected" = 200 ]; then
	grep -qi '^cache-control: no-store' "$work/headers"
	grep -qi '^content-disposition: attachment' "$work/headers"
	grep -qF "${MASTER_KEY_GROUPS:?set MASTER_KEY_GROUPS}" "$work/body"
else
	# No key, no sheet: only the error sentence.
	test "$(jq -r .code "$work/body")" = recovery_sheet_not_here
fi
# The signed-in admin's cookies for a later check (routes-check.sh session), when asked.
if [ -n "${KEEP_JAR:-}" ]; then
	cp "$work/jar" "$KEEP_JAR"
fi
echo "Recovery sheet check passed ($expected)."
