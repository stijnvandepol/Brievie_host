from django.contrib import admin
from django.urls import path, re_path

from apps.common.errors import api_path_not_found
from brievie.api import api

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", api.urls),
    re_path(r"^api/", api_path_not_found),
]
