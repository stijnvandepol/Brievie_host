from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.devices.models import Device
from apps.devices.services import create_pairing_code, pair_device
from apps.workspaces.models import Membership, Workspace
from tests.factories import UserFactory
from tests.helpers import post_json


def paired(user: User) -> tuple[Device, str]:
    _, code = create_pairing_code(user)
    return pair_device(code=code, device_name="iPhone")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_revoked_device_is_rejected_on_the_next_call() -> None:  # Review Focus 4
    device, token = paired(UserFactory.create())
    assert Client().get("/api/v1/me", headers=bearer(token)).status_code == 200

    device.revoked_at = timezone.now()
    device.save()
    r = Client().get("/api/v1/me", headers=bearer(token))

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


def test_unknown_token_is_401() -> None:
    r = Client().get("/api/v1/me", headers=bearer("niet-bestaand"))

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


def test_inactive_user_token_is_401() -> None:
    user = UserFactory.create()
    _, token = paired(user)
    user.is_active = False
    user.save()

    assert Client().get("/api/v1/me", headers=bearer(token)).status_code == 401


def test_last_seen_is_updated_at_most_once_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    device, token = paired(UserFactory.create())
    assert device.last_seen_at is None

    Client().get("/api/v1/me", headers=bearer(token))
    device.refresh_from_db()
    first = device.last_seen_at
    assert first is not None

    Client().get("/api/v1/me", headers=bearer(token))
    device.refresh_from_db()
    assert device.last_seen_at == first

    later = timezone.now() + timedelta(minutes=2)
    monkeypatch.setattr(timezone, "now", lambda: later)
    Client().get("/api/v1/me", headers=bearer(token))
    device.refresh_from_db()
    assert device.last_seen_at == later


def test_bearer_token_works_on_workspace_endpoints_without_csrf(ws_a: Workspace) -> None:
    admin = Membership.objects.get(workspace=ws_a).user
    _, token = paired(admin)
    client = Client(enforce_csrf_checks=True)

    r = client.post(
        f"/api/v1/workspaces/{ws_a.id}/folders",
        {"name": "Auto"},
        content_type="application/json",
        headers=bearer(token),
    )

    assert r.status_code == 201


def test_logout_needs_a_session() -> None:
    _, token = paired(UserFactory.create())

    assert post_json(Client(), "/auth/logout", headers=bearer(token)).status_code == 401
