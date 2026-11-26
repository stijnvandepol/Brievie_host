"""Text rules for deploy/compose/docker-compose.yml, the file a user pastes.

No YAML parser (none is a dependency); the rules read the file as text, service by
service. `docker compose config` of the same file is compared with the wrapper's in
tests/test_compose_parity.py (CI) and checked for secrets by
.github/scripts/compose-secrets-check.sh.
"""

import re
from pathlib import Path

import pytest

from apps.updates.gate import COMPOSE_REVISION

ROOT = Path(__file__).resolve().parents[2]
TEXT = (ROOT / "deploy" / "compose" / "docker-compose.yml").read_text()
SERVICES = (
    "init-secrets",
    "postgres",
    "init",
    "api",
    "worker",
    "intake",
    "keeper",
    "caddy",
    "cloudflared",
    "admin",
)


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests read files only."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def service(name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|^\S|\Z)", TEXT, re.M | re.S)
    assert match, name
    return match.group(0)


def code_lines() -> list[str]:
    return [line for line in TEXT.splitlines() if not line.strip().startswith("#")]


def test_all_services_are_there_and_nothing_else() -> None:
    names = re.findall(
        r"^  ([\w-]+):\n", TEXT.split("\nservices:\n", 1)[1].split("\nnetworks:", 1)[0], re.M
    )
    assert names == list(SERVICES)


def test_no_line_holds_a_secret() -> None:
    for raw in code_lines():
        # An image pinned by digest is a checksum, not a secret.
        line = re.sub(r"@sha256:[0-9a-f]{64}\b", "@sha256:<digest>", raw)
        assert not re.search(r"[0-9a-f]{40,}", line), line
        assert not re.search(r"[A-Za-z0-9+/]{60,}={0,2}(?![A-Za-z0-9+/])", line), line
        assert not re.search(r"postgres://[^@\s/]+:[^@\s]+@", line), line


def test_a_secret_variable_is_a_file_or_empty_by_default() -> None:
    for line in code_lines():
        match = re.match(
            r"\s+([A-Z][A-Z0-9_]*(?:PASSWORD|SECRET_ACCESS_KEY|SECRET_KEY|MASTER_KEY|TOKEN)):\s*(.*)$",
            line,
        )
        if match is None:
            continue
        name, value = match.groups()
        # cloudflared reads its token as TUNNEL_TOKEN; Brievie's name for it is
        # BRIEVIE_TUNNEL_TOKEN (the one place a secret is renamed on its way in).
        if name == "TUNNEL_TOKEN":
            assert value == "${BRIEVIE_TUNNEL_TOKEN:-}", line
            continue
        assert value == f"${{{name}:-}}", line


def test_no_container_name_and_no_moving_tag() -> None:
    assert "container_name" not in TEXT
    assert not re.search(r"image:\s*\S+:(latest|edge)\b", TEXT)
    assert not re.search(r"image:\s*[^\s$:]+\s*$", TEXT, re.M)


def test_both_brievie_images_share_one_version_default() -> None:
    lines = re.findall(r"^\s*image:\s*(ghcr\.io/\S+)", TEXT, re.M)
    assert len(lines) == 2
    defaults = {re.search(r"\$\{BRIEVIE_VERSION:-([^}]+)\}", line).group(1) for line in lines}  # type: ignore[union-attr]
    assert len(defaults) == 1
    assert any("brievie-backend:" in line for line in lines)
    assert any("brievie-proxy:" in line for line in lines)


def test_every_service_but_caddy_uses_the_backend_image_by_anchor() -> None:
    # postgres, caddy and cloudflared name their own
    assert TEXT.count("<<: *backend") == len(SERVICES) - 3
    for name in ("postgres", "caddy", "cloudflared"):
        assert "<<: *backend" not in service(name)


@pytest.mark.parametrize(
    "name",
    [
        "init-secrets",
        "postgres",
        "init",
        "api",
        "worker",
        "intake",
        "keeper",
        "cloudflared",
        "admin",
    ],
)
def test_only_caddy_publishes_a_port(name: str) -> None:
    assert "ports:" not in service(name)
    assert "ports:" in service("caddy")


def test_postgres_is_only_on_the_internal_network() -> None:
    assert "networks: [db, scan]" in service("postgres")
    assert re.search(r"^networks:\n  # .*\n  db:\n    internal: true\n", TEXT, re.M)


def test_intake_reaches_postgres_only_and_has_its_own_secrets() -> None:
    # Security review of PR #64 (M5): not on `db` (which carries the api), no back-up password.
    block = service("intake")
    assert "networks: [scan]" in block
    assert re.search(r"^  scan:\n    internal: true\n", TEXT, re.M)
    lists = {name: re.findall(r"networks: \[(.*)\]", service(name)) for name in SERVICES}
    on_scan = [name for name, found in lists.items() if any("scan" in f.split(", ") for f in found)]
    assert on_scan == ["postgres", "intake"]
    assert "secrets-api" not in block
    assert "secrets-intake:/out/intake" in service("init-secrets")


def test_the_keeper_has_its_own_secrets_and_no_master_key() -> None:
    keeper = service("keeper")
    assert "secrets-keeper:/run/brievie:ro" in keeper
    assert "secrets-api" not in keeper
    assert not re.search(r"\bsecrets:/", keeper)
    assert "MASTER_KEY" not in keeper


@pytest.mark.parametrize("name", ["api", "worker", "intake"])
def test_the_api_and_worker_see_neither_the_vault_nor_the_keeper_files(name: str) -> None:
    block = service(name)
    assert ("secrets-intake" if name == "intake" else "secrets-api") + ":/run/brievie:ro" in block
    assert "secrets-keeper" not in block
    assert not re.search(r"\bsecrets:/", block)
    for name_in_vault in ("OWNER_PASSWORD", "SUPERUSER", "owner_password", "postgres_password"):
        assert name_in_vault not in block


@pytest.mark.parametrize("name", ["api", "worker", "intake", "keeper"])
def test_the_long_running_services_drop_every_capability(name: str) -> None:
    block = service(name)
    assert "cap_drop: [ALL]" in block
    assert 'security_opt: ["no-new-privileges:true"]' in block


def test_init_secrets_has_no_network() -> None:
    block = service("init-secrets")
    assert "network_mode: none" in block
    assert "networks:" not in block
    assert 'command: ["python", "-m", "apps.installer.init_secrets"]' in block


def test_only_admin_reads_the_whole_vault() -> None:
    holders = [name for name in SERVICES if re.search(r"\bsecrets:/run/brievie", service(name))]
    assert holders == ["admin"]


def test_init_has_its_own_volume_without_the_master_key_and_drops_every_capability() -> None:
    block = service("init")
    assert "secrets-init:/run/brievie:ro" in block
    assert "MASTER_KEY" not in block
    assert not re.search(r"\bsecrets:/", block)
    assert "cap_drop: [ALL]" in block
    assert 'security_opt: ["no-new-privileges:true"]' in block


def test_the_revision_of_the_file_is_the_one_of_the_image() -> None:
    assert f'BRIEVIE_COMPOSE_REVISION: "{COMPOSE_REVISION}"' in TEXT


def test_the_three_places_to_change_are_marked() -> None:
    marked = [line for line in TEXT.splitlines() if "<--" in line]
    assert len(marked) >= 4
    assert any(
        "BRIEVIE_VERSION" in TEXT.splitlines()[i + 1]
        for i, line in enumerate(TEXT.splitlines())
        if "The version" in line
    )


def test_logs_rotate() -> None:
    assert TEXT.count('max-size: "10m"') == 3  # the anchor, caddy and cloudflared
    assert "restart: always" not in TEXT


def test_the_database_hostname_placeholder_is_the_only_hostname_default() -> None:
    assert set(re.findall(r"BRIEVIE_HOSTNAME:-([^}]+)\}", TEXT)) == {"verander-mij.local"}


def test_the_hostname_is_written_in_one_place() -> None:
    """Changing the one line marked <-- must also reach caddy."""
    assert TEXT.count("verander-mij.local}") == 1
    assert "BRIEVIE_HOSTNAME: &hostname ${BRIEVIE_HOSTNAME:-verander-mij.local}" in TEXT
    assert TEXT.count("BRIEVIE_HOSTNAME: *hostname") == 1
