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
    token_set: bool
    # yes | no | not_set
    connected: str


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
