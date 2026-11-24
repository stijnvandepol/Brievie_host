"""The back-up panel's endpoints: server admins, web session, nothing secret in an answer."""

from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User
from apps.audit.models import ServerAuditEvent
from apps.backup.models import BackupRun
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json

STATUS = "/api/v1/server/backup-status"
RUNS = "/server/backup-runs"


def sign_in(user: User) -> Client:
    client = Client()
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    assert r.status_code == 200
    return client


def admin_client() -> Client:
    return sign_in(UserFactory.create(is_server_admin=True))


def test_the_status_of_a_server_without_a_destination() -> None:
    r = admin_client().get(STATUS)
    assert r.status_code == 200
    assert r.json() == {
        "destination": "none",
        "last_success_at": None,
        "last_success_size_bytes": None,
        "last_failure_at": None,
        "last_failure_reason": "",
        "last_check_at": None,
        "running": False,
        "late": False,
    }


def test_the_status_holds_no_path_address_or_credential(
    configured_folder: Path, settings: Settings
) -> None:
    settings.BRIEVIE_BACKUP_DIR_HOST = "/volume1/geheime-map"
    settings.BRIEVIE_BACKUP_PASSWORD = "geheim-back-upwachtwoord"
    BackupRun.objects.create(
        kind="backup",
        status=BackupRun.Status.SUCCEEDED,
        finished_at=timezone.now() - timedelta(hours=2),
        destination_kind="folder",
        size_bytes=1234,
    )
    r = admin_client().get(STATUS)
    body = r.content.decode()
    assert r.json()["destination"] == "folder"
    assert r.json()["last_success_size_bytes"] == 1234
    for secret in ("/volume1/geheime-map", "geheim-back-upwachtwoord", str(configured_folder)):
        assert secret not in body


def test_a_member_is_refused() -> None:
    client = sign_in(UserFactory.create())
    assert client.get(STATUS).status_code == 403
    assert post_json(client, RUNS).status_code == 403


def test_a_device_token_and_no_session_are_401() -> None:
    assert Client().get(STATUS).status_code == 401
    assert post_json(Client(), RUNS).status_code == 401
    user = UserFactory.create(is_server_admin=True)
    code = post_json(sign_in(user), "/devices/pairing-codes").json()["code"]
    token = post_json(Client(), "/devices/pair", {"code": code, "device_name": "A"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert Client().get(STATUS, headers=headers).status_code == 401
    assert post_json(Client(), RUNS, headers=headers).status_code == 401


def test_a_manual_back_up_is_accepted_then_refused_while_it_runs(configured_folder: Path) -> None:
    client = admin_client()
    first = post_json(client, RUNS)
    assert first.status_code == 202
    assert BackupRun.objects.get(id=first.json()["run_id"]).requested_by == "web"
    second = post_json(client, RUNS)
    assert (second.status_code, second.json()["code"]) == (409, "backup_running")
    assert client.get(STATUS).json()["running"] is True


def test_a_manual_back_up_without_a_destination_is_409() -> None:
    r = post_json(admin_client(), RUNS)
    assert (r.status_code, r.json()["code"]) == (409, "not_configured")


@pytest.mark.parametrize("path", ["/api/v1/server/backup-runs"])
def test_a_get_of_the_run_endpoint_is_not_allowed(path: str) -> None:
    assert admin_client().get(path).status_code == 405


def test_a_manual_back_up_needs_a_csrf_token(configured_folder: Path) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(UserFactory.create(is_server_admin=True))
    r = client.post("/api/v1/server/backup-runs", content_type="application/json")
    assert (r.status_code, r.json()["code"]) == (403, "csrf_failed")
    assert not BackupRun.objects.exists()


def test_a_manual_back_up_is_recorded_in_the_log(configured_folder: Path) -> None:
    user = UserFactory.create(is_server_admin=True)
    assert post_json(sign_in(user), RUNS).status_code == 202
    rows = list(ServerAuditEvent.objects.filter(code="backup_requested"))
    assert len(rows) == 1
    assert rows[0].actor_id == user.id
    assert rows[0].values == {}


def test_a_refused_manual_back_up_is_not_recorded() -> None:
    assert post_json(admin_client(), RUNS).status_code == 409
    assert not ServerAuditEvent.objects.filter(code="backup_requested").exists()
