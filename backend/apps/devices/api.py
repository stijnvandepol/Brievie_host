from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status
from ninja.security import django_auth

from apps.accounts.schemas import UserOut
from apps.accounts.services import current_user
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.common.throttling import ClientAddressThrottle
from apps.devices import services
from apps.devices.models import Device
from apps.devices.schemas import CaCertificateOut, DeviceOut, PairIn, PairingCodeOut, PairOut

router = Router(tags=["devices"])


@router.post(
    "/devices/pairing-codes",
    response={201: PairingCodeOut, **SESSION_WRITE_ERRORS},
    auth=django_auth,
    operation_id="createPairingCode",
)
def create_pairing_code(request: HttpRequest) -> Status[services.IssuedPairingCode]:
    return Status(201, services.issue_pairing_code(current_user(request)))


@router.post(
    "/devices/pair",
    response={201: PairOut, 429: ErrorOut, **BODY_ERRORS},
    auth=None,
    throttle=ClientAddressThrottle("pairing", "10/10m"),
    operation_id="pairDevice",
)
def pair_device(request: HttpRequest, payload: PairIn) -> Status[PairOut]:
    device, token = services.pair_device(code=payload.code, device_name=payload.device_name)
    user = UserOut.model_validate(device.user, from_attributes=True)
    return Status(201, PairOut(device_id=device.id, token=token, user=user))


@router.get(
    "/devices",
    response={200: list[DeviceOut], **AUTH_ERRORS},
    operation_id="listDevices",
)
def list_devices(request: HttpRequest) -> list[Device]:
    return services.list_devices(current_user(request))


@router.delete(
    "/devices/{device_id}",
    response={204: None, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="revokeDevice",
)
def revoke_device(request: HttpRequest, device_id: UUID) -> Status[None]:
    services.revoke_device(current_user(request), device_id)
    return Status(204, None)


@router.get(
    "/server/ca-certificate",
    response={200: CaCertificateOut, 404: ErrorOut},
    auth=None,
    tags=["server"],
    operation_id="getCaCertificate",
)
def get_ca_certificate(request: HttpRequest) -> CaCertificateOut:
    return CaCertificateOut(pem=services.ca_certificate_pem())
