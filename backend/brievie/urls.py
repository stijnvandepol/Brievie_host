from django.urls import path, re_path

from apps.common.errors import api_path_not_found
from apps.common.health import mark_non_atomic
from apps.common.web import web_app
from brievie.api import api

urlpatterns = [
    # Ninja has no route for the bare root and would answer with Django's HTML 404.
    path("api/v1/", api_path_not_found),
    # The health check answers 503 (not Django's 500) when the database is down.
    path("api/v1/", mark_non_atomic(api.urls, "health")),
    re_path(r"^api(?:/|$)", api_path_not_found),
    # Last: every other path is a route of the web app.
    re_path(r"^(?P<path>.*)$", web_app),
]
