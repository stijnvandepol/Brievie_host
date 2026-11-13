from django.http import HttpRequest
from ninja import NinjaAPI

from apps.common.errors import install_error_handlers
from apps.common.schemas import HealthOut

api = NinjaAPI(title="Brievie", version="1.0.0", urls_namespace="api-v1")
install_error_handlers(api)


@api.get("/health", response=HealthOut, auth=None, operation_id="getHealth")
def get_health(request: HttpRequest) -> HealthOut:
    return HealthOut(status="ok", api_version=api.version)
