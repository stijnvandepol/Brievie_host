"""The Scanmap page (intake spec S 3.1, 3.2a): workspace admins, web session only (a device
token answers 401; closed on the public route)."""

from uuid import UUID

from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _
from ninja import Router

from apps.accounts.auth import session_auth
from apps.audit.services import actor_from_request
from apps.common.errors import ApiError
from apps.common.schemas import BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.intake import scan_settings
from apps.intake.scan_settings import ScanFolderError
from apps.intake.schemas import ScanFolderIn, ScanFolderOut, ScanSubfolderIn
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["scan-folder"])

_PATH = "/workspaces/{workspace_id}/scan-folder"
_ONLY = " Web session only: a bearer token answers 401 not_authenticated. Administrators only."


def _error(error: ScanFolderError) -> ApiError:
    match error.reason:
        case "unavailable":
            return ApiError(
                409,
                "scan_folder_unavailable",
                _(
                    "Deze server heeft geen scanmap. "
                    "Vraag de serverbeheerder om er een in te stellen."
                ),
            )
        case "taken":
            return ApiError(
                409,
                "scan_folder_taken",
                _("Een andere werkruimte gebruikt deze mapnaam al. Kies een andere naam."),
            )
        case "invalid_name":
            return ApiError(
                422,
                "validation_error",
                _("Deze mapnaam kan niet. Gebruik kleine letters, cijfers en streepjes."),
            )
        case "person_not_found":
            return ApiError(404, "person_not_found", _("We kunnen deze persoon niet vinden."))
        case "folder_not_found":
            return ApiError(404, "scan_folder_not_found", _("We kunnen deze map niet vinden."))
        case "subfolder_taken":
            return ApiError(
                409,
                "scan_subfolder_taken",
                _("Een andere persoon of afdeling heeft al een map met deze naam."),
            )
        case _:
            return ApiError(
                409,
                "scan_folder_departments_off",
                _("Afdelingen staan uit. Zet ze eerst aan om een map voor een afdeling te maken."),
            )


def _status(workspace_id: UUID) -> ScanFolderOut:
    return scan_settings.scan_folder_status(workspace_id, now=timezone.now())


@router.get(
    _PATH,
    response={200: ScanFolderOut, 403: ErrorOut, 404: ErrorOut, 401: ErrorOut},
    auth=session_auth,
    operation_id="getScanFolder",
    description="The workspace's scan folder: whether the server has one, the switch, the"
    " folder's name, the intake service's check, what waits and what failed, and the folders"
    " per person or department." + _ONLY,
)
def get_scan_folder(request: HttpRequest, workspace_id: UUID) -> ScanFolderOut:
    workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    return _status(workspace_id)


@router.put(
    _PATH,
    response={
        200: ScanFolderOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    operation_id="updateScanFolder",
    description="Switch the scan folder and its folders per person on or off and name the"
    " workspace's folder (empty: keep the name, or the suggested one). 409"
    " `scan_folder_unavailable` when the server has no scan folder, `scan_folder_taken` when"
    " another workspace uses the name; 422 `validation_error` for a name that is not lower"
    " case letters, digits and `-`." + _ONLY,
)
def update_scan_folder(
    request: HttpRequest, workspace_id: UUID, payload: ScanFolderIn
) -> ScanFolderOut:
    workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    try:
        scan_settings.configure_scan_folder(
            workspace_id=workspace_id,
            enabled=payload.enabled,
            folder_name=payload.folder_name,
            per_person=payload.per_person,
            actor=actor_from_request(request),
        )
    except ScanFolderError as error:
        raise _error(error) from None
    return _status(workspace_id)


@router.post(
    f"{_PATH}/folders",
    response={
        200: ScanFolderOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    operation_id="addScanFolder",
    description='A folder for a person or department ("Map maken"), also one without an'
    ' account; with `name` an unknown folder of that name becomes theirs ("Koppel aan").'
    " A stopped folder is read again. 404 `person_not_found`; 409 `scan_subfolder_taken`"
    " when another has that name (case ignored), `scan_folder_departments_off` for a"
    " department while departments are off." + _ONLY,
)
def add_scan_folder(
    request: HttpRequest, workspace_id: UUID, payload: ScanSubfolderIn
) -> ScanFolderOut:
    workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    try:
        scan_settings.add_scan_folder(
            workspace_id=workspace_id,
            person_id=payload.person_id,
            name=payload.name,
            actor=actor_from_request(request),
        )
    except ScanFolderError as error:
        raise _error(error) from None
    return _status(workspace_id)


@router.delete(
    f"{_PATH}/folders/{{folder_id}}",
    response={200: ScanFolderOut, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    operation_id="removeScanFolder",
    description="Stop reading a person's or department's folder (\"Niet meer lezen\"). The"
    " folder and its files stay on the share; it is then listed as an unknown folder."
    " 404 `scan_folder_not_found`." + _ONLY,
)
def remove_scan_folder(request: HttpRequest, workspace_id: UUID, folder_id: UUID) -> ScanFolderOut:
    workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    try:
        scan_settings.remove_scan_folder(
            workspace_id=workspace_id, folder_id=folder_id, actor=actor_from_request(request)
        )
    except ScanFolderError as error:
        raise _error(error) from None
    return _status(workspace_id)
