from django.conf import settings
from django.http import HttpRequest
from django.utils.translation import gettext as _
from ninja import NinjaAPI
from pydantic import BaseModel

from apps.access.api import router as access_router
from apps.accounts.api import router as accounts_router
from apps.accounts.auth import session_auth
from apps.audit.api import router as audit_router
from apps.backup.api import router as backup_router
from apps.common import health
from apps.common.errors import ApiError, install_error_handlers
from apps.common.schemas import ErrorOut, HealthOut
from apps.common.version import server_version
from apps.devices.api import router as devices_router
from apps.devices.auth import DeviceTokenAuth
from apps.documents.api import router as documents_router
from apps.documents.duplicates_api import router as duplicates_router
from apps.documents.schemas import FileMetadataIn, UploadMetadataIn
from apps.intake.api import router as intake_router
from apps.labels.api import router as labels_router
from apps.people.api import router as people_router
from apps.pipeline.api import router as pipeline_router
from apps.rules.api import router as rules_router
from apps.tasks.api import router as tasks_router
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
CONTRACT_ONLY_SCHEMAS: list[type[BaseModel]] = [UploadMetadataIn, FileMetadataIn]

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


# Up, and the database answers. Outside the request transaction (brievie/urls.py), so an
# unreachable database is a 503 with a sentence, never a 500.
@api.get("/health", response={200: HealthOut, 503: ErrorOut}, auth=None, operation_id="getHealth")
def get_health(request: HttpRequest) -> HealthOut:
    if not health.database_reachable():
        raise ApiError(
            503,
            "database_unavailable",
            _("De database is niet bereikbaar. Probeer het later opnieuw."),
        )
    return HealthOut(status="ok", api_version=api.version, server_version=server_version())


api.add_router("", accounts_router)
api.add_router("", workspaces_router)
# Before documents: its `/documents/{document_id}` would answer 405 to `/documents/files`.
api.add_router("", intake_router)
api.add_router("", documents_router)
api.add_router("", duplicates_router)
api.add_router("", people_router)
api.add_router("", labels_router)
api.add_router("", rules_router)
api.add_router("", tasks_router)
api.add_router("", pipeline_router)
api.add_router("", devices_router)
api.add_router("", audit_router)
api.add_router("", backup_router)
api.add_router("", access_router)
