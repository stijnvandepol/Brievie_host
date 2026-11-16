from django.conf import settings
from django.http import HttpRequest
from ninja import NinjaAPI
from pydantic import BaseModel

from apps.accounts.api import router as accounts_router
from apps.accounts.auth import session_auth
from apps.common.errors import install_error_handlers
from apps.common.schemas import HealthOut
from apps.devices.api import router as devices_router
from apps.devices.auth import DeviceTokenAuth
from apps.documents.api import router as documents_router
from apps.documents.schemas import UploadMetadataIn
from apps.labels.api import router as labels_router
from apps.people.api import router as people_router
from apps.pipeline.api import router as pipeline_router
from apps.rules.api import router as rules_router
from apps.workspaces.api import router as workspaces_router


def docs_urls(*, debug: bool) -> dict[str, str | None]:
    """The interactive docs and the live schema only in development.

    The contract is the committed docs/api/openapi.json, not a public endpoint.
    """
    if debug:
        return {"docs_url": "/docs", "openapi_url": "/openapi.json"}
    return {"docs_url": None, "openapi_url": None}


# In the exported contract although no operation names them: the JSON inside the
# `metadata` field of the multipart upload.
CONTRACT_ONLY_SCHEMAS: list[type[BaseModel]] = [UploadMetadataIn]

_urls = docs_urls(debug=settings.DEBUG)
api = NinjaAPI(
    title="Brievie",
    version="1.0.0",
    urls_namespace="api-v1",
    # A paired device's bearer token first, then the web session (with CSRF).
    auth=[DeviceTokenAuth(), session_auth],
    docs_url=_urls["docs_url"],
    openapi_url=_urls["openapi_url"],
)
install_error_handlers(api)


@api.get("/health", response=HealthOut, auth=None, operation_id="getHealth")
def get_health(request: HttpRequest) -> HealthOut:
    return HealthOut(status="ok", api_version=api.version)


api.add_router("", accounts_router)
api.add_router("", workspaces_router)
api.add_router("", documents_router)
api.add_router("", people_router)
api.add_router("", labels_router)
api.add_router("", rules_router)
api.add_router("", pipeline_router)
api.add_router("", devices_router)
