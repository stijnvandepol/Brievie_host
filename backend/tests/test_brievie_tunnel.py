"""./brievie and the optional Cloudflare Tunnel, the extra names and their checks (S 10)."""

import secrets
import subprocess
from pathlib import Path

import pytest

from tests.test_brievie_wrapper import Install, current_env

# Made at run time: no token-like literal in the repository.
TOKEN = "eyJ" + secrets.token_urlsafe(48)
TUNNEL_UP = "--profile tunnel up -d --no-deps cloudflared"
FAILING = (
    "Brievie draait, maar de tunnel naar Cloudflare start niet. Controleer BRIEVIE_TUNNEL_TOKEN."
)


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests run only shell and a fake."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


@pytest.fixture
def installed(tmp_path: Path) -> Install:
    install = Install(tmp_path)
    install.write_env(current_env())
    return install


def with_settings(install: Install, *lines: str) -> None:
    install.write_env(current_env() + "".join(line + "\n" for line in lines))


def run_start(install: Install, **env: str) -> "subprocess.CompletedProcess[str]":
    return install.run("start", BRIEVIE_TUNNEL_WAIT="0", **env)


def test_start_without_token_never_mentions_the_tunnel_profile(installed: Install) -> None:
    result = installed.run("start")
    assert result.returncode == 0, result.stderr
    assert not any("tunnel" in command for command in installed.commands())
    assert "tunnel" not in result.stdout + result.stderr


def test_start_with_token_starts_cloudflared_after_the_stack(installed: Install) -> None:
    with_settings(
        installed, f"BRIEVIE_TUNNEL_TOKEN={TOKEN}", "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl"
    )
    result = installed.run(
        "start", FAKE_DOCKER_RUNNING="postgres keeper api worker caddy cloudflared"
    )
    assert result.returncode == 0, result.stderr
    main = installed.index("up -d --build --wait")
    assert "--profile" not in installed.commands()[main]
    assert main < installed.index(TUNNEL_UP)
    assert "De tunnel naar Cloudflare draait." in result.stdout


def test_a_token_file_also_switches_the_tunnel_on(installed: Install) -> None:
    with_settings(
        installed,
        "BRIEVIE_TUNNEL_TOKEN_FILE=/volume1/brievie/tunnel-token",
        "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl",
    )
    result = installed.run(
        "start", FAKE_DOCKER_RUNNING="postgres keeper api worker caddy cloudflared"
    )
    assert result.returncode == 0, result.stderr
    assert installed.index(TUNNEL_UP) > 0


def test_a_failing_tunnel_is_a_warning_not_a_failed_start(installed: Install) -> None:
    with_settings(
        installed, f"BRIEVIE_TUNNEL_TOKEN={TOKEN}", "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl"
    )
    result = run_start(installed, FAKE_DOCKER_FAIL=TUNNEL_UP)
    assert result.returncode == 0, result.stderr
    assert FAILING in result.stderr
    # A tunnel that starts but does not keep running is the same warning.
    installed.log.unlink()
    result = run_start(installed)
    assert result.returncode == 0, result.stderr
    assert FAILING in result.stderr


def test_update_and_restore_pass_the_profile_when_stopping(installed: Install) -> None:
    from tests.test_brievie_wrapper import restore_inputs

    with_settings(
        installed, f"BRIEVIE_TUNNEL_TOKEN={TOKEN}", "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl"
    )
    assert (
        installed.run(
            "start", FAKE_DOCKER_RUNNING="postgres keeper api worker caddy cloudflared"
        ).returncode
        == 0
    )
    installed.log.unlink()
    result = installed.run(
        "restore",
        "--replace",
        BRIEVIE_TUNNEL_WAIT="0",
        FAKE_DOCKER_RUNNING="postgres keeper api worker caddy cloudflared",
        **restore_inputs(installed),
    )
    assert result.returncode == 0, result.stderr
    assert installed.index("--profile tunnel stop api worker caddy cloudflared") >= 0


def test_the_token_never_appears_in_an_argv_or_output(installed: Install) -> None:
    with_settings(
        installed, f"BRIEVIE_TUNNEL_TOKEN={TOKEN}", "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl"
    )
    for running in (
        "postgres keeper api worker caddy cloudflared",
        "postgres keeper api worker caddy",
    ):
        result = run_start(installed, FAKE_DOCKER_RUNNING=running)
        assert result.returncode == 0, result.stderr
        assert TOKEN not in result.stdout + result.stderr
        for call in installed.calls():
            assert TOKEN not in " ".join(call["argv"])
    step_log = installed.root / "deploy" / ".brievie-step.log"
    if step_log.exists():
        assert TOKEN not in step_log.read_text()


@pytest.mark.parametrize(
    "value",
    [
        "https://brievie.example.nl",
        "brievie.example.nl:443",
        "brievie.example.nl/",
        "brievie example.nl",
        "-x.nl",
    ],
)
def test_start_refuses_a_public_name_that_is_not_a_bare_host(
    installed: Install, value: str
) -> None:
    with_settings(installed, f"BRIEVIE_PUBLIC_HOSTNAME={value}")
    result = installed.run("start")
    assert result.returncode != 0
    assert "BRIEVIE_PUBLIC_HOSTNAME in deploy/.env is geen kale naam" in result.stderr
    assert not any("up -d" in command for command in installed.commands())


@pytest.mark.parametrize(
    "value", ["brievie.example.nl", "nas.tail1234.ts.net", "192.168.1.20", "fd00::20"]
)
def test_start_accepts_bare_names(installed: Install, value: str) -> None:
    with_settings(installed, f"BRIEVIE_VPN_HOSTNAME={value}")
    result = installed.run("start")
    assert result.returncode == 0, result.stderr


def test_start_refuses_a_public_name_equal_to_the_home_name(installed: Install) -> None:
    with_settings(installed, "BRIEVIE_PUBLIC_HOSTNAME=Brievie.Local.")
    result = installed.run("start")
    assert result.returncode != 0
    assert "Gebruik elke naam maar één keer" in result.stderr


def test_compose_profiles_in_env_is_refused(installed: Install) -> None:
    # With it, compose would pull cloudflared into the main `up --wait`.
    with_settings(installed, "COMPOSE_PROFILES=tunnel")
    result = installed.run("start")
    assert result.returncode != 0
    assert "COMPOSE_PROFILES" in result.stderr
    assert not any("up -d" in command for command in installed.commands())


def test_start_warns_about_a_token_without_a_public_name(installed: Install) -> None:
    with_settings(installed, f"BRIEVIE_TUNNEL_TOKEN={TOKEN}")
    result = run_start(
        installed, FAKE_DOCKER_RUNNING="postgres keeper api worker caddy cloudflared"
    )
    assert result.returncode == 0, result.stderr
    assert "geen BRIEVIE_PUBLIC_HOSTNAME" in result.stdout


def test_start_prints_every_address(installed: Install) -> None:
    with_settings(
        installed,
        "BRIEVIE_HTTPS_PORT=8443",
        "BRIEVIE_VPN_HOSTNAME=nas.tail1234.ts.net",
        "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl",
        "BRIEVIE_TRUSTED_PROXIES=192.168.1.5/32",
    )
    result = installed.run("start")
    assert result.returncode == 0, result.stderr
    assert "Brievie draait op https://brievie.local:8443." in result.stdout
    assert "Via je VPN: https://nas.tail1234.ts.net:8443." in result.stdout
    assert "Van buiten (publiek adres): https://brievie.example.nl." in result.stdout
    assert "tweede stap" not in result.stdout


def test_start_prints_the_red_sentence_with_public_web_before_second_factor(
    installed: Install,
) -> None:
    with_settings(
        installed,
        "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl",
        "BRIEVIE_TRUSTED_PROXIES=192.168.1.5/32",
        "BRIEVIE_PUBLIC_WEB=true",
    )
    result = installed.run("start")
    assert result.returncode == 0, result.stderr
    assert (
        "Let op: inloggen op de webinterface kan van buiten zonder tweede stap "
        "(BRIEVIE_PUBLIC_WEB=true)." in result.stdout
    )


def test_start_warns_about_a_public_name_without_a_way(installed: Install) -> None:
    with_settings(installed, "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl")
    result = installed.run("start")
    assert result.returncode == 0, result.stderr
    assert "er is geen tunnel en geen BRIEVIE_TRUSTED_PROXIES" in result.stdout
    with_settings(
        installed,
        "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl",
        "BRIEVIE_TRUSTED_PROXIES=192.168.1.5/32",
    )
    result = installed.run("start")
    assert "er is geen tunnel" not in result.stdout
