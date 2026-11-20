from urllib.parse import urlsplit
from uuid import UUID

from django.http import HttpRequest
from django.utils.translation import gettext as _
from ninja import Router

from apps.accounts.auth import session_auth
from apps.accounts.services import current_user
from apps.common.errors import ApiError
from apps.common.net import valid_client_ip
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.common.throttling import CallerThrottle
from apps.pipeline import settings_service
from apps.pipeline.schemas import (
    AiAutopilotIn,
    AiAutopilotOut,
    AiSettingsIn,
    AiSettingsPreviewIn,
    AiSettingsPreviewOut,
    AiStatusOut,
    AiSupplierOut,
)
from apps.pipeline.settings_service import (
    Actor,
    AiSettingsError,
    AutopilotError,
    ConsentRequiredError,
)
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["ai"])

_AI = "/workspaces/{workspace_id}/ai"
# The web interface or the app; the user is recorded beside it.
API = "api"


@router.get(
    _AI,
    response={200: AiStatusOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="getAiStatus",
    description=(
        "How the workspace's letters are read: `off`, `external` or `local`. For "
        "`external` also the supplier, the host the text goes to and since when "
        "(the moment of consent). An administrator also gets the model. No full "
        "address and no key."
    ),
)
def get_ai_status(request: HttpRequest, workspace_id: UUID) -> AiStatusOut:
    membership = workspace_member(request, workspace_id)
    status = settings_service.status(workspace_id)
    # The model is for the form of an administrator only.
    return AiStatusOut.from_status(
        status,
        with_model=membership.has_role(Membership.Role.ADMIN),
        autopilot=settings_service.autopilot(workspace_id),
    )


@router.get(
    f"{_AI}/suppliers",
    response={200: list[AiSupplierOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listAiSuppliers",
    description="The external AI services with a preset, and the country each one is in.",
)
def list_ai_suppliers(request: HttpRequest, workspace_id: UUID) -> list[AiSupplierOut]:
    workspace_member(request, workspace_id)
    return [
        AiSupplierOut(
            key=key,
            name=preset.name,
            country=str(preset.country),
            host=urlsplit(preset.base_url).hostname or "",
            default_model=preset.model,
        )
        for key, preset in settings_service.SUPPLIERS.items()
    ]


@router.post(
    f"{_AI}/preview",
    response={
        200: AiSettingsPreviewOut,
        404: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="previewAiSettings",
    description=(
        "Check AI settings without saving them. When saving them needs consent, "
        "`notice` is the text to show (the same `configure_ai` prints) and `address` "
        "is what to send back as `consent_address`. Administrators only."
    ),
)
def preview_ai_settings(
    request: HttpRequest, workspace_id: UUID, payload: AiSettingsPreviewIn
) -> AiSettingsPreviewOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    try:
        preview = settings_service.preview(
            workspace_id=workspace_id,
            workspace_name=membership.workspace.name,
            mode=payload.mode,
            supplier=payload.supplier,
            base_url=payload.base_url,
            model=payload.model,
        )
    except AiSettingsError as exc:
        raise _invalid(exc) from exc
    return AiSettingsPreviewOut(
        needs_consent=preview.needs_consent, notice=preview.notice, address=preview.address
    )


@router.put(
    _AI,
    response={
        200: AiStatusOut,
        404: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    throttle=CallerThrottle("ai_settings", "10/10m"),
    operation_id="updateAiSettings",
    description=(
        "Set how the workspace's letters are read. Administrators only. Switching to an "
        "external address without consent in force answers 409 `ai_consent_required` "
        "unless `consent_address` is exactly the address these settings lead to: first "
        "show the notice of `previewAiSettings`. Without `api_key` the stored key stays, "
        "at the same address only. The key is never returned."
    ),
)
def update_ai_settings(
    request: HttpRequest, workspace_id: UUID, payload: AiSettingsIn
) -> AiStatusOut:
    workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    try:
        settings_service.configure(
            workspace_id=workspace_id,
            mode=payload.mode,
            supplier=payload.supplier,
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key or None,
            actor=Actor(
                user_id=current_user(request).id, label=API, client_ip=valid_client_ip(request)
            ),
            consent_address=payload.consent_address,
        )
    except ConsentRequiredError as exc:
        raise ApiError(
            409,
            "ai_consent_required",
            _("Lees eerst wat er naar de AI-dienst gaat en geef akkoord."),
        ) from exc
    except AiSettingsError as exc:
        raise _invalid(exc) from exc
    return AiStatusOut.from_status(
        settings_service.status(workspace_id),
        with_model=True,
        autopilot=settings_service.autopilot(workspace_id),
    )


def _autopilot_out(workspace_id: UUID) -> AiAutopilotOut:
    state = settings_service.autopilot(workspace_id)
    return AiAutopilotOut(
        auto_organise=state.organise,
        auto_confirm=state.confirm,
        allowed=state.allowed,
        notice=settings_service.autopilot_notice(),
        notice_outdated=state.notice_outdated,
    )


@router.get(
    f"{_AI}/autopilot",
    response={200: AiAutopilotOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="getAiAutopilot",
    description=(
        "The AI autopilot of the workspace: whether letters are put under persons and "
        "labels (`auto_organise`) and confirmed (`auto_confirm`) without anybody checking "
        "first, whether the server `allowed` it, and the `notice` to agree to. Any member."
    ),
)
def get_ai_autopilot(request: HttpRequest, workspace_id: UUID) -> AiAutopilotOut:
    workspace_member(request, workspace_id)
    return _autopilot_out(workspace_id)


@router.put(
    f"{_AI}/autopilot",
    response={
        200: AiAutopilotOut,
        404: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    throttle=CallerThrottle("ai_autopilot", "10/10m"),
    operation_id="updateAiAutopilot",
    description=(
        "Switch the AI autopilot on or off. Web session only: a bearer token answers 401 "
        "not_authenticated. Administrators only. A switch that goes on needs "
        "`acknowledged` (409 `ai_autopilot_ack_required`), AI on for the workspace "
        "(409 `ai_autopilot_needs_ai`) and the server's permission (409 "
        "`ai_autopilot_forbidden`). Switching off always works. Switching AI off or "
        "agreeing to another AI address also switches both off. `acknowledged` with a "
        "switch on records that the current `notice` was agreed to (clears "
        "`notice_outdated`), also when no switch changes."
    ),
)
def update_ai_autopilot(
    request: HttpRequest, workspace_id: UUID, payload: AiAutopilotIn
) -> AiAutopilotOut:
    workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    try:
        settings_service.configure_autopilot(
            workspace_id=workspace_id,
            organise=payload.auto_organise,
            confirm=payload.auto_confirm,
            acknowledged=payload.acknowledged,
            actor=Actor(
                user_id=current_user(request).id, label=API, client_ip=valid_client_ip(request)
            ),
        )
    except AutopilotError as exc:
        messages = {
            "ack_required": _("Lees eerst wat de autopilot doet en geef akkoord."),
            "needs_ai": _("Zet eerst AI aan voor deze werkruimte."),
            "forbidden": _("De beheerder van deze server staat de autopilot niet toe."),
        }
        raise ApiError(
            409,
            f"ai_autopilot_{exc.reason}",
            messages.get(exc.reason, _("De autopilot kan nu niet worden aangezet.")),
        ) from exc
    return _autopilot_out(workspace_id)


def _invalid(error: AiSettingsError) -> ApiError:
    """The Dutch sentence for settings that cannot be saved."""
    messages = {
        "model_required": _("Vul de naam van het model in."),
        "model_invalid": _("De naam van het model is te lang."),
        "key_required": _("Vul de sleutel van de AI-dienst in."),
        "not_https": _("Het adres van een externe AI-dienst moet met https:// beginnen."),
        "not_local": _("Dit adres ligt niet op je eigen netwerk."),
        "address_invalid": _("Dit adres van de AI-dienst klopt niet."),
    }
    message = messages.get(error.reason, _("Deze AI-instelling klopt niet."))
    return ApiError(422, "ai_settings_invalid", message)
