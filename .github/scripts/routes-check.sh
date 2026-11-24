#!/usr/bin/env bash
# The assertions of the CI jobs `stack` and `compose-stack` about the ways in ("Van buiten
# bereikbaar", docs/SELFHOST.md section 25). No real tunnel: the tunnel listener :8080 is
# reached from inside the compose network, as cloudflared reaches it.
#
#   routes-check.sh caddy        the adapted Caddyfile: who reads CF-Connecting-IP, edge headers
#   routes-check.sh listeners    HSTS per route, the tunnel's redirect and Host, 413s, forged
#                                route headers, the VPN name, a request without SNI
#   routes-check.sh session JAR  a signed-in server admin's view (getExternalAccess) on :443
#                                with forged address headers, and on :8080 (closed unless
#                                BRIEVIE_PUBLIC_WEB; then the route, https and the address)
#   routes-check.sh token NAME   no value of the file NAME (a planted tunnel token) in any
#                                container but cloudflared, `compose config`, a log or a process
#
# Needs COMPOSE, PUBLIC_HOST (the public name the stack serves), VPN_HOST and RUNNER_TEMP.
set -euo pipefail
umask 077

: "${COMPOSE:?}" "${PUBLIC_HOST:?}" "${VPN_HOST:?}"
work=$(mktemp -d "${RUNNER_TEMP:?}/routes-check.XXXXXX")
trap 'rm -rf "${work:?}"' EXIT

# One HTTP request from inside the api container to Caddy (the compose network).
# Prints "<status>\n<headers as JSON>\n<body>".
inside() { # $1 method, $2 host:port, $3 path, $4 JSON of headers, $5 body size (bytes)
	$COMPOSE exec -T api python - "$@" <<'PY'
import http.client, json, sys
method, target, path, headers, size = sys.argv[1:6]
host, port = target.split(":")
conn = http.client.HTTPConnection(host, int(port), timeout=10)
body = b"x" * int(size) if int(size) else None
try:
    conn.request(method, path, body=body, headers=json.loads(headers))
except (BrokenPipeError, ConnectionResetError):
    pass  # Caddy answered 413 before the whole body was sent; its answer is still read.
resp = conn.getresponse()
print(resp.status)
print(json.dumps({k.lower(): v for k, v in resp.getheaders()}))
print(resp.read().decode("utf-8", "replace"))
PY
}

header_of() { sed -n 2p "$1" | python3 -c "import json,sys; print(json.load(sys.stdin).get('$2', ''))"; }
status_of() { sed -n 1p "$1"; }
body_of() { sed -n '3,$p' "$1"; }

check_caddy() {
	$COMPOSE exec -T caddy caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile >"$work/caddy.json"
	python3 - "$work/caddy.json" <<'PY'
import json, sys
config = json.load(open(sys.argv[1]))
servers = config["apps"]["http"]["servers"]
tunnel = [s for s in servers.values() if s.get("listen") == [":8080"]]
assert len(tunnel) == 1, "one server on :8080"
for name, server in servers.items():
    headers = server.get("client_ip_headers")
    if server.get("listen") == [":8080"]:
        assert headers == ["CF-Connecting-IP"], (name, headers)
        assert server["trusted_proxies"]["ranges"], name
    else:
        assert headers is None, (name, headers)
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
print(f"Caddy: {len(proxies)} proxies set the edge headers; only :8080 reads CF-Connecting-IP.")
PY
}

check_listeners() {
	# The home name: HSTS 0 (the private CA), as before.
	curl --silent --show-error --insecure --dump-header "$work/home.h" --output /dev/null \
		https://localhost/api/v1/health
	grep -qi '^strict-transport-security: max-age=0' "$work/home.h"
	# The tunnel listener: 200 with one year of HSTS and the public name as Host.
	inside GET caddy:8080 /api/v1/health '{"X-Forwarded-Proto": "https"}' 0 >"$work/t"
	test "$(status_of "$work/t")" = 200
	test "$(header_of "$work/t" strict-transport-security)" = "max-age=31536000"
	# Cloudflare got it over plain http: 308 to https on the public name.
	inside GET caddy:8080 /api/v1/health '{"X-Forwarded-Proto": "http"}' 0 >"$work/t"
	test "$(status_of "$work/t")" = 308
	test "$(header_of "$work/t" location)" = "https://$PUBLIC_HOST/api/v1/health"
	# A body over the limit: 413 in the API's shape, with the route's HSTS.
	inside POST caddy:8080 /api/v1/auth/login '{"X-Forwarded-Proto": "https", "Content-Type": "application/json"}' 3000000 >"$work/t"
	test "$(status_of "$work/t")" = 413
	test "$(header_of "$work/t" strict-transport-security)" = "max-age=31536000"
	head -c 3000000 /dev/zero | tr '\0' x >"$work/big"
	code=$(curl --silent --insecure --dump-header "$work/big.h" --output /dev/null --write-out '%{http_code}' \
		--header 'Content-Type: application/json' --data-binary @"$work/big" https://localhost/api/v1/auth/login)
	test "$code" = 413
	grep -qi '^strict-transport-security: max-age=0' "$work/big.h"
	# A forged route header changes nothing: on :8080 "private" is overwritten to public, so a
	# session-only operation is closed; on :443 "public" is overwritten to private, so it is
	# only unauthenticated.
	inside GET caddy:8080 /api/v1/server/audit-events '{"X-Forwarded-Proto": "https", "X-Brievie-Route": "private"}' 0 >"$work/t"
	if [ "${PUBLIC_WEB:-false}" = true ]; then
		# The web is open on the public address: the operation's own auth answers.
		test "$(status_of "$work/t")" = 401
		test "$(body_of "$work/t" | jq -r .code)" = not_authenticated
	else
		test "$(status_of "$work/t")" = 403
		test "$(body_of "$work/t" | jq -r .code)" = public_web_off
		# A junk Bearer header opens nothing either.
		inside POST caddy:8080 /api/v1/auth/login '{"X-Forwarded-Proto": "https", "X-Brievie-Route": "private", "Authorization": "Bearer junk", "Content-Type": "application/json"}' 0 >"$work/t"
		test "$(status_of "$work/t")" = 403
	fi
	body=$(curl --silent --insecure --header 'X-Brievie-Route: public' --header 'CF-Connecting-IP: 203.0.113.9' \
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
	# The app's own paths stay open on the tunnel.
	inside GET caddy:8080 /api/v1/server/ca-certificate '{"X-Forwarded-Proto": "https"}' 0 >"$work/t"
	test "$(status_of "$work/t")" = 200
	# The VPN name is served by the private CA, and a call without SNI gets a certificate.
	curl --fail --silent --show-error --insecure --resolve "$VPN_HOST:443:127.0.0.1" \
		"https://$VPN_HOST/api/v1/health" >/dev/null
	curl --fail --silent --show-error --insecure --header 'Host: localhost' \
		https://127.0.0.1/api/v1/health >/dev/null
	echo "Listeners: HSTS per route, the tunnel's redirect, 413s, forged routes, VPN name and no SNI."
}

check_session() {
	jar=${1:?usage: routes-check.sh session JAR}
	session=$(awk '$6 == "sessionid" { print $7 }' "$jar" | tail -n 1)
	test -n "$session" || { echo "No session in the cookie jar." >&2; exit 1; }
	echo "::add-mask::$session"
	# :443 with every forged address header: Brievie sees the peer, private, https.
	curl --silent --show-error --insecure --cookie "sessionid=$session" \
		--header 'X-Forwarded-For: 198.51.100.1' --header 'CF-Connecting-IP: 198.51.100.2' \
		--header 'X-Brievie-Client-IP: 198.51.100.3' --header 'X-Brievie-Route: public' \
		--header 'X-Brievie-Peer-IP: 198.51.100.4' \
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
# Seen by the listeners check before: the public name from an untrusted source, Cloudflare
# on the https port.
assert "public_name_from_untrusted_source" in codes, codes
assert "cloudflare_on_https_port" in codes, codes
print("The page shows what the server saw:", codes)
PY
	cookie_header=$(jq -cn --arg s "$session" '{"X-Forwarded-Proto": "https", "Cookie": ("sessionid=" + $s), "CF-Connecting-IP": "203.0.113.77", "X-Brievie-Client-IP": "198.51.100.3", "X-Brievie-Route": "private", "X-Forwarded-For": "198.51.100.1"}')
	inside GET caddy:8080 /api/v1/server/external-access "$cookie_header" 0 >"$work/t"
	if [ "${PUBLIC_WEB:-false}" = true ]; then
		test "$(status_of "$work/t")" = 200
		body_of "$work/t" | python3 -c '
import json, sys
found = json.load(sys.stdin)
seen = found["seen_as"]
assert seen["route"] == "public", seen
assert seen["secure"] is True, seen
assert seen["client_ip"] == "203.0.113.77", seen
codes = [c["code"] for c in found["checks"]]
assert "public_host_not_forwarded" not in codes, codes
assert "request_not_secure" not in codes, codes
print("Tunnel route: public, https, the visitor counted by CF-Connecting-IP only.")
'
	else
		test "$(status_of "$work/t")" = 403
		test "$(body_of "$work/t" | jq -r .code)" = public_web_off
		echo "Tunnel route: a web session is closed while BRIEVIE_PUBLIC_WEB is off."
	fi
}

check_token() {
	value_file=${1:?usage: routes-check.sh token FILE}
	value=$(cat "$value_file")
	echo "::add-mask::$value"
	printf '%s\n' "$value" >"$work/values"
	$COMPOSE --profile tunnel config >"$work/config.txt" 2>&1 || true
	: >"$work/inspect.txt"
	for id in $($COMPOSE --profile tunnel ps -aq); do
		name=$(docker inspect "$id" --format '{{index .Config.Labels "com.docker.compose.service"}}')
		docker inspect "$id" >"$work/inspect-$name.txt"
	done
	for service in $($COMPOSE --profile tunnel config --services); do
		$COMPOSE --profile tunnel logs --no-color "$service" >"$work/logs-$service.txt" 2>&1 || true
	done
	: >"$work/processes.txt"
	for id in $($COMPOSE --profile tunnel ps -q); do
		docker top "$id" -eo args >>"$work/processes.txt" 2>/dev/null || true
	done
	for file in "$work"/config.txt "$work"/inspect-*.txt "$work"/logs-*.txt "$work"/processes.txt; do
		case $(basename "$file") in
		# cloudflared's own environment holds the plain variable when that form is used.
		inspect-cloudflared.txt) [ "${TOKEN_AS_FILE:-}" = yes ] || continue ;;
		config.txt) [ "${TOKEN_AS_FILE:-}" = yes ] || continue ;;
		esac
		if grep -qF -f "$work/values" "$file"; then
			echo "The tunnel token is in $(basename "$file")." >&2
			exit 1
		fi
	done
	echo "The tunnel token is in no container but cloudflared, no log and no process list."
}

case ${1:-} in
caddy) check_caddy ;;
listeners) check_listeners ;;
session) check_session "${2:-}" ;;
token) check_token "${2:-}" ;;
*)
	echo "usage: routes-check.sh caddy|listeners|session JAR|token FILE" >&2
	exit 2
	;;
esac
