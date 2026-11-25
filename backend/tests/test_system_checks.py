import secrets
from pathlib import Path

import pytest
from django.core.checks import Error, Warning, run_checks
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import SecondFactor
from apps.common.checks import (
    check_database_passwords,
    check_external_access,
    check_public_urls,
    check_second_factor_rule,
    check_trusted_proxies,
)
from tests.factories import UserFactory


@pytest.fixture(autouse=True)
def _no_route_table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The runner's own routes stay out of the trusted-proxy checks; a test gives its own."""
    from apps.common import checks

    monkeypatch.setattr(checks, "ROUTE_TABLE", tmp_path / "no-route-table")


def test_public_url_with_an_allowed_host_passes(settings: Settings) -> None:
    settings.ALLOWED_HOSTS = ["192.168.1.20"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://192.168.1.20", "https://192.168.1.20:8443"]

    assert check_public_urls(None) == []


def test_public_url_host_outside_allowed_hosts_is_an_error(settings: Settings) -> None:
    settings.ALLOWED_HOSTS = ["localhost"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://localhost", "https://brievie.local:8443"]

    errors = check_public_urls(None)

    assert len(errors) == 1
    assert isinstance(errors[0], Error)
    assert errors[0].id == "brievie.E001"
    assert "brievie.local" in errors[0].msg
    assert "ALLOWED_HOSTS" in errors[0].msg
    assert errors[0].hint is not None
    assert "BRIEVIE_HOSTNAME" in errors[0].hint


def test_subdomain_patterns_and_wildcards_are_honoured(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://post.example.nl"]
    settings.ALLOWED_HOSTS = [".example.nl"]
    assert check_public_urls(None) == []
    settings.ALLOWED_HOSTS = ["*"]
    assert check_public_urls(None) == []


def test_ipv6_host_is_compared_with_its_brackets(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://[fd00::1]:8443"]
    settings.ALLOWED_HOSTS = ["[fd00::1]"]
    assert check_public_urls(None) == []
    settings.ALLOWED_HOSTS = ["localhost"]
    assert len(check_public_urls(None)) == 1


def test_the_check_is_registered(settings: Settings) -> None:
    settings.ALLOWED_HOSTS = ["localhost"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://elders.example"]

    assert "brievie.E001" in {m.id for m in run_checks()}


def test_placeholder_database_password_is_a_warning(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEBUG = False
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", "change-me-app")
    settings.DATABASE_OWNER = {"PASSWORD": "change-me-owner"}

    messages = check_database_passwords(None)

    assert [m.id for m in messages] == ["brievie.W001", "brievie.W001"]
    assert all(isinstance(m, Warning) for m in messages)
    assert all("change-me" not in (m.hint or "") for m in messages)


def test_real_database_passwords_pass(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.DEBUG = False
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", "7f3a9c")
    settings.DATABASE_OWNER = {"PASSWORD": "b41d02"}

    assert check_database_passwords(None) == []


def test_placeholder_database_password_is_fine_in_debug(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEBUG = True
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", "change-me-app")
    settings.DATABASE_OWNER = None

    assert check_database_passwords(None) == []


def test_any_password_containing_change_me_is_a_warning(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEBUG = False
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", "x-change-me-too")
    settings.DATABASE_OWNER = None

    assert [m.id for m in check_database_passwords(None)] == ["brievie.W001"]


# --- External access (S 7.5, 7.6) ----------------------------------------------------------


def _external(
    settings: Settings,
    *,
    tunnel: bool,
    proxies: list[str],
    public_web: bool,
    second_factor: bool = False,
) -> None:
    # `second_factor` False: as if the second step were somehow not required outside (it is
    # since Task 13 of the two-step plan, so W002 is silent in a real install).
    settings.BRIEVIE_TUNNEL_TOKEN_SET = tunnel
    settings.BRIEVIE_TRUSTED_PROXIES = proxies
    settings.BRIEVIE_PUBLIC_WEB = public_web
    settings.BRIEVIE_SECOND_FACTOR_AVAILABLE = second_factor
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 33554432
    settings.BRIEVIE_PUBLIC_HOSTNAME = "brievie.example.nl" if tunnel or proxies else ""


@pytest.mark.parametrize(("tunnel", "proxies"), [(True, []), (False, ["192.168.1.5/32"])])
def test_w002_public_web_without_second_factor(
    settings: Settings, tunnel: bool, proxies: list[str]
) -> None:
    _external(settings, tunnel=tunnel, proxies=proxies, public_web=True)
    found = check_external_access(None)
    assert [m.id for m in found] == ["brievie.W002"]
    assert isinstance(found[0], Warning)


@pytest.mark.parametrize(("tunnel", "proxies"), [(True, []), (False, ["192.168.1.5/32"])])
def test_w002_silent_now_the_public_route_needs_the_second_step(
    settings: Settings, tunnel: bool, proxies: list[str]
) -> None:
    _external(settings, tunnel=tunnel, proxies=proxies, public_web=True, second_factor=True)
    assert check_external_access(None) == []


def test_w002_silent_when_public_web_off(settings: Settings) -> None:
    _external(settings, tunnel=True, proxies=["192.168.1.5/32"], public_web=False)
    assert check_external_access(None) == []


def test_w002_silent_without_a_public_way(settings: Settings) -> None:
    _external(settings, tunnel=False, proxies=[], public_web=True)
    assert check_external_access(None) == []


def test_w003_upload_limit_above_cloudflare_with_tunnel(settings: Settings) -> None:
    _external(settings, tunnel=True, proxies=[], public_web=False)
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 100_000_001
    assert [m.id for m in check_external_access(None)] == ["brievie.W003"]
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 100_000_000
    assert check_external_access(None) == []


def test_w003_silent_without_tunnel(settings: Settings) -> None:
    _external(settings, tunnel=False, proxies=["192.168.1.5/32"], public_web=False)
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 200_000_000
    assert check_external_access(None) == []


def test_warnings_never_print_the_token(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Made at run time: no token-like literal in the repository.
    secret = secrets.token_urlsafe(32)
    monkeypatch.setenv("BRIEVIE_TUNNEL_TOKEN", secret)
    monkeypatch.setenv("TUNNEL_TOKEN", secret)
    _external(settings, tunnel=True, proxies=[], public_web=True)
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 200_000_000
    found = check_external_access(None)
    assert {m.id for m in found} == {"brievie.W002", "brievie.W003"}
    for message in found:
        assert secret not in f"{message.msg} {message.hint}"
    assert not hasattr(settings, "BRIEVIE_TUNNEL_TOKEN")


def test_w005_public_name_without_a_way(settings: Settings) -> None:
    _external(settings, tunnel=False, proxies=[], public_web=False)
    settings.BRIEVIE_PUBLIC_HOSTNAME = "brievie.example.nl"
    assert [m.id for m in check_external_access(None)] == ["brievie.W005"]
    settings.BRIEVIE_TRUSTED_PROXIES = ["192.168.1.5/32"]
    assert check_external_access(None) == []
    settings.BRIEVIE_TRUSTED_PROXIES = []
    settings.BRIEVIE_TUNNEL_TOKEN_SET = True
    assert check_external_access(None) == []


# --- BRIEVIE_TRUSTED_PROXIES (security review of PR #53, point 4) ------------------------


@pytest.mark.parametrize(
    "value",
    [
        ["0.0.0.0/0"],
        ["::/0"],
        ["192.168.0.0/16"],
        ["10.0.0.0/8"],
        ["fd00::/48"],
        ["private_ranges"],
        ["192.168.1.300"],
        ["proxy.local"],
    ],
)
def test_a_wide_or_invalid_trusted_proxy_is_refused(settings: Settings, value: list[str]) -> None:
    settings.BRIEVIE_TRUSTED_PROXIES = value
    settings.BRIEVIE_TRUSTED_PROXIES_WIDE = False
    found = check_trusted_proxies(None)
    assert [m.id for m in found] == ["brievie.E003"]
    assert isinstance(found[0], Error)


@pytest.mark.parametrize(
    "value",
    [["192.168.1.5/32"], ["192.168.1.5"], ["10.1.2.0/24", "192.168.1.5/32"], ["fd00::5/64"], []],
)
def test_a_narrow_trusted_proxy_passes(settings: Settings, value: list[str]) -> None:
    settings.BRIEVIE_TRUSTED_PROXIES = value
    settings.BRIEVIE_TRUSTED_PROXIES_WIDE = False
    assert check_trusted_proxies(None) == []


def test_a_wide_range_passes_only_when_forced(settings: Settings) -> None:
    settings.BRIEVIE_TRUSTED_PROXIES = ["10.0.0.0/8"]
    settings.BRIEVIE_TRUSTED_PROXIES_WIDE = True
    assert check_trusted_proxies(None) == []
    # Never everything, forced or not.
    settings.BRIEVIE_TRUSTED_PROXIES = ["0.0.0.0/0"]
    assert [m.id for m in check_trusted_proxies(None)] == ["brievie.E003"]
    settings.BRIEVIE_TRUSTED_PROXIES = ["::/0"]
    assert [m.id for m in check_trusted_proxies(None)] == ["brievie.E003"]


@pytest.mark.parametrize("value", ["172.17.0.1", "172.18.0.1/32", "172.31.0.1"])
def test_a_docker_gateway_is_a_warning_w006(settings: Settings, value: str) -> None:
    settings.BRIEVIE_TRUSTED_PROXIES = [value]
    settings.BRIEVIE_TRUSTED_PROXIES_WIDE = False
    assert [m.id for m in check_trusted_proxies(None)] == ["brievie.W006"]


def test_a_required_second_factor_without_an_admin_who_has_one_warns(settings: Settings) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "server_admins"
    UserFactory.create(is_server_admin=True)

    (message,) = check_second_factor_rule(None)

    assert isinstance(message, Warning)
    assert message.id == "brievie.W004"
    assert message.msg == "Nog geen serverbeheerder heeft tweestapsverificatie aan."


def test_an_unconfirmed_factor_does_not_count(settings: Settings) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    admin = UserFactory.create(is_server_admin=True)
    SecondFactor.objects.create(user=admin, secret_sealed=b"x")

    assert [m.id for m in check_second_factor_rule(None)] == ["brievie.W004"]


def test_an_admin_with_a_confirmed_factor_silences_it(settings: Settings) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    admin = UserFactory.create(is_server_admin=True)
    SecondFactor.objects.create(user=admin, secret_sealed=b"x", confirmed_at=timezone.now())

    assert check_second_factor_rule(None) == []


def test_no_rule_no_warning(settings: Settings) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "none"
    UserFactory.create(is_server_admin=True)

    assert check_second_factor_rule(None) == []


ROUTES = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
eth0\t00000000\t0100A8C0\t0003\t0\t0\t0\t00000000\t0\t0\t0
eth0\t0000A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0
eth1\t0000140A\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0
"""


def test_docker_gateways_come_from_the_containers_own_routes(tmp_path: Path) -> None:
    from apps.common.checks import docker_gateways

    table = tmp_path / "route"
    table.write_text(ROUTES)
    found = {str(address) for address in docker_gateways(table)}
    # The default gateway (192.168.0.1) and the first address of each network: 192.168.0.1
    # and 10.20.0.1 (a custom network outside 172.16.0.0/12).
    assert found == {"192.168.0.1", "10.20.0.1"}
    assert docker_gateways(tmp_path / "missing") == set()


@pytest.mark.parametrize("value", ["192.168.0.1", "10.20.0.1/32"])
def test_a_gateway_of_a_custom_network_is_a_warning_too(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    from apps.common import checks

    table = tmp_path / "route"
    table.write_text(ROUTES)
    monkeypatch.setattr(checks, "ROUTE_TABLE", table)
    settings.BRIEVIE_TRUSTED_PROXIES = [value]
    settings.BRIEVIE_TRUSTED_PROXIES_WIDE = False
    assert [m.id for m in check_trusted_proxies(None)] == ["brievie.W006"]
    settings.BRIEVIE_TRUSTED_PROXIES = ["192.168.0.5/32"]
    assert check_trusted_proxies(None) == []
