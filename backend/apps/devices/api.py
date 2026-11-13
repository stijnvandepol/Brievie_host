from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status
from ninja.security import django_auth

from apps.accounts.schemas import UserOut
from apps.accounts.services import current_user
from apps.common.schemas import ErrorOut
from apps.devices import services
from apps.devices.models import Device
from apps.devices.schemas import DeviceOut, PairIn, PairingCodeOut, PairOut
from apps.devices.throttling import PairingThrottle

router = Router(tags=["devices"])


@router.post(
    "/devices/pairing-codes",
    response={201: PairingCodeOut, 401: ErrorOut, 403: ErrorOut},
    auth=django_auth,
    operation_id="createPairingCode",
)
def create_pairing_code(request: HttpRequest) -> Status[PairingCodeOut]:
    pairing_code, code = services.create_pairing_code(current_user(request))
    return Status(
        201,
        PairingCodeOut(
            code=code,
            expires_at=pairing_code.expires_at,
            qr_payload=services.qr_payload(code, ca_sha256=None),
        ),
    )


@router.post(
    "/devices/pair",
    response={201: PairOut, 400: ErrorOut, 422: ErrorOut, 429: ErrorOut},
    auth=None,
    throttle=PairingThrottle(),
    operation_id="pairDevice",
)
def pair_device(request: HttpRequest, payload: PairIn) -> Status[PairOut]:
    device, token = services.pair_device(code=payload.code, device_name=payload.device_name)
    user = UserOut.model_validate(device.user, from_attributes=True)
    return Status(201, PairOut(device_id=device.id, token=token, user=user))


@router.get(
    "/devices",
    response={200: list[DeviceOut], 401: ErrorOut},
    operation_id="listDevices",
)
def list_devices(request: HttpRequest) -> list[Device]:
    return services.list_devices(current_user(request))


@router.delete(
    "/devices/{device_id}",
    response={204: None, 401: ErrorOut, 403: ErrorOut, 404: ErrorOut},
    operation_id="revokeDevice",
)
def revoke_device(request: HttpRequest, device_id: UUID) -> Status[None]:
    services.revoke_device(current_user(request), device_id)
    return Status(204, None)
