from django.urls import path, re_path

from apps.common.errors import api_path_not_found
from apps.common.web import web_app
from brievie.api import api

urlpatterns = [
    path("api/v1/", api.urls),
    re_path(r"^api(?:/|$)", api_path_not_found),
    # Last: every other path is a route of the web app.
    re_path(r"^(?P<path>.*)$", web_app),
]
