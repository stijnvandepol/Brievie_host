"""The public route is app-only unless `BRIEVIE_PUBLIC_WEB` (S 7.6).

Through a tunnel or an own reverse proxy (Caddy marks the route, apps.access.route) the
web interface and its password sign-in stay closed by default. What passes is decided by
the endpoint, never by a header the request carries:

- the app's unauthenticated operations (`APP_OPERATIONS`: up check, the CA to pin,
  pairing; the code itself is made in a signed-in web session at home), and
- operations that accept the device token (their auth includes `DeviceTokenAuth`); their
  own auth then checks the token as everywhere. Such an operation may also accept a web
  session, but `SessionAuth` refuses every session on this route (`session_allowed`).

Everything else (sign-in, setup, password reset, invitations, every session-only
operation, every web page and file, any path no operation answers) gets 403
`public_web_off`. Lives in Django because only Django knows which operation is which;
Caddy only marks the route.
"""

from collections.abc import Callable, Iterable

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.urls import Resolver404, resolve
from django.utils import translation
from django.utils.html import escape
from django.utils.translation import gettext as _
from ninja import NinjaAPI
from ninja.constants import NOT_SET
from ninja.operation import Operation, PathView

from apps.access import observed
from apps.access.route import is_public_route, peer_ip, via
from apps.common.errors import error_response
from apps.devices.auth import DeviceTokenAuth

APP_OPERATIONS: frozenset[str] = frozenset(
    {
        "getHealth",
        "getCaCertificate",
        "createPairingChallenge",
        "pairDevice",
        "pairDeviceWithProof",
    }
)
CODE = "public_web_off"


def session_allowed(request: HttpRequest) -> bool:
    """Whether a web session may count for this request: not on the public route while
    the web interface is off there. (Two-step sign-in hooks its rule in here, B2.)"""
    return settings.BRIEVIE_PUBLIC_WEB or not is_public_route(request)


def _auth_list(auth: object) -> list[object]:
    if isinstance(auth, list | tuple):
        return list(auth)
    return [] if auth is None else [auth]


def accepts_device_token(operation: Operation, api: NinjaAPI) -> bool:
    """Whether the operation's auth (its own, else the API's default) includes the
    device token. `auth=None` and session-only operations do not."""
    auth: object = operation.auth_param
    if auth is NOT_SET:
        auth = api.auth
    return any(isinstance(callback, DeviceTokenAuth) for callback in _auth_list(auth))


def open_on_public_route(operation: Operation, api: NinjaAPI) -> bool:
    return operation.operation_id in APP_OPERATIONS or accepts_device_token(operation, api)


def _api() -> NinjaAPI:
    # Imported late: brievie.api imports the auth classes, which import this module.
    from brievie.api import api

    return api


def operations(api: NinjaAPI) -> Iterable[Operation]:
    for _prefix, router in api._routers:
        for path_view in router.path_operations.values():
            yield from path_view.operations


def _path_view(view: Callable[..., object]) -> PathView | None:
    """The Ninja PathView behind a resolved view (the wrapper closes over it). None
    when it cannot be found: the request is then refused, never let through."""
    for cell in getattr(view, "__closure__", None) or ():
        try:
            content = cell.cell_contents
        except ValueError:
            continue
        if isinstance(content, PathView):
            return content
    return None


def operation_for(request: HttpRequest) -> Operation | None:
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None
    path_view = _path_view(match.func)
    if path_view is None:
        return None
    for operation in path_view.operations:
        if request.method in operation.methods:
            return operation
    return None


def _sentence() -> str:
    return _("Op dit adres werkt alleen de Brievie-app. Gebruik de webinterface thuis of via VPN.")


def _page() -> HttpResponse:
    language = translation.get_language()
    title = escape(_("Alleen de app"))
    body = escape(_sentence())
    html = (
        f'<!doctype html><html lang="{escape(language)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Brievie · {title}</title></head>"
        f'<body style="font-family: system-ui, sans-serif; margin: 3rem auto; max-width: 32rem; '
        f'padding: 0 1rem"><h1>Brievie</h1><p>{body}</p></body></html>'
    )
    return HttpResponse(html, status=403, content_type="text/html; charset=utf-8")


class PublicRouteGate:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        observe(request)
        if session_allowed(request):
            return self.get_response(request)
        path = request.path_info
        is_api = path == "/api" or path.startswith("/api/")
        if is_api:
            operation = operation_for(request)
            if operation is not None and open_on_public_route(operation, _api()):
                return self.get_response(request)
        language = translation.get_language_from_request(request)
        with translation.override(language):
            response = error_response(403, CODE, _sentence()) if is_api else _page()
        response["Cache-Control"] = "no-store"
        return response


def observe(request: HttpRequest) -> None:
    """Remember the ways in that the settings page should warn about (apps.access.observed)."""
    way = via(request)
    if way == "public_name":
        observed.note("public_name_untrusted", peer=peer_ip(request))
    elif way == "cloudflare":
        observed.note("cloudflare_on_https_port")
    elif way == "tunnel":
        observed.note("tunnel")
