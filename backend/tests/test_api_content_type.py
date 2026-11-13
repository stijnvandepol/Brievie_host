"""Cross-site forms cannot reach the API: bodies must be JSON, sessions need CSRF."""

import json

import pytest
from axes.models import AccessAttempt
from django.core.cache import cache
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import User
from apps.devices.models import Device
from apps.documents.models import Folder
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import SETUP_TOKEN, post_json

BODIES = {
    "/setup": {
        "setup_token": SETUP_TOKEN,
        "email": "aanvaller@example.nl",
        "password": "een-lang-wachtwoord-2026",
        "name": "Aanvaller",
        "workspace_name": "Overname",
        "workspace_kind": "home",
    },
    "/auth/login": {"email": "jan@example.nl", "password": "fout-wachtwoord"},
    "/devices/pair": {"code": "AAAA-AAAA", "device_name": "Aanvaller"},
}


@pytest.fixture(autouse=True)
def _setup_token(settings: Settings) -> None:
    settings.BRIEVIE_SETUP_TOKEN = SETUP_TOKEN


def send_as(client: Client, path: str, content_type: str) -> tuple[int, dict[str, object]]:
    body = BODIES[path]
    if content_type == "text/plain":
        # What <form enctype="text/plain"> can produce: JSON-looking text.
        r = client.post(f"/api/v1{path}", json.dumps(body), content_type="text/plain")
    else:
        r = client.post(f"/api/v1{path}", body)  # form-encoded (multipart) by default
    return r.status_code, r.json()


@pytest.mark.parametrize("path", list(BODIES))
@pytest.mark.parametrize("content_type", ["text/plain", "multipart/form-data"])
def test_non_json_bodies_are_415_and_change_nothing(path: str, content_type: str) -> None:
    UserFactory.create(email="jan@example.nl")
    client = Client()

    for _ in range(12):
        status, body = send_as(client, path, content_type)
        assert status == 415
        assert body["code"] == "unsupported_media_type"
        assert set(body) == {"code", "message"}

    assert list(User.objects.values_list("email", flat=True)) == ["jan@example.nl"]
    assert Workspace.objects.count() == 0
    assert AccessAttempt.objects.count() == 0
    assert Device.objects.count() == 0
    assert cache.get("throttle_pairing_127.0.0.1") is None
    assert cache.get("throttle_setup_127.0.0.1") is None


def test_url_encoded_form_is_415() -> None:
    r = Client().post(
        "/api/v1/auth/login",
        "email=jan%40example.nl&password=x",
        content_type="application/x-www-form-urlencoded",
    )
    assert r.status_code == 415


def test_json_with_charset_is_accepted() -> None:
    UserFactory.create(email="jan@example.nl")
    r = Client().post(
        "/api/v1/auth/login",
        json.dumps({"email": "jan@example.nl", "password": TEST_PASSWORD}),
        content_type="application/json; charset=utf-8",
    )
    assert r.status_code == 200


def test_request_without_body_needs_no_content_type() -> None:
    client = Client()
    client.force_login(UserFactory.create())
    r = client.generic("POST", "/api/v1/auth/logout")
    assert r.status_code == 204


@pytest.fixture
def csrf_client(ws_a: Workspace) -> Client:
    client = Client(enforce_csrf_checks=True)
    client.force_login(Membership.objects.get(workspace=ws_a).user)
    return client


def test_session_create_folder_needs_csrf(csrf_client: Client, ws_a: Workspace) -> None:
    r = post_json(csrf_client, f"/workspaces/{ws_a.id}/folders", {"name": "Auto"})

    assert r.status_code == 403
    assert set(r.json()) == {"code", "message"}
    with workspace_context(ws_a.id):
        assert Folder.objects.count() == 0


def test_session_create_pairing_code_needs_csrf(csrf_client: Client) -> None:
    r = post_json(csrf_client, "/devices/pairing-codes")

    assert r.status_code == 403
    assert set(r.json()) == {"code", "message"}


def test_session_revoke_device_needs_csrf(csrf_client: Client, ws_a: Workspace) -> None:
    user = Membership.objects.get(workspace=ws_a).user
    device = Device.objects.create(user=user, name="A", token_hash="2" * 64)

    r = csrf_client.delete(f"/api/v1/devices/{device.id}")

    assert r.status_code == 403
    device.refresh_from_db()
    assert device.revoked_at is None


def test_session_logout_needs_csrf(csrf_client: Client) -> None:
    r = post_json(csrf_client, "/auth/logout")

    assert r.status_code == 403
    assert post_json(csrf_client, "/auth/logout").status_code == 403  # still logged in


def test_session_with_a_valid_csrf_token_works(csrf_client: Client) -> None:
    # Logging in rotates the CSRF token and sends it as a cookie.
    user = UserFactory.create(email="kees@example.nl")
    login = post_json(csrf_client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    token = login.cookies["csrftoken"].value

    r = post_json(csrf_client, "/devices/pairing-codes", headers={"X-CSRFToken": token})

    assert r.status_code == 201
