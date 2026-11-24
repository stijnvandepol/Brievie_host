"""The optional Cloudflare Tunnel and the external-access variables in both compose files.

Text rules (no YAML parser is a dependency); `tests/test_compose_parity.py` compares the
two files' `docker compose config` in CI, the tunnel profile included.
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
def test_cloudflared_is_optional_behind_the_tunnel_profile(name: str) -> None:
    block = service(FILES[name], "cloudflared")
    assert 'profiles: ["tunnel"]' in block
    assert re.search(r"depends_on:\n\s+caddy:\n\s+condition: service_started", block)
    assert "restart: unless-stopped" in block


@both
def test_cloudflared_is_pinned_by_version_and_digest(name: str) -> None:
    images = re.findall(r"image:\s*(cloudflare/cloudflared\S*)", FILES[name])
    assert len(images) == 1
    assert re.fullmatch(r"cloudflare/cloudflared:\d{4}\.\d+\.\d+@sha256:[0-9a-f]{64}", images[0])
    assert "latest" not in images[0]


def test_both_files_pin_the_same_cloudflared() -> None:
    found = {re.search(r"cloudflare/cloudflared\S+", text).group(0) for text in FILES.values()}  # type: ignore[union-attr]
    assert len(found) == 1


@both
def test_the_tunnel_token_reaches_only_cloudflared(name: str) -> None:
    text = code(FILES[name])
    holders = [
        part.split(":\n", 1)[0].strip()
        for part in re.split(r"\n(?=  [\w-]+:\n)", text.split("\nservices:\n", 1)[1])
        if re.search(r"\$\{BRIEVIE_TUNNEL_TOKEN(_FILE)?:-", part)
    ]
    assert holders == ["cloudflared"]
    block = service(FILES[name], "cloudflared")
    assert "TUNNEL_TOKEN: ${BRIEVIE_TUNNEL_TOKEN:-}" in block
    assert "TUNNEL_TOKEN_FILE: ${BRIEVIE_TUNNEL_TOKEN_FILE:+/run/brievie-tunnel/token}" in block
    assert "${BRIEVIE_TUNNEL_TOKEN_FILE:-/dev/null}:/run/brievie-tunnel/token:ro" in block
    # The api learns only whether one is set (two variables: `truetrue` is no boolean).
    api = service(FILES[name], "api")
    assert "BRIEVIE_TUNNEL_TOKEN_SET: ${BRIEVIE_TUNNEL_TOKEN:+true}" in api
    assert "BRIEVIE_TUNNEL_TOKEN_FILE_SET: ${BRIEVIE_TUNNEL_TOKEN_FILE:+true}" in api
    assert "TUNNEL_TOKEN" not in CADDYFILE


@both
def test_the_token_is_never_required_by_interpolation(name: str) -> None:
    # Compose interpolates the services of inactive profiles too: a `:?` would stop
    # every installation without a tunnel.
    assert "BRIEVIE_TUNNEL_TOKEN:?" not in FILES[name]
    assert "BRIEVIE_TUNNEL_TOKEN_FILE:?" not in FILES[name]


@both
def test_cloudflared_is_locked_down(name: str) -> None:
    block = service(FILES[name], "cloudflared")
    assert "read_only: true" in block
    assert "cap_drop: [ALL]" in block
    assert 'security_opt: ["no-new-privileges:true"]' in block
    assert "ports:" not in block
    assert "user:" not in block  # the image's own nonroot user
    assert 'command: ["tunnel", "--no-autoupdate", "--metrics", "0.0.0.0:2000", "run"]' in block
    # The token is never a process argument.
    assert "--token" not in block


@both
def test_caddy_gets_the_route_variables(name: str) -> None:
    caddy = service(FILES[name], "caddy")
    assert "BRIEVIE_VPN_HOSTNAME: ${BRIEVIE_VPN_HOSTNAME:-}" in caddy
    assert "BRIEVIE_PUBLIC_HOSTNAME: ${BRIEVIE_PUBLIC_HOSTNAME:-}" in caddy
    # Never empty: Caddy's own default applies only to a variable that is not set at all.
    assert (
        "BRIEVIE_TUNNEL_HOST: ${BRIEVIE_PUBLIC_HOSTNAME:-public-hostname-not-set.invalid}" in caddy
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
        "BRIEVIE_TUNNEL_TOKEN",
        "BRIEVIE_TUNNEL_TOKEN_FILE",
        "BRIEVIE_TRUSTED_PROXIES",
        "BRIEVIE_PUBLIC_WEB",
        "COMPOSE_PROFILES",
    ):
        assert re.search(rf"^# {variable}=", ENV_EXAMPLE, re.M), variable
    # Nothing of it is switched on by the template.
    switched_on = r"^(BRIEVIE_TUNNEL_TOKEN|BRIEVIE_PUBLIC_WEB|COMPOSE_PROFILES)="
    assert not re.search(switched_on, ENV_EXAMPLE, re.M)
    # Said plainly, next to the token.
    before_token = ENV_EXAMPLE.split("# BRIEVIE_TUNNEL_TOKEN=", 1)[0].rsplit("\n\n", 1)[1]
    assert "Cloudflare" in before_token
    assert "read" in before_token


def test_the_new_image_has_a_decision() -> None:
    assert "cloudflare/cloudflared" in DECISIONS


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
    privacy = " ".join((ROOT / "docs" / "PRIVACY.md").read_text().split())
    selfhost = " ".join((ROOT / "docs" / "SELFHOST.md").read_text().split())
    for text in (privacy, selfhost):
        assert "Cloudflare ends the encryption on its own servers" in text
    assert "nobody in between can read it" in privacy
    assert "https://brievie.com/docs/installeren#van-buiten" in ENV_EXAMPLE


CI_TEXT = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
ROUTES = (ROOT / ".github" / "scripts" / "routes-check.sh").read_text()


def ci_job(name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|^  # |\Z)", CI_TEXT, re.M | re.S
    )
    assert match, name
    return match.group(0)


def test_ci_checks_the_tunnel_listener() -> None:
    for name in ("stack", "compose-stack"):
        block = ci_job(name)
        for mode in ("caddy", "listeners", "session", "isolation"):
            assert f"routes-check.sh {mode}" in block, (name, mode)
        assert "routes-check.sh token" in block, name
        assert "--profile tunnel down --volumes" in block, name
    assert "TOKEN_AS_FILE=yes" in ci_job("compose-stack")
    assert 'BRIEVIE_PUBLIC_WEB: "true"' in ci_job("compose-stack")
    stack = ci_job("stack")
    assert "--profile tunnel config --quiet" in stack
    assert "no-token.env" in stack
    # No literal token in the workflow: made at run time and masked.
    assert "openssl rand -base64 48" in stack
    assert "::add-mask::$token" in stack


def test_ci_scans_the_pinned_cloudflared_and_validates_the_caddyfile_twice() -> None:
    image = ci_job("image")
    pinned = re.search(r"cloudflare/cloudflared\S+", FILES["wrapper"]).group(0)  # type: ignore[union-attr]
    assert f"image-ref: {pinned}" in image
    assert image.count("caddy validate --config /etc/caddy/Caddyfile") == 3


def test_the_routes_script_checks_what_the_review_asks() -> None:
    for needle in (
        'headers == ["CF-Connecting-IP"]',
        '"X-Brievie-Route", "X-Brievie-Client-IP", "X-Brievie-Peer-IP", "Host"',
        "max-age=31536000",
        "max-age=0",
        "= 308",
        "= 413",
        "public_web_off",
        "Bearer junk",
        "--resolve",
        'seen["client_ip"] == seen["peer_ip"]',
        '"203.0.113.77"',
    ):
        assert needle in ROUTES, needle


@both
def test_cloudflared_shares_a_network_with_caddy_only(name: str) -> None:
    # Security review of PR #53, point 2: a tunnel ingress set to http://api:8000 must not
    # reach the api (nor the worker or PostgreSQL).
    text = FILES[name]
    assert "networks: [tunnel]" in service(text, "cloudflared")
    assert re.search(r"networks: \[(default|edge), tunnel\]", service(text, "caddy"))
    holders = [
        part.split(":\n", 1)[0].strip()
        for part in re.split(
            r"\n(?=  [\w-]+:\n)", text.split("\nservices:\n", 1)[1].split("\nnetworks:\n")[0]
        )
        if re.search(r"networks: \[[^\]]*\btunnel\b", part)
    ]
    assert sorted(holders) == ["caddy", "cloudflared"]
    assert re.search(r"^networks:\n(?:  .*\n)*  tunnel: \{\}", text, re.M)
