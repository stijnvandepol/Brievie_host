from ninja import Schema


class ServerAddressesOut(Schema):
    urls: list[str]
    public_urls: list[str]


class AddressOut(Schema):
    url: str
    # home | vpn | public
    kind: str
    # private_ca | public
    certificate: str


class TunnelOut(Schema):
    # Kept for older clients: Brievie runs no tunnel any more, so always False / "not_set".
    token_set: bool = False
    # yes | no | not_set
    connected: str = "not_set"


class SeenAsOut(Schema):
    # private | public
    route: str
    secure: bool
    client_ip: str
    peer_ip: str


class CheckOut(Schema):
    # A code of the closed list (apps.access.status.CHECK_CODES); the web holds the text.
    code: str
    # ok | warning | problem
    level: str


class ExternalAccessOut(Schema):
    hostname: str
    vpn_hostname: str = ""
    public_hostname: str = ""
    addresses: list[AddressOut]
    public_web: bool
    tunnel: TunnelOut
    # Not secret: the addresses of your own reverse proxy.
    trusted_proxies: list[str]
    second_factor_available: bool
    seen_as: SeenAsOut
    checks: list[CheckOut]
    docs_url: str
    # A local address that asked for the public name without being a trusted proxy (likely
    # your proxy, before BRIEVIE_TRUSTED_PROXIES names it). Empty when none was seen.
    untrusted_public_name_peer: str = ""
