"""The server's names and the addresses that follow from them (S 6).

Pure: no Django import, so settings.py can call it before Django is configured and the
tests need nothing but values. Up to three names: the home name (`BRIEVIE_HOSTNAME`,
always), the server's own VPN name (`BRIEVIE_VPN_HOSTNAME`) and the public name of a
tunnel or an own reverse proxy (`BRIEVIE_PUBLIC_HOSTNAME`).
"""

import ipaddress
import re
from dataclasses import dataclass

# One DNS label: letters, digits and dashes, not starting or ending with a dash.
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


@dataclass(frozen=True)
class Addresses:
    allowed_hosts: tuple[str, ...]
    # Home, VPN, public: the order the app tries them in.
    public_urls: tuple[str, ...]
    # The URL(s) served with a public certificate (the public name), not the private CA.
    public_certificate_urls: tuple[str, ...]


def _normal(value: str) -> str:
    return value.strip().lower().rstrip(".")


def is_bare_host(value: str) -> bool:
    """Whether `value` is a host name or an IP address and nothing else: no scheme, port,
    path, user, wildcard or space."""
    if value != value.strip() or not value:
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        return True
    name = value.lower().removesuffix(".")
    if not name or len(name) > 253:
        return False
    return all(_LABEL.fullmatch(label) for label in name.split("."))


def name_problems(*, hostname: str, vpn_hostname: str, public_hostname: str) -> list[str]:
    """The variables among the two optional names that are not a bare host name, or that
    repeat a name before them (Caddy refuses a site address twice)."""
    problems: list[str] = []
    seen = {_normal(hostname)}
    for variable, value in (
        ("BRIEVIE_VPN_HOSTNAME", vpn_hostname),
        ("BRIEVIE_PUBLIC_HOSTNAME", public_hostname),
    ):
        if not value:
            continue
        if not is_bare_host(value) or _normal(value) in seen:
            problems.append(variable)
        seen.add(_normal(value))
    return problems


def _host_for_url(host: str) -> str:
    """`host` as it stands in a URL and in ALLOWED_HOSTS: an IPv6 address in brackets."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    return f"[{address}]" if address.version == 6 else host


def derive(*, hostname: str, vpn_hostname: str, public_hostname: str, https_port: int) -> Addresses:
    """ALLOWED_HOSTS and BRIEVIE_PUBLIC_URLS from the names.

    The home and VPN names are served by Caddy on the published https port with the
    private CA, so their URLs carry that port (as compose wrote it before: the same QR
    code after the update). The public name is a tunnel or a reverse proxy on 443 with a
    public certificate: no port. An empty optional name is left out.
    """
    hosts: list[str] = []
    urls: list[str] = []
    for name in (hostname, vpn_hostname):
        if name:
            host = _host_for_url(_normal(name))
            hosts.append(host)
            urls.append(f"https://{host}:{https_port}")
    certificate: list[str] = []
    if public_hostname:
        host = _host_for_url(_normal(public_hostname))
        hosts.append(host)
        urls.append(f"https://{host}")
        certificate.append(f"https://{host}")
    return Addresses(
        allowed_hosts=tuple(hosts),
        public_urls=tuple(urls),
        public_certificate_urls=tuple(certificate),
    )
