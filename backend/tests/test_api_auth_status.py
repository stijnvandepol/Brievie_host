"""401 for anyone who is not authenticated, 403 only for a session without a CSRF token."""

from django.test import Client
from django.utils import timezone

from apps.devices.models import Device
from apps.devices.services import create_pairing_code, pair_device
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json


def csrf_client() -> Client:
    return Client(enforce_csrf_checks=True)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_bogus_bearer_on_post_is_401() -> None:
    r = post_json(csrf_client(), "/auth/logout", headers=bearer("niet-bestaand"))

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


def test_bogus_bearer_on_delete_is_401() -> None:
    r = csrf_client().delete(
        "/api/v1/devices/0b6f9a2e-8f5c-4f43-8a39-5a6b6d0c2222", headers=bearer("niet-bestaand")
    )

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


def test_revoked_bearer_on_post_is_401() -> None:
    user = UserFactory.create()
    _, code = create_pairing_code(user)
    device, token = pair_device(code=code, device_name="iPhone")
    device.revoked_at = timezone.now()
    device.save()

    r = post_json(csrf_client(), "/devices/pairing-codes", headers=bearer(token))

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


def test_revoked_bearer_does_not_fall_back_to_a_session() -> None:
    user = UserFactory.create()
    _, code = create_pairing_code(user)
    device, token = pair_device(code=code, device_name="iPhone")
    device.revoked_at = timezone.now()
    device.save()
    client = csrf_client()
    client.force_login(user)

    assert client.get("/api/v1/me", headers=bearer(token)).status_code == 401


def test_no_credentials_on_post_is_401() -> None:
    r = post_json(csrf_client(), "/auth/logout")

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


def test_no_credentials_on_delete_is_401() -> None:
    r = csrf_client().delete("/api/v1/devices/0b6f9a2e-8f5c-4f43-8a39-5a6b6d0c2222")

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


def test_session_without_csrf_token_is_403() -> None:
    client = csrf_client()
    client.force_login(UserFactory.create())

    r = post_json(client, "/auth/logout")

    # Its own code: the client only has to refresh, unlike a role that may not do this.
    assert r.status_code == 403
    assert r.json() == {
        "code": "csrf_failed",
        "message": "Je sessie is niet meer geldig voor deze pagina. Ververs de pagina en "
        "probeer het opnieuw.",
    }


def test_session_with_csrf_token_succeeds() -> None:
    user = UserFactory.create(email="kees@example.nl")
    client = csrf_client()
    login = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    token = login.cookies["csrftoken"].value

    assert post_json(client, "/auth/logout", headers={"X-CSRFToken": token}).status_code == 204


def test_session_delete_with_csrf_token_succeeds() -> None:
    user = UserFactory.create(email="kees@example.nl")
    device = Device.objects.create(user=user, name="A", token_hash="3" * 64)
    client = csrf_client()
    login = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    token = login.cookies["csrftoken"].value

    r = client.delete(f"/api/v1/devices/{device.id}", headers={"X-CSRFToken": token})

    assert r.status_code == 204


def test_pairing_code_needs_a_session_even_with_a_valid_bearer() -> None:
    user = UserFactory.create()
    _, code = create_pairing_code(user)
    _, token = pair_device(code=code, device_name="iPhone")

    r = post_json(csrf_client(), "/devices/pairing-codes", headers=bearer(token))

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"
