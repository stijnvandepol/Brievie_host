"""The Caddyfile enforces the body limit and answers proxy errors in the API shape."""

import json
import re
from pathlib import Path

import pytest

CADDYFILE = (Path(__file__).resolve().parents[2] / "deploy" / "Caddyfile").read_text()


def error_bodies() -> dict[str, dict[str, str]]:
    bodies = {}
    for body in re.findall(r"respond `(\{.*?\})`", CADDYFILE):
        parsed = json.loads(body)
        bodies[parsed["code"]] = parsed
    return bodies


COMPOSE = (Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml").read_text()


def handle_block(opening: str) -> str:
    """The body of the `handle` block that starts with `opening` (brace matched)."""
    start = CADDYFILE.index(opening) + len(opening)
    depth = 1
    position = start
    while depth:
        depth += {"{": 1, "}": -1}.get(CADDYFILE[position], 0)
        position += 1
    return CADDYFILE[start : position - 1]


def test_document_upload_has_its_own_body_limit() -> None:
    # Two request_body directives on one request both apply and the smaller wins,
    # so each limit lives in its own handle block.
    matcher = handle_block("@documentUpload {").split()
    assert matcher == [
        "method",
        "POST",
        "path_regexp",
        "^/api/v1/workspaces/[^/]+/documents(/files)?$",
    ]
    upload = handle_block("handle @documentUpload {")
    general = handle_block("handle {")
    assert re.search(r"request_body\s*\{\s*max_size \{\$BRIEVIE_MAX_UPLOAD_BYTES\}", upload)
    assert "BRIEVIE_MAX_BODY_BYTES" not in upload
    assert re.search(r"request_body\s*\{\s*max_size \{\$BRIEVIE_MAX_BODY_BYTES\}", general)
    assert "BRIEVIE_MAX_UPLOAD_BYTES" not in general
    assert len(re.findall(r"request_body\s*\{", CADDYFILE)) == 2
    assert "import to_api {args[0]}" in upload
    assert "import to_api {args[0]}" in general


def test_compose_passes_the_upload_limit_to_caddy_and_api() -> None:
    # Caddy, the api, and the scan folder's intake service (which checks files too).
    assert COMPOSE.count("BRIEVIE_MAX_UPLOAD_BYTES: ${BRIEVIE_MAX_UPLOAD_BYTES:-33554432}") == 3


def test_proxy_errors_use_the_error_shape() -> None:
    bodies = error_bodies()
    assert set(bodies) == {"payload_too_large", "server_unavailable"}
    for body in bodies.values():
        assert set(body) == {"code", "message"}
    assert bodies["payload_too_large"]["message"] == "Dit verzoek is te groot."
    assert re.search(r"handle_errors 413\b", CADDYFILE)
    assert re.search(r"handle_errors 502 503 504\b", CADDYFILE)


def test_content_security_policy_allows_no_inline_script_or_eval() -> None:
    match = re.search(r'Content-Security-Policy "([^"]+)"', CADDYFILE)
    assert match
    policy = dict(
        (directive.split(" ", 1)[0], directive.split(" ", 1)[1:] or [""])
        for directive in match.group(1).split("; ")
    )
    assert policy["default-src"] == ["'self'"]
    assert policy["script-src"] == ["'self'"]
    assert policy["frame-ancestors"] == ["'none'"]
    assert "'unsafe-inline'" not in match.group(1)
    assert "'unsafe-eval'" not in match.group(1)


def service_block(name: str) -> str:
    """The compose service `name` up to the next top-level service."""
    start = COMPOSE.index(f"\n  {name}:\n")
    following = re.search(r"\n  [a-z][a-z0-9_-]*:\n", COMPOSE[start + 1 :])
    return COMPOSE[start : start + 1 + following.start()] if following else COMPOSE[start:]


def block(opening: str) -> str:
    """The body of the block that starts with the line `opening` (brace matched)."""
    start = CADDYFILE.index(opening) + len(opening)
    depth = 1
    position = start
    while depth:
        depth += {"{": 1, "}": -1}.get(CADDYFILE[position], 0)
        position += 1
    return CADDYFILE[start : position - 1]


def code(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


# A compose-only install keeps its pasted compose file while BRIEVIE_VERSION moves on: the
# 0.5.0 file passes BRIEVIE_TUNNEL_HOST, this release's passes BRIEVIE_PUBLIC_HOST_MATCH.
# The Caddyfile must honour both, or the public name stops being public by name (H1 of the
# review of PR #65).
OLD_AND_NEW_MATCH = (
    "@public_name host {$BRIEVIE_PUBLIC_HOST_MATCH:public-hostname-not-set.invalid} "
    "{$BRIEVIE_TUNNEL_HOST:old-hostname-not-set.invalid}"
)


def site_home() -> str:
    return block("\n{$BRIEVIE_HOSTNAME} {$BRIEVIE_VPN_HOSTNAME} {$BRIEVIE_PUBLIC_HOSTNAME} {")


def test_every_proxy_to_the_api_sets_the_three_edge_headers() -> None:
    # One snippet holds the only reverse_proxy; header_up replaces a client's value.
    assert code(CADDYFILE).count("reverse_proxy") == 1
    to_api = block("(to_api) {")
    assert "reverse_proxy api:8000 {" in to_api
    for line in (
        "header_up X-Brievie-Route {vars.brievie_route}",
        "header_up X-Brievie-Client-IP {client_ip}",
        "header_up X-Brievie-Peer-IP {remote_host}",
    ):
        assert line in to_api, line
    # Both limits go through it, and the one site imports the routes.
    assert code(CADDYFILE).count("import to_api {args[0]}") == 2
    assert "import brievie_routes" in site_home()


def test_there_is_no_tunnel_listener_and_no_cf_connecting_ip() -> None:
    # Owner decision of 26-11: remote access is the user's own tool, forwarding to :443.
    text = code(CADDYFILE)
    assert "8080" not in text
    assert "CF-Connecting-IP" not in text
    assert "client_ip_headers" not in text
    assert "tunnel" not in text.replace("BRIEVIE_TUNNEL_HOST", "").lower()
    # Only the old name of the public-name match stays, for an older compose file.
    assert text.count("BRIEVIE_TUNNEL") == 1
    assert "BRIEVIE_TUNNEL_TOKEN" not in CADDYFILE
    # One site only: every name Caddy answers is served by the block that decides the route.
    sites = [line for line in re.findall(r"^[^\s(#].*\{$", text, re.MULTILINE)]
    assert len(sites) == 1, sites


def test_trusted_proxies_only_from_the_variable_on_443() -> None:
    text = code(CADDYFILE)
    assert text.count("trusted_proxies static") == 1
    on_443 = block("servers :443 {")
    assert "trusted_proxies static {$BRIEVIE_TRUSTED_PROXIES:127.0.0.1/32}" in on_443
    assert "trusted_proxies_strict" in on_443
    # Nobody passes X-Forwarded-For on by hand: Caddy writes it from its trust rules.
    assert not re.search(r"header_up\s+[+-]?X-Forwarded-For", CADDYFILE, re.IGNORECASE)


def test_a_users_own_cloudflared_on_443_lands_on_the_public_route() -> None:
    """What the user's cloudflared sends: the https port, Host = public name and an
    X-Forwarded-For from an address that is not in BRIEVIE_TRUSTED_PROXIES.

    Text rules, since Caddy is not run here (CI's routes-check runs the real thing): the
    site answers the public name, `@public_name` and `@proxied_xff` both turn the route to
    public, and both come after the private default, so they win.
    """
    home = block("\n{$BRIEVIE_HOSTNAME} {$BRIEVIE_VPN_HOSTNAME} {$BRIEVIE_PUBLIC_HOSTNAME} {")
    assert (
        "{$BRIEVIE_PUBLIC_HOSTNAME}"
        in CADDYFILE.split("\n{$BRIEVIE_HOSTNAME}", 1)[1].split("\n", 1)[0]
    )
    assert OLD_AND_NEW_MATCH in home
    for name in ("public_name", "proxied_xff"):
        variables = block(f"vars @{name} {{")
        assert "brievie_route public" in variables, name
        assert home.index("vars @direct {") < home.index(f"vars @{name} {{"), name
    # The api gets the Host as sent (with its port): the origin check needs it.
    assert "import brievie_routes {http.request.hostport}" in home
    # A name that is not set resolves nowhere: unknown Hosts fail closed, never private.
    assert "public-hostname-not-set.invalid" in home


def test_the_route_on_443_is_decided_by_name_first_and_fails_closed() -> None:
    # Security review of PR #53, point 1: the public name is public whatever its source,
    # and Cloudflare's headers on :443 make a request public too.
    home = site_home()
    assert "@direct not remote_ip {$BRIEVIE_TRUSTED_PROXIES:127.0.0.1/32}" in home
    assert OLD_AND_NEW_MATCH in home
    assert "@via_proxy remote_ip {$BRIEVIE_TRUSTED_PROXIES:127.0.0.1/32}" in home
    assert "@cf_ray header Cf-Ray *" in home
    assert "@cf_client header Cf-Connecting-Ip *" in home
    direct = block("vars @direct {")
    assert "brievie_route private" in direct
    assert "brievie_hsts {$BRIEVIE_HSTS_SECONDS:0}" in direct
    for name in ("public_name", "via_proxy", "cf_ray", "cf_client"):
        assert "brievie_route public" in block(f"vars @{name} {{"), name
    # The public name from an untrusted source keeps the home HSTS (it is Caddy's own
    # certificate there); a proxied or Cloudflare request gets a year.
    assert "brievie_hsts" not in block("vars @public_name {")
    for name in ("via_proxy", "cf_ray", "cf_client"):
        assert "brievie_hsts 31536000" in block(f"vars @{name} {{"), name
    # Every vars has a matcher, and private comes first: a later public match wins.
    assert not re.search(r"^\tvars \{", home, re.MULTILINE)
    order = [
        home.index(f"vars @{name} {{")
        for name in ("direct", "public_name", "via_proxy", "cf_ray", "cf_client")
    ]
    assert order == sorted(order)
    assert "header_up X-Brievie-Via {vars.brievie_via}" in block("(to_api) {")


def test_hsts_comes_from_the_route_var() -> None:
    headers = block("(security_headers) {")
    assert 'Strict-Transport-Security "max-age={vars.brievie_hsts}"' in headers
    assert "BRIEVIE_HSTS_SECONDS" not in code(headers)
    assert "brievie_hsts 31536000" in block("vars @via_proxy {")


def test_the_home_route_keeps_the_host_with_its_port() -> None:
    assert "header_up Host {args[0]}" in block("(to_api) {")
    assert "import brievie_routes {http.request.hostport}" in site_home()
    assert code(CADDYFILE).count("http.request.hostport") == 1


def test_default_sni_is_the_home_name() -> None:
    assert re.search(r"^\tdefault_sni \{\$BRIEVIE_HOSTNAME\}$", CADDYFILE, re.MULTILINE)


def test_only_80_and_443_are_published() -> None:
    # Every published port targets 80 or 443 only; no listener exists for anything else.
    root = Path(__file__).resolve().parents[2]
    for path in (
        root / "deploy" / "docker-compose.yml",
        root / "deploy" / "compose" / "docker-compose.yml",
    ):
        lines = path.read_text().splitlines()
        targets = []
        for index, line in enumerate(lines):
            if line.strip() != "ports:":
                continue
            for entry in lines[index + 1 :]:
                if entry.strip().startswith("#"):
                    continue
                if not entry.strip().startswith("- "):
                    break
                targets.append(entry.strip().strip('-" ').rsplit(":", 1)[1])
        assert targets == ["80", "443"], (path, targets)
        assert "expose:" not in path.read_text()


def test_only_caddy_publishes_the_api() -> None:
    # Reached directly, the api would believe any X-Forwarded-For.
    for service in ("api", "worker", "keeper", "admin", "migrate"):
        assert "ports:" not in service_block(service), service
    assert "ports:" in service_block("caddy")


@pytest.mark.parametrize(
    "header",
    ["X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Real-IP", "Forwarded", "Via"],
)
def test_a_proxys_header_from_an_untrusted_source_is_public(header: str) -> None:
    # Re-review of PR #53, point 1: a proxy that is not trusted yet and rewrites Host to the
    # home name would otherwise get the private route.
    home = site_home()
    match = re.search(
        r"@proxied_(\w+) \{\n\t\tnot remote_ip "
        r"\{\$BRIEVIE_TRUSTED_PROXIES:127\.0\.0\.1/32\}\n\t\theader "
        + re.escape(header)
        + r" \*\n\t\}",
        home,
    )
    assert match, header
    variables = block(f"vars @proxied_{match.group(1)} {{")
    assert "brievie_route public" in variables
    assert "brievie_via unknown_proxy" in variables
    # After the private default, so it wins over it.
    assert home.index("vars @direct {") < home.index(f"vars @proxied_{match.group(1)} {{")
