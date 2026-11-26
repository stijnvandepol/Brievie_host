"""./brievie, the names of the ways in from outside, and an old tunnel setting (S 10)."""

import secrets
from pathlib import Path

import pytest

from tests.test_brievie_wrapper import Install, current_env

# Made at run time: no token-like literal in the repository.
TOKEN = "eyJ" + secrets.token_urlsafe(48)


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


OLD_TUNNEL = "BRIEVIE_TUNNEL_TOKEN staat nog in deploy/.env"


def test_start_without_a_tunnel_setting_says_nothing_about_a_tunnel(installed: Install) -> None:
    result = installed.run("start")
    assert result.returncode == 0, result.stderr
    assert not any("cloudflared" in c or "--profile" in c for c in installed.commands())
    assert OLD_TUNNEL not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "line",
    [f"BRIEVIE_TUNNEL_TOKEN={TOKEN}", "BRIEVIE_TUNNEL_TOKEN_FILE=/volume1/brievie/tunnel-token"],
)
def test_an_old_tunnel_setting_gets_a_clear_message_and_the_start_goes_on(
    installed: Install, line: str
) -> None:
    with_settings(installed, line, "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl")
    result = installed.run("start")
    assert result.returncode == 0, result.stderr
    assert OLD_TUNNEL in result.stderr
    assert "start niet meer" in result.stderr
    assert "BRIEVIE_PUBLIC_HOSTNAME" in result.stderr
    # Nothing starts a tunnel, and the main start carries no profile.
    assert not any("cloudflared" in c or "--profile" in c for c in installed.commands())
    up = installed.index("up -d --build --wait")
    assert "--profile" not in installed.commands()[up]


def test_the_old_token_never_appears_in_an_argv_or_output(installed: Install) -> None:
    with_settings(installed, f"BRIEVIE_TUNNEL_TOKEN={TOKEN}")
    result = installed.run("start")
    assert result.returncode == 0, result.stderr
    assert TOKEN not in result.stdout + result.stderr
    for call in installed.calls():
        assert TOKEN not in " ".join(call["argv"])


def test_a_leftover_compose_profiles_line_is_not_refused(installed: Install) -> None:
    # The profile no longer exists: compose ignores it, so the start must not stop on it.
    with_settings(installed, "COMPOSE_PROFILES=tunnel")
    result = installed.run("start")
    assert result.returncode == 0, result.stderr


def test_restore_stops_the_services_without_a_profile(installed: Install) -> None:
    from tests.test_brievie_wrapper import restore_inputs

    with_settings(
        installed, f"BRIEVIE_TUNNEL_TOKEN={TOKEN}", "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl"
    )
    result = installed.run("restore", "--replace", **restore_inputs(installed))
    assert result.returncode == 0, result.stderr
    assert installed.index("stop api worker caddy") >= 0
    assert not any("--profile" in command for command in installed.commands())


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
    assert "BRIEVIE_TRUSTED_PROXIES niet" in result.stdout
    with_settings(
        installed,
        "BRIEVIE_PUBLIC_HOSTNAME=brievie.example.nl",
        "BRIEVIE_TRUSTED_PROXIES=192.168.1.5/32",
    )
    result = installed.run("start")
    assert "BRIEVIE_TRUSTED_PROXIES niet" not in result.stdout
