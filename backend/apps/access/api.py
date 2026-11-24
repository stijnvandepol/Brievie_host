"""The server's addresses for paired apps, and (for server admins) its external access."""

from django.http import HttpRequest
from ninja import Router

from apps.access import services
from apps.access.schemas import ServerAddressesOut
from apps.common.schemas import AUTH_ERRORS

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
