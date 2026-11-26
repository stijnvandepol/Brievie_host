"""Remote access is the user's own tool: no tunnel in either compose file, and the hooks stay.

Text rules (no YAML parser is a dependency); `tests/test_compose_parity.py` compares the
two files' `docker compose config` in CI.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FILES = {
    "wrapper": (ROOT / "deploy" / "docker-compose.yml").read_text(),
    "standalone": (ROOT / "deploy" / "compose" / "docker-compose.yml").read_text(),
}
CADDYFILE = (ROOT / "deploy" / "Caddyfile").read_text()
ENV_EXAMPLE = (ROOT / "deploy" / ".env.example").read_text()
DECISIONS = (ROOT / "docs" / "DECISIONS.md").read_text()


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests read files only."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def service(text: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|^\S|\Z)", text, re.M | re.S)
    assert match, name
    return match.group(0)


def code(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


both = pytest.mark.parametrize("name", sorted(FILES))


@both
def test_no_service_image_or_network_of_a_tunnel_is_left(name: str) -> None:
    text = FILES[name]
    for needle in ("cloudflared", "cloudflare/", "TUNNEL", "tunnel"):
        assert needle not in code(text), (name, needle)
    assert 'profiles: ["tunnel"]' not in text
    assert not re.search(r"^networks:\n(?:  .*\n)*  tunnel: ", text, re.M)
    caddy = service(text, "caddy")
    assert not re.search(r"networks: \[[^\]]*\btunnel\b", caddy)
    # The hooks stay: the names, the trusted proxies and the public web switch reach the api.
    api = service(text, "api")
    assert "BRIEVIE_PUBLIC_WEB: ${BRIEVIE_PUBLIC_WEB:-false}" in api
    assert "BRIEVIE_TRUSTED_PROXIES: ${BRIEVIE_TRUSTED_PROXIES:-}" in api


@both
def test_caddy_gets_the_route_variables(name: str) -> None:
    caddy = service(FILES[name], "caddy")
    assert "BRIEVIE_VPN_HOSTNAME: ${BRIEVIE_VPN_HOSTNAME:-}" in caddy
    assert "BRIEVIE_PUBLIC_HOSTNAME: ${BRIEVIE_PUBLIC_HOSTNAME:-}" in caddy
    # Never empty: Caddy's own default applies only to a variable that is not set at all.
    assert (
        "BRIEVIE_PUBLIC_HOST_MATCH: ${BRIEVIE_PUBLIC_HOSTNAME:-public-hostname-not-set.invalid}"
        in caddy
    )
    assert "BRIEVIE_TRUSTED_PROXIES: ${BRIEVIE_TRUSTED_PROXIES:-127.0.0.1/32}" in caddy


@both
def test_api_is_behind_caddy(name: str) -> None:
    api = service(FILES[name], "api")
    assert 'BRIEVIE_BEHIND_CADDY: "true"' in api
    assert "BRIEVIE_PUBLIC_WEB: ${BRIEVIE_PUBLIC_WEB:-false}" in api
    assert "BRIEVIE_TRUSTED_PROXIES: ${BRIEVIE_TRUSTED_PROXIES:-}" in api
    # Django's HSTS stays one value; Caddy decides per route.
    assert "BRIEVIE_HSTS_SECONDS: ${BRIEVIE_HSTS_SECONDS:-0}" in api
    for other in ("worker", "keeper", "admin"):
        assert "BRIEVIE_BEHIND_CADDY" not in service(FILES[name], other), other


def test_env_example_documents_every_new_variable() -> None:
    for variable in (
        "BRIEVIE_VPN_HOSTNAME",
        "BRIEVIE_PUBLIC_HOSTNAME",
        "BRIEVIE_TRUSTED_PROXIES",
        "BRIEVIE_PUBLIC_WEB",
    ):
        assert re.search(rf"^# {variable}=", ENV_EXAMPLE, re.M), variable
    # Nothing of it is switched on by the template.
    assert not re.search(r"^BRIEVIE_PUBLIC_WEB=", ENV_EXAMPLE, re.M)
    # No tunnel setting is offered any more, in either template.
    for path in (ROOT / "deploy" / ".env.example", ROOT / "deploy" / "compose" / ".env.example"):
        text = path.read_text()
        for gone in ("BRIEVIE_TUNNEL_TOKEN", "COMPOSE_PROFILES"):
            assert gone not in text, (path.name, gone)


def test_dropping_the_tunnel_has_a_decision() -> None:
    assert "remote access is the user's own tool" in DECISIONS.lower().replace("\u2019", "'")


def test_selfhost_documents_every_env_variable() -> None:
    selfhost = (ROOT / "docs" / "SELFHOST.md").read_text()
    section = selfhost.split("## 25. Van buiten bereikbaar", 1)[1]
    table = section.split("### Every variable of", 1)[1]
    names: set[str] = set()
    for path in (ROOT / "deploy" / ".env.example", ROOT / "deploy" / "compose" / ".env.example"):
        names |= set(re.findall(r"^#? ?([A-Z][A-Z0-9_]+)=", path.read_text(), re.M))
    missing = sorted(name for name in names if f"`{name}`" not in table)
    assert missing == []


def test_the_docs_say_plainly_what_cloudflare_can_read() -> None:
    # Only when the user sets up a Cloudflare Tunnel of their own: the docs still say so.
    privacy = " ".join((ROOT / "docs" / "PRIVACY.md").read_text().split())
    selfhost = " ".join((ROOT / "docs" / "SELFHOST.md").read_text().split())
    for text in (privacy, selfhost):
        assert "Cloudflare ends the encryption on its own servers" in text
    assert "nobody in between can read it" in privacy
    assert "https://brievie.com/nl/install/#publieke-toegang" in ENV_EXAMPLE
    assert "https://brievie.com/docs/installeren#van-buiten" not in ENV_EXAMPLE


CI_TEXT = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
ROUTES = (ROOT / ".github" / "scripts" / "routes-check.sh").read_text()


def ci_job(name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|^  # |\Z)", CI_TEXT, re.M | re.S
    )
    assert match, name
    return match.group(0)


def test_ci_checks_the_public_route_without_a_tunnel() -> None:
    for name in ("stack", "compose-stack"):
        block = ci_job(name)
        for mode in ("caddy", "listeners", "session", "isolation"):
            assert f"routes-check.sh {mode}" in block, (name, mode)
        assert "routes-check.sh token" not in block, name
        assert "--profile tunnel" not in block, name
        assert "TUNNEL" not in block, name
    assert 'BRIEVIE_PUBLIC_WEB: "true"' in ci_job("compose-stack")
    assert "BRIEVIE_PUBLIC_HOSTNAME=$PUBLIC_HOST" in ci_job("stack")


def test_ci_validates_the_caddyfile_and_scans_no_cloudflared_image() -> None:
    image = ci_job("image")
    assert "cloudflared" not in image
    assert image.count("caddy validate --config /etc/caddy/Caddyfile") == 3
    assert "BRIEVIE_TUNNEL_HOST" not in CI_TEXT
    assert not (ROOT / ".trivyignore.cloudflared.yaml").exists()


def test_the_routes_script_checks_what_the_review_asks() -> None:
    for needle in (
        'assert len(servers) == 1, "one server"',
        '"X-Brievie-Route", "X-Brievie-Client-IP", "X-Brievie-Peer-IP", "Host"',
        "max-age=0",
        "= 413",
        "public_web_off",
        "pair_at_home",
        "Bearer junk",
        "--resolve",
        'seen["client_ip"] == seen["peer_ip"]',
        "X-Forwarded-For: 198.51.100.1",
    ):
        assert needle in ROUTES, needle
    assert "caddy:8080" not in ROUTES

