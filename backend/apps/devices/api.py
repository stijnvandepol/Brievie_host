from uuid import UUID

from django.http import HttpRequest, HttpResponse
from ninja import Router, Status

from apps.access.services import server_addresses
from apps.accounts.auth import session_auth
from apps.accounts.schemas import UserOut
from apps.accounts.services import current_user
from apps.audit.services import actor_from_request
from apps.common.net import valid_client_ip
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.common.throttling import CallerThrottle, ClientAddressThrottle
from apps.devices import services
from apps.devices.models import Device, PairingChallenge
from apps.devices.schemas import (
    CaCertificateOut,
    DeviceOut,
    PairIn,
    PairingChallengeOut,
    PairingCodeOut,
    PairOut,
    PairProofIn,
)

router = Router(tags=["devices"])


@router.post(
    "/devices/pairing-codes",
    response={201: PairingCodeOut, 429: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    # Each code costs a slow key derivation (proof.py): ten per user per ten minutes.
    throttle=CallerThrottle("pairing_code", "10/10m"),
    description=(
        "Web session only. A bearer token (even a valid one) is not accepted here and "
        "answers 401 not_authenticated; a session without a valid CSRF token answers 403. "
        "A session that signed in more than 15 minutes ago answers 403 "
        "reauthentication_required: call reauthenticate first. "
        "The response carries the code once and is not stored (Cache-Control: no-store). "
        "server_link opens the app without the code: it pairs with a proof."
    ),
    operation_id="createPairingCode",
)
def create_pairing_code(
    request: HttpRequest, response: HttpResponse
) -> Status[services.IssuedPairingCode]:
    issued = services.issue_pairing_code(request)
    response["Cache-Control"] = "no-store"
    return Status(201, issued)


@router.post(
    "/devices/pair",
    response={201: PairOut, 429: ErrorOut, **BODY_ERRORS},
    auth=None,
    throttle=ClientAddressThrottle("pairing", "10/10m"),
    operation_id="pairDevice",
)
def pair_device(request: HttpRequest, response: HttpResponse, payload: PairIn) -> Status[PairOut]:
    device, token = services.pair_device(
        code=payload.code,
        device_name=payload.device_name,
        app_version=payload.app_version,
        client_ip=valid_client_ip(request),
    )
    # The device token is shown once.
    response["Cache-Control"] = "no-store"
    user = UserOut.model_validate(device.user, from_attributes=True)
    return Status(
        201,
        PairOut(
            device_id=device.id,
            token=token,
            user=user,
            public_urls=server_addresses().public_urls,
        ),
    )


@router.post(
    "/devices/pairing-challenges",
    response={201: PairingChallengeOut, 429: ErrorOut},
    auth=None,
    throttle=ClientAddressThrottle("pairing_challenge", "20/10m"),
    description=(
        "A one-time nonce (five minutes) for pairDeviceWithProof. Takes no input and "
        "reveals nothing about any pairing code."
    ),
    operation_id="createPairingChallenge",
)
def create_pairing_challenge(request: HttpRequest) -> Status[PairingChallenge]:
    return Status(201, services.create_pairing_challenge())


@router.post(
    "/devices/pair-with-proof",
    response={201: PairOut, 429: ErrorOut, **BODY_ERRORS},
    auth=None,
    # The scope of pairDevice: both share one count per client address.
    throttle=ClientAddressThrottle("pairing", "10/10m"),
    description=(
        "Pair from a brievie://server link without sending the code: proof = "
        "HMAC-SHA256(PBKDF2-HMAC-SHA256(code, 'brievie-pair-v1' + pairing_id, 600000), "
        "'brievie-pair-v1' + nonce + trust binding), the binding being the pinned CA's "
        "SHA-256 or the host name connected to. 400 pairing_challenge_invalid for an "
        "unknown, expired or used nonce; 400 pairing_code_invalid for a wrong proof or a "
        "code that cannot pair (five wrong proofs burn the code)."
    ),
    operation_id="pairDeviceWithProof",
)
def pair_device_with_proof(
    request: HttpRequest, response: HttpResponse, payload: PairProofIn
) -> Status[PairOut]:
    device, token = services.pair_device_with_proof(
        pairing_id=payload.pairing_id,
        nonce=payload.nonce,
        proof=payload.proof,
        device_name=payload.device_name,
        app_version=payload.app_version,
        client_ip=valid_client_ip(request),
    )
    response["Cache-Control"] = "no-store"
    user = UserOut.model_validate(device.user, from_attributes=True)
    return Status(
        201,
        PairOut(
            device_id=device.id,
            token=token,
            user=user,
            public_urls=server_addresses().public_urls,
        ),
    )


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
    services.revoke_device(current_user(request), device_id, actor=actor_from_request(request))
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
