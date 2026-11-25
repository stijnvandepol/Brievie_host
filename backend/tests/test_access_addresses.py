"""The server's names and addresses, derived in one pure function (S 6).

`derive` is pure (no Django), so most tests call it directly; the settings that use it
are started in a fresh process with a chosen environment, as a container starts them.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from apps.access.addresses import Addresses, derive, is_bare_host, name_problems

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: nothing here uses the database."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def test_home_only_gives_one_host_and_url() -> None:
    assert derive(
        hostname="192.168.1.20", vpn_hostname="", public_hostname="", https_port=443
    ) == Addresses(
        allowed_hosts=("192.168.1.20",),
        public_urls=("https://192.168.1.20:443",),
        public_certificate_urls=(),
    )


def test_the_home_url_keeps_its_port_as_compose_wrote_it() -> None:
    # Ruling: home and VPN URLs keep the explicit port, byte for byte what compose derived
    # before (`https://${BRIEVIE_HOSTNAME}:${BRIEVIE_HTTPS_PORT:-443}`): a home install
    # gets the same QR code and links after the update.
    assert derive(
        hostname="brievie.local", vpn_hostname="", public_hostname="", https_port=8443
    ).public_urls == ("https://brievie.local:8443",)


def test_vpn_name_comes_second_with_the_https_port() -> None:
    found = derive(
        hostname="192.168.1.20",
        vpn_hostname="nas.tail1234.ts.net",
        public_hostname="",
        https_port=8443,
    )
    assert found.allowed_hosts == ("192.168.1.20", "nas.tail1234.ts.net")
    assert found.public_urls == ("https://192.168.1.20:8443", "https://nas.tail1234.ts.net:8443")
    assert found.public_certificate_urls == ()


def test_public_name_comes_last_without_port() -> None:
    found = derive(
        hostname="192.168.1.20",
        vpn_hostname="nas.tail1234.ts.net",
        public_hostname="brievie.example.nl",
        https_port=8443,
    )
    assert found.allowed_hosts == ("192.168.1.20", "nas.tail1234.ts.net", "brievie.example.nl")
    assert found.public_urls[-1] == "https://brievie.example.nl"
    assert found.public_certificate_urls == ("https://brievie.example.nl",)


def test_ipv6_host_gets_brackets_in_the_url() -> None:
    found = derive(hostname="fd00::20", vpn_hostname="", public_hostname="", https_port=443)
    assert found.public_urls == ("https://[fd00::20]:443",)
    assert found.allowed_hosts == ("[fd00::20]",)


def test_names_are_lowered_and_lose_a_trailing_dot() -> None:
    found = derive(
        hostname="Brievie.Local.", vpn_hostname="", public_hostname="Brievie.Example.NL",
        https_port=443,
    )  # fmt: skip
    assert found.allowed_hosts == ("brievie.local", "brievie.example.nl")


@pytest.mark.parametrize(
    "value",
    [
        "https://brievie.example.nl",
        "brievie.example.nl/",
        "brievie.example.nl:443",
        "brievie example.nl",
        "",
        "-brievie.nl",
        "brievie..nl",
        "brievie.nl\n",
        "*.example.nl",
        "user@brievie.nl",
        "a" * 254,
    ],
)
def test_is_bare_host_refuses_scheme_path_port_and_spaces(value: str) -> None:
    assert not is_bare_host(value)


@pytest.mark.parametrize(
    "value", ["brievie.example.nl", "nas.tail1234.ts.net", "192.168.1.20", "fd00::20", "localhost"]
)
def test_is_bare_host_accepts_names_and_addresses(value: str) -> None:
    assert is_bare_host(value)


def test_name_problems_finds_duplicates_and_bad_names() -> None:
    assert name_problems(hostname="a.nl", vpn_hostname="", public_hostname="") == []
    assert name_problems(hostname="a.nl", vpn_hostname="b.nl", public_hostname="c.nl") == []
    assert name_problems(hostname="a.nl", vpn_hostname="", public_hostname="A.nl") == [
        "BRIEVIE_PUBLIC_HOSTNAME"
    ]
    assert name_problems(hostname="a.nl", vpn_hostname="c.nl", public_hostname="c.nl") == [
        "BRIEVIE_PUBLIC_HOSTNAME"
    ]
    assert name_problems(hostname="a.nl", vpn_hostname="https://b.nl", public_hostname="") == [
        "BRIEVIE_VPN_HOSTNAME"
    ]


def test_the_module_imports_no_django() -> None:
    source = (BACKEND / "apps" / "access" / "addresses.py").read_text()
    assert "import django" not in source
    assert "from django" not in source


def _settings(environ: dict[str, str], *names: str) -> dict[str, object]:
    script = (
        "import json, django; from django.conf import settings; django.setup();"
        "from apps.common import checks;"
        f"names = {list(names)!r};"
        "out = {n: getattr(settings, n) for n in names};"
        "found = checks.check_public_urls(None) + checks.check_names(None);"
        "out['checks'] = sorted(m.id for m in found);"
        "print(json.dumps(out))"
    )
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env={
            "PATH": os.environ.get("PATH", ""),
            "DJANGO_SETTINGS_MODULE": "brievie.settings",
            "SECRET_KEY": "k" * 60,
            "DATABASE_URL": "postgres://u:p@h:5432/d",
            **environ,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    loaded = json.loads(done.stdout.strip().splitlines()[-1])
    assert isinstance(loaded, dict)
    return loaded


def test_settings_derive_from_the_names() -> None:
    found = _settings(
        {
            "BRIEVIE_HOSTNAME": "192.168.1.20",
            "BRIEVIE_VPN_HOSTNAME": "nas.tail1234.ts.net",
            "BRIEVIE_PUBLIC_HOSTNAME": "brievie.example.nl",
            "BRIEVIE_HTTPS_PORT": "443",
            "ALLOWED_HOSTS": "",
            "BRIEVIE_PUBLIC_URLS": "",
        },
        "ALLOWED_HOSTS",
        "BRIEVIE_PUBLIC_URLS",
        "BRIEVIE_PUBLIC_CERTIFICATE_URLS",
    )
    assert found["ALLOWED_HOSTS"] == ["192.168.1.20", "nas.tail1234.ts.net", "brievie.example.nl"]
    assert found["BRIEVIE_PUBLIC_URLS"] == [
        "https://192.168.1.20:443",
        "https://nas.tail1234.ts.net:443",
        "https://brievie.example.nl",
    ]
    assert found["BRIEVIE_PUBLIC_CERTIFICATE_URLS"] == ["https://brievie.example.nl"]
    assert found["checks"] == []


def test_without_a_hostname_nothing_is_derived() -> None:
    found = _settings({}, "ALLOWED_HOSTS", "BRIEVIE_PUBLIC_URLS")
    assert found["ALLOWED_HOSTS"] == ["localhost"]
    assert found["BRIEVIE_PUBLIC_URLS"] == []


def test_explicit_allowed_hosts_and_public_urls_still_win() -> None:
    found = _settings(
        {
            "BRIEVIE_HOSTNAME": "192.168.1.20",
            "BRIEVIE_PUBLIC_HOSTNAME": "brievie.example.nl",
            "ALLOWED_HOSTS": "192.168.1.20,other.example.nl",
            "BRIEVIE_PUBLIC_URLS": "https://192.168.1.20,https://other.example.nl",
        },
        "ALLOWED_HOSTS",
        "BRIEVIE_PUBLIC_URLS",
        "BRIEVIE_PUBLIC_CERTIFICATE_URLS",
    )
    assert found["ALLOWED_HOSTS"] == ["192.168.1.20", "other.example.nl"]
    assert found["BRIEVIE_PUBLIC_URLS"] == ["https://192.168.1.20", "https://other.example.nl"]
    # The public name is not in the explicit list: no URL is served with a public certificate.
    assert found["BRIEVIE_PUBLIC_CERTIFICATE_URLS"] == []


def test_e002_when_public_name_equals_home_name() -> None:
    found = _settings(
        {"BRIEVIE_HOSTNAME": "brievie.example.nl", "BRIEVIE_PUBLIC_HOSTNAME": "brievie.example.nl"}
    )
    assert "brievie.E002" in found["checks"]  # type: ignore[operator]


def test_e002_when_vpn_name_is_not_a_bare_host() -> None:
    found = _settings(
        {"BRIEVIE_HOSTNAME": "192.168.1.20", "BRIEVIE_VPN_HOSTNAME": "https://nas.ts.net"}
    )
    assert "brievie.E002" in found["checks"]  # type: ignore[operator]


def test_e001_still_reported() -> None:
    found = _settings(
        {
            "BRIEVIE_HOSTNAME": "192.168.1.20",
            "ALLOWED_HOSTS": "192.168.1.20",
            "BRIEVIE_PUBLIC_URLS": "https://brievie.example.nl",
        }
    )
    assert found["checks"] == ["brievie.E001"]
