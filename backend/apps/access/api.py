"""The server's addresses for paired apps, and (for server admins) its external access."""

from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from ninja import Router

from apps.access import services, status
from apps.access.schemas import (
    AddressOut,
    CheckOut,
    ExternalAccessOut,
    SeenAsOut,
    ServerAddressesOut,
    TunnelOut,
)
from apps.accounts.auth import session_auth
from apps.accounts.services import current_user
from apps.common.errors import ApiError
from apps.common.schemas import AUTH_ERRORS, ErrorOut

router = Router(tags=["server"])


@router.get(
    "/server/addresses",
    response={200: ServerAddressesOut, **AUTH_ERRORS},
    description=(
        "The addresses of this server, home first, and those of them served with a public "
        "certificate (a tunnel or an own reverse proxy). A paired app refreshes its "
        "addresses from here over a verified connection only."
    ),
    operation_id="getServerAddresses",
)
def get_server_addresses(request: HttpRequest) -> ServerAddressesOut:
    found = services.server_addresses()
    return ServerAddressesOut(urls=found.urls, public_urls=found.public_urls)


@router.get(
    "/server/external-access",
    response={200: ExternalAccessOut, 403: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    description=(
        "Server admins only, web session only (a device token answers 401). What is set for "
        "reaching this server from outside the home network, what works and what not, as "
        "check codes. Compose decides; nothing here changes a setting. Never a token or "
        "secret: only whether a tunnel token is set. Waits up to 2 seconds for the tunnel, "
        "outside a transaction. Cache-Control: no-store."
    ),
    operation_id="getExternalAccess",
)
def get_external_access(request: HttpRequest, response: HttpResponse) -> ExternalAccessOut:
    user = current_user(request)
    if not (user.is_active and user.is_server_admin):
        raise ApiError(403, "forbidden", _("Alleen een serverbeheerder mag dit doen."))
    found = status.external_access_status(request)
    response["Cache-Control"] = "no-store"
    return ExternalAccessOut(
        hostname=found.hostname,
        vpn_hostname=found.vpn_hostname,
        public_hostname=found.public_hostname,
        addresses=[
            AddressOut(url=a.url, kind=a.kind, certificate=a.certificate) for a in found.addresses
        ],
        public_web=found.public_web,
        tunnel=TunnelOut(token_set=found.tunnel_token_set, connected=found.tunnel_connected),
        trusted_proxies=found.trusted_proxies,
        second_factor_available=found.second_factor_available,
        seen_as=SeenAsOut(
            route=found.seen_as.route,
            secure=found.seen_as.secure,
            client_ip=found.seen_as.client_ip,
            peer_ip=found.seen_as.peer_ip,
        ),
        checks=[CheckOut(code=c.code, level=c.level) for c in found.checks],
        docs_url=found.docs_url,
    )
