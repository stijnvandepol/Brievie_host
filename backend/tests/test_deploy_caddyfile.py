"""The Caddyfile enforces the body limit and answers proxy errors in the API shape."""

import json
import re
from pathlib import Path

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
    assert matcher == ["method", "POST", "path_regexp", "^/api/v1/workspaces/[^/]+/documents$"]
    upload = handle_block("handle @documentUpload {")
    general = handle_block("handle {")
    assert re.search(r"request_body\s*\{\s*max_size \{\$BRIEVIE_MAX_UPLOAD_BYTES\}", upload)
    assert "BRIEVIE_MAX_BODY_BYTES" not in upload
    assert re.search(r"request_body\s*\{\s*max_size \{\$BRIEVIE_MAX_BODY_BYTES\}", general)
    assert "BRIEVIE_MAX_UPLOAD_BYTES" not in general
    assert len(re.findall(r"request_body\s*\{", CADDYFILE)) == 2
    assert "reverse_proxy api:8000" in upload
    assert "reverse_proxy api:8000" in general


def test_compose_passes_the_upload_limit_to_caddy_and_api() -> None:
    assert COMPOSE.count("BRIEVIE_MAX_UPLOAD_BYTES: ${BRIEVIE_MAX_UPLOAD_BYTES:-33554432}") == 2


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


def test_caddy_replaces_a_client_sent_forwarded_for() -> None:
    # The client address (sessions, audit log, login lockout) is the last
    # X-Forwarded-For entry (apps.common.net, BRIEVIE_PROXY_COUNT 1). Caddy trusts no
    # proxy here, so it replaces whatever X-Forwarded-For a client sends with the peer
    # it sees: a client cannot pick its own address. Trusting a proxy, or passing the
    # header on by hand, would let any client do so.
    assert "trusted_proxies" not in CADDYFILE
    assert "client_ip_headers" not in CADDYFILE
    assert not re.search(r"header_up\s+[+-]?X-Forwarded-For", CADDYFILE, re.IGNORECASE)
    assert 'BRIEVIE_PROXY_COUNT: "1"' in service_block("api")


def test_only_caddy_publishes_the_api() -> None:
    # Reached directly, the api would believe any X-Forwarded-For.
    for service in ("api", "worker", "keeper", "admin", "migrate"):
        assert "ports:" not in service_block(service), service
    assert "ports:" in service_block("caddy")
