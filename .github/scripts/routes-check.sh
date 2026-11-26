#!/usr/bin/env bash
# The assertions of the CI jobs `stack` and `compose-stack` about the ways in ("Van buiten
# bereikbaar", docs/SELFHOST.md section 25). Brievie runs no tunnel: a user's own cloudflared
# (or Tailscale, or reverse proxy) is simulated by what it sends to Caddy's https port, a
# request for the public name with an X-Forwarded-For from an address that is not in
# BRIEVIE_TRUSTED_PROXIES.
#
#   routes-check.sh caddy        the adapted Caddyfile: one listener, nobody reads CF-Connecting-IP
#   routes-check.sh listeners    HSTS per route, the public name as a user's own cloudflared sends
#                                it, 413s, forged route headers, the VPN name, a request without SNI
#   routes-check.sh session JAR  a signed-in server admin's view (getExternalAccess) at home with
#                                forged address headers, and for the public name (closed unless
#                                BRIEVIE_PUBLIC_WEB; then a session started at home without the
#                                second step counts as none (and only because of that step), a
#                                password sign-in gets the outside sentence, and a forwarded
#                                address from an untrusted peer changes nothing)
#   routes-check.sh oldenv       Caddy with only the 0.5.0 variable: the public name is still public
#   routes-check.sh isolation    a request that reaches the api without Caddy counts as public
#
# Needs COMPOSE, PUBLIC_HOST (the public name the stack serves), VPN_HOST and RUNNER_TEMP.
set -euo pipefail
umask 077

: "${COMPOSE:?}" "${PUBLIC_HOST:?}" "${VPN_HOST:?}"
work=$(mktemp -d "${RUNNER_TEMP:?}/routes-check.XXXXXX")
trap 'rm -rf "${work:?}"' EXIT
# Which check failed: the line, never a value.
set -E
trap 'echo "routes-check.sh: a check failed at line $LINENO." >&2' ERR

# Whether the session with key $1 counts as having passed the second step ($2: True|False),
# straight in the database from inside the api container. Fails unless one row changed.
mark_passed() {
	$COMPOSE exec -T api python - "$1" "$2" <<'PY'
import os, sys
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "brievie.settings")
django.setup()
from apps.accounts.models import UserSession
key, passed = sys.argv[1], sys.argv[2] == "True"
assert UserSession.objects.filter(session_key=key).update(second_factor=passed) == 1
PY
}

# A request as a user's own cloudflared sends it: the https port, Host = the public name.
outside() {
	curl --silent --insecure --resolve "$PUBLIC_HOST:443:127.0.0.1" "$@"
}

header_of() { sed -n 2p "$1" | python3 -c "import json,sys; print(json.load(sys.stdin).get('$2', ''))"; }
status_of() { sed -n 1p "$1"; }
body_of() { sed -n '3,$p' "$1"; }

# Caddy was recreated with only the 0.5.0 compose file's variable (BRIEVIE_TUNNEL_HOST): a request
# for the public name with no proxy header at all must still be the public route, which
# pairing tells (403 pair_at_home).
check_oldenv() {
	answer=$(outside --retry 10 --retry-delay 2 --retry-all-errors --request POST \
		--header 'Content-Type: application/json' "https://$PUBLIC_HOST/api/v1/devices/pairing-challenges")
	test "$(jq -r .code <<<"$answer")" = pair_at_home
	echo "Old compose variable: the public name is still the public route."
}

check_caddy() {
	$COMPOSE exec -T caddy caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile >"$work/caddy.json"
	python3 - "$work/caddy.json" <<'PY'
import json, sys
config = json.load(open(sys.argv[1]))
servers = config["apps"]["http"]["servers"]
assert len(servers) == 1, "one server"
for name, server in servers.items():
    assert server.get("listen") == [":443"], (name, server.get("listen"))
    assert server.get("client_ip_headers") is None, (name, server.get("client_ip_headers"))
    assert server["trusted_proxies"]["ranges"], name
proxies = []
def walk(node):
    if isinstance(node, dict):
        if node.get("handler") == "reverse_proxy":
            proxies.append(node)
        for value in node.values():
            walk(value)
    elif isinstance(node, list):
        for value in node:
            walk(value)
walk(config)
assert proxies, "no reverse_proxy"
for proxy in proxies:
    # Caddy stores header names in their canonical spelling (X-Brievie-Client-Ip).
    up = {name.lower(): value for name, value in proxy["headers"]["request"]["set"].items()}
    for header in ("X-Brievie-Route", "X-Brievie-Client-IP", "X-Brievie-Peer-IP", "Host"):
        assert header.lower() in up, (header, up)
    assert up["x-brievie-client-ip"] == ["{http.vars.client_ip}"], up
print(f"Caddy: {len(proxies)} proxies set the edge headers; one listener; no CF-Connecting-IP.")
PY
}

check_listeners() {
	# The home name: HSTS 0 (the private CA), as before.
	curl --silent --show-error --insecure --dump-header "$work/home.h" --output /dev/null \
		https://localhost/api/v1/health
	grep -qi '^strict-transport-security: max-age=0' "$work/home.h"
	# A user's own cloudflared: the https port, Host = the public name, X-Forwarded-For from an
	# address that is not in BRIEVIE_TRUSTED_PROXIES. It is the public route and the app's own
	# paths answer.
	code=$(outside --output /dev/null --write-out '%{http_code}' \
		--header 'X-Forwarded-For: 198.51.100.1' "https://$PUBLIC_HOST/api/v1/health")
	test "$code" = 200
	head -c 3000000 /dev/zero | tr '\0' x >"$work/big"
	code=$(curl --silent --insecure --dump-header "$work/big.h" --output /dev/null --write-out '%{http_code}' \
		--header 'Content-Type: application/json' --data-binary @"$work/big" https://localhost/api/v1/auth/login)
	test "$code" = 413
	grep -qi '^strict-transport-security: max-age=0' "$work/big.h"
	# A forged route header changes nothing: for the public name "private" is overwritten to
	# public, so a session-only operation is closed; on :443 at home "public" is overwritten to
	# private, so it is only unauthenticated.
	forged=$(outside --header 'X-Brievie-Route: private' --header 'X-Forwarded-For: 198.51.100.1' \
		"https://$PUBLIC_HOST/api/v1/server/audit-events")
	if [ "${PUBLIC_WEB:-false}" = true ]; then
		# The web is open on the public address: the operation's own auth answers.
		test "$(jq -r .code <<<"$forged")" = not_authenticated
	else
		test "$(jq -r .code <<<"$forged")" = public_web_off
		# A junk Bearer header opens nothing either.
		code=$(outside --output /dev/null --write-out '%{http_code}' --request POST \
			--header 'Authorization: Bearer junk' --header 'X-Brievie-Route: private' \
			--header 'X-Forwarded-For: 198.51.100.1' --header 'Content-Type: application/json' \
			"https://$PUBLIC_HOST/api/v1/auth/login")
		test "$code" = 403
	fi
	body=$(curl --silent --insecure --header 'X-Brievie-Route: public' \
		https://localhost/api/v1/server/audit-events)
	test "$(jq -r .code <<<"$body")" = not_authenticated
	# The route is decided by name first (security review of PR #53): the public name from an
	# untrusted source, and Cloudflare's headers on :443, are public whatever the source.
	pub=$(curl --silent --insecure --resolve "$PUBLIC_HOST:443:127.0.0.1" \
		--header 'X-Brievie-Route: private' --header 'X-Forwarded-For: 198.51.100.1' \
		"https://$PUBLIC_HOST/api/v1/server/audit-events")
	cf=$(curl --silent --insecure --header 'Cf-Ray: 0123456789abcdef-AMS' \
		https://localhost/api/v1/server/audit-events)
	if [ "${PUBLIC_WEB:-false}" = true ]; then
		test "$(jq -r .code <<<"$pub")" = not_authenticated
		test "$(jq -r .code <<<"$cf")" = not_authenticated
	else
		test "$(jq -r .code <<<"$pub")" = public_web_off
		test "$(jq -r .code <<<"$cf")" = public_web_off
		code=$(curl --silent --insecure --output /dev/null --write-out '%{http_code}' \
			--resolve "$PUBLIC_HOST:443:127.0.0.1" "https://$PUBLIC_HOST/")
		test "$code" = 403
	fi
	# A proxy that is not trusted yet (re-review of PR #53): each of its headers on :443 from an
	# untrusted source makes the request public, whatever its Host. Pairing tells public from
	# private in both modes (public: 403 pair_at_home).
	for header in 'X-Forwarded-For: 198.51.100.1' 'X-Forwarded-Host: localhost' \
		'X-Forwarded-Proto: https' 'X-Real-IP: 198.51.100.1' 'Forwarded: for=198.51.100.1' \
		'Via: 1.1 proxy'; do
		answer=$(curl --silent --insecure --request POST --header 'Content-Type: application/json' \
			--header "$header" https://localhost/api/v1/devices/pairing-challenges)
		test "$(jq -r .code <<<"$answer")" = pair_at_home || {
			echo "Not public with: ${header%%:*}" >&2
			exit 1
		}
	done
	# Pairing and the server's addresses never go through the public name (pair_at_home), and
	# the app's own paths stay open there.
	answer=$(outside --request POST --header 'Content-Type: application/json' \
		--header 'X-Forwarded-For: 198.51.100.1' "https://$PUBLIC_HOST/api/v1/devices/pairing-challenges")
	test "$(jq -r .code <<<"$answer")" = pair_at_home
	code=$(outside --output /dev/null --write-out '%{http_code}' \
		--header 'X-Forwarded-For: 198.51.100.1' "https://$PUBLIC_HOST/api/v1/server/ca-certificate")
	test "$code" = 200
	# An unknown Host is served by no site: it never reaches the api.
	other=$(curl --silent --insecure --resolve "unknown.example.test:443:127.0.0.1" \
		https://unknown.example.test/api/v1/health || true)
	if grep -q server_version <<<"$other"; then
		echo "An unknown Host reached the api." >&2
		exit 1
	fi
	# The VPN name is served by the private CA, and a call without SNI gets a certificate.
	curl --fail --silent --show-error --insecure --resolve "$VPN_HOST:443:127.0.0.1" \
		"https://$VPN_HOST/api/v1/health" >/dev/null
	curl --fail --silent --show-error --insecure --header 'Host: localhost' \
		https://127.0.0.1/api/v1/health >/dev/null
	echo "Listeners: HSTS per route, a user's own cloudflared, 413s, forged routes, VPN name and no SNI."
}

check_session() {
	jar=${1:?usage: routes-check.sh session JAR}
	session=$(awk '$6 == "sessionid" { print $7 }' "$jar" | tail -n 1)
	test -n "$session" || { echo "No session in the cookie jar." >&2; exit 1; }
	echo "::add-mask::$session"
	# :443 with forged address and route headers: Brievie sees the peer, private, https.
	# (CF-Connecting-IP on :443 would make the request public: checked below.)
	# (A proxy's header such as X-Forwarded-For makes it public: checked below and in listeners.)
	curl --silent --show-error --insecure --cookie "sessionid=$session" \
		--header 'X-Brievie-Client-IP: 198.51.100.3' --header 'X-Brievie-Route: public' \
		--header 'X-Brievie-Peer-IP: 198.51.100.4' --header 'X-Brievie-Via: proxy' \
		https://localhost/api/v1/server/external-access >"$work/home.json"
	python3 - "$work/home.json" <<'PY'
import json, sys
seen = json.load(open(sys.argv[1]))["seen_as"]
assert seen["route"] == "private", seen
assert seen["secure"] is True, seen
assert seen["client_ip"] == seen["peer_ip"], seen
assert not seen["client_ip"].startswith("198.51.100."), seen
print("Home route: forged address headers change nothing:", seen["route"], "https")
codes = [c["code"] for c in json.load(open(sys.argv[1]))["checks"]]
# Seen by the listeners check before: the public name from an untrusted source (a user's own
# cloudflared that is not in BRIEVIE_TRUSTED_PROXIES yet).
assert "public_name_from_untrusted_source" in codes, codes
assert "cloudflare_on_https_port" not in codes, codes
assert "unknown_proxy" in codes, codes
print("The page shows what the server saw:", codes)
PY
	# CF-Connecting-IP on :443: public (point 1 of the review), and still the peer counts.
	code=$(curl --silent --insecure --cookie "sessionid=$session" --output "$work/cf.json" \
		--write-out '%{http_code}' --header 'CF-Connecting-IP: 198.51.100.2' \
		--header 'X-Forwarded-For: 198.51.100.1' \
		https://localhost/api/v1/server/external-access)
	if [ "${PUBLIC_WEB:-false}" = true ]; then
		# The session began at home without the second step: on the public route it does not
		# count (two-step plan Task 13), so this is the public route.
		test "$code" = 401
		test "$(jq -r .code "$work/cf.json")" = not_authenticated
		echo "Cloudflare's and a proxy's headers on :443: public, a session without the code is none."
	else
		test "$code" = 403
		test "$(jq -r .code "$work/cf.json")" = public_web_off
	fi
	# The same session for the public name, as a user's own cloudflared forwards it: the https
	# port, Host = the public name, an X-Forwarded-For from an untrusted peer.
	forward=(--header 'X-Forwarded-For: 198.51.100.1' --header 'X-Brievie-Client-IP: 198.51.100.3'
		--header 'X-Brievie-Route: private')
	code=$(outside --cookie "sessionid=$session" --output "$work/pub.json" --write-out '%{http_code}' \
		"${forward[@]}" "https://$PUBLIC_HOST/api/v1/server/external-access")
	if [ "${PUBLIC_WEB:-false}" = true ]; then
		# The web is on outside, but the home session without the second step does not count.
		test "$code" = 401
		test "$(jq -r .code "$work/pub.json")" = not_authenticated
		# The cookie did arrive: the same request for the same session, marked as having
		# passed the second step, is let in. So the 401 above is the session refused.
		mark_passed "$session" True
		code=$(outside --cookie "sessionid=$session" --output "$work/pub.json" --write-out '%{http_code}' \
			"${forward[@]}" "https://$PUBLIC_HOST/api/v1/server/external-access")
		mark_passed "$session" False
		test "$code" = 200
		test "$(jq -r .seen_as.route "$work/pub.json")" = public
		# The forwarded address of an untrusted peer is not believed: the visitor is the peer.
		python3 - "$work/pub.json" <<'PY'
import json, sys
seen = json.load(open(sys.argv[1]))["seen_as"]
assert seen["client_ip"] == seen["peer_ip"], seen
assert not seen["client_ip"].startswith("198.51.100."), seen
PY
		# A password sign-in for the public name answers the outside sentence (an account
		# without a factor gets the same answer as a wrong password).
		wrong=$(head -c 18 /dev/urandom | base64)
		login=$(jq -cn --arg p "$wrong" '{email: "ci@example.invalid", password: $p}')
		outside --request POST --header 'Content-Type: application/json' --data "$login" \
			"https://$PUBLIC_HOST/api/v1/auth/login" >"$work/t"
		jq -e '.code == "invalid_credentials" and (.message | contains("tweestapsverificatie"))' "$work/t" >/dev/null
		# The visitor's address is the peer, never a forwarded header: six wrong passwords for
		# one new address, each with another X-Forwarded-For, lock that peer (axes, after 5);
		# the per-account slow-down starts only after 10, so only the address makes the sixth a 429.
		email="ci-$(head -c 6 /dev/urandom | od -An -tx1 | tr -d ' \n')@example.invalid"
		login=$(jq -cn --arg e "$email" --arg p "$wrong" '{email: $e, password: $p}')
		for attempt in 1 2 3 4 5 6; do
			code=$(outside --output /dev/null --write-out '%{http_code}' --request POST \
				--header 'Content-Type: application/json' \
				--header "X-Forwarded-For: 198.51.100.$((20 + attempt))" --data "$login" \
				"https://$PUBLIC_HOST/api/v1/auth/login")
			if [ "$attempt" -lt 6 ]; then
				test "$code" = 401
			else
				test "$code" = 429
			fi
		done
		echo "Public route: the visitor is the peer; X-Forwarded-For from an untrusted peer changes nothing."
		echo "Public route: a session needs the second step, and so does a password sign-in."
	else
		test "$code" = 403
		test "$(jq -r .code "$work/pub.json")" = public_web_off
		echo "Public route: a web session is closed while BRIEVIE_PUBLIC_WEB is off."
	fi
}

check_isolation() {
	project=${COMPOSE_PROJECT:-brievie}
	image=$(docker inspect "$($COMPOSE ps -q caddy)" --format '{{.Config.Image}}')
	# A container that does reach the api directly (here the worker) cannot pose as Caddy.
	$COMPOSE exec -T worker python - <<'PY' >"$work/bypass"
import http.client, json
conn = http.client.HTTPConnection("api", 8000, timeout=10)
conn.request("GET", "/api/v1/server/audit-events", headers={
    "Host": "localhost", "X-Brievie-Route": "private", "X-Brievie-Via": "home",
    "X-Brievie-Client-IP": "198.51.100.2", "X-Forwarded-Proto": "https"})
resp = conn.getresponse()
print(resp.status)
print(json.loads(resp.read())["code"])
PY
	if [ "${PUBLIC_WEB:-false}" = true ]; then
		test "$(sed -n 2p "$work/bypass")" = not_authenticated
	else
		test "$(sed -n 1p "$work/bypass")" = 403
		test "$(sed -n 2p "$work/bypass")" = public_web_off
	fi
	echo "Isolation: a request around Caddy is public."
}

case ${1:-} in
caddy) check_caddy ;;
isolation) check_isolation ;;
oldenv) check_oldenv ;;
listeners) check_listeners ;;
session) check_session "${2:-}" ;;
*)
	echo "usage: routes-check.sh caddy|listeners|isolation|oldenv|session JAR" >&2
	exit 2
	;;
esac
