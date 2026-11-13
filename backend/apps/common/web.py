from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_safe


@require_safe
@ensure_csrf_cookie
@transaction.non_atomic_requests
def web_app(request: HttpRequest, path: str) -> HttpResponse:
    """The single-page web app: its index for every path that is not a file or /api.

    Built files (assets, favicon) are served by WhiteNoise before the URL resolver
    runs, so a path that reaches this view with a file extension is a file that does
    not exist: 404, never the index. The index sets the CSRF cookie that the app
    echoes in `X-CSRFToken` on its first write, whatever the session looks like.
    """
    if "." in path.rsplit("/", 1)[-1]:
        raise Http404
    index = settings.BRIEVIE_WEB_DIST / "index.html"
    try:
        content = index.read_bytes()
    except FileNotFoundError:
        raise Http404 from None
    response = HttpResponse(content, content_type="text/html; charset=utf-8")
    # Revalidate on every visit: a new build changes the hashed asset names it points to.
    response["Cache-Control"] = "no-cache"
    return response
