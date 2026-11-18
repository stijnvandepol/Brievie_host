import importlib
import threading
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.db import DatabaseError, connection, connections, transaction
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.codes import AuditCode
from apps.audit.models import ServerAuditEvent
from apps.devices import services
from apps.devices.models import Device, TokenExpiryStart
from apps.devices.services import TOKEN_IDLE_LIMIT, create_pairing_code, pair_device, token_expired
from tests.factories import UserFactory

_MIGRATION = importlib.import_module("apps.devices.migrations.0004_tokenexpirystart")


def paired(user: User) -> tuple[Device, str]:
    _, code = create_pairing_code(user)
    return pair_device(code=code, device_name="iPhone")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def set_install_moment(days_ago: int) -> None:
    """The moment the update was installed, `days_ago` days back (one row)."""
    started = timezone.now() - timedelta(days=days_ago)
    TokenExpiryStart.objects.all().delete()
    TokenExpiryStart.objects.create(started_at=started)


def age(device: Device, *, last_seen_days: int | None, created_days: int) -> None:
    now = timezone.now()
    Device.objects.filter(pk=device.pk).update(
        last_seen_at=None if last_seen_days is None else now - timedelta(days=last_seen_days),
        created_at=now - timedelta(days=created_days),
    )


def audit_rows(code: AuditCode) -> int:
    return ServerAuditEvent.objects.filter(code=code.value).count()


@pytest.mark.parametrize(
    ("last_seen_days", "created_days", "baseline_days", "expired"),
    [
        (179, 400, 400, False),
        (180, 400, 400, True),
        (None, 181, 400, True),
        (None, 100, 400, False),
        # Used long ago, but the update was installed 10 days ago.
        (400, 400, 10, False),
    ],
)
def test_token_expired(
    last_seen_days: int | None, created_days: int, baseline_days: int, expired: bool
) -> None:
    now = timezone.now()
    device = Device(
        name="x",
        token_hash="h",
        last_seen_at=None if last_seen_days is None else now - timedelta(days=last_seen_days),
        created_at=now - timedelta(days=created_days),
    )

    assert token_expired(device, baseline=now - timedelta(days=baseline_days), now=now) is expired


def test_a_missing_baseline_counts_nothing() -> None:
    now = timezone.now()
    device = Device(name="x", token_hash="h", last_seen_at=now - TOKEN_IDLE_LIMIT, created_at=now)

    assert token_expired(device, baseline=None, now=now) is True


def test_expired_token_answers_exactly_like_a_revoked_one() -> None:
    set_install_moment(days_ago=400)
    user = UserFactory.create()
    revoked, revoked_token = paired(user)
    expired, expired_token = paired(user)
    Device.objects.filter(pk=revoked.pk).update(revoked_at=timezone.now())
    age(expired, last_seen_days=200, created_days=300)

    from_revoked = Client().get("/api/v1/workspaces", headers=bearer(revoked_token))
    from_expired = Client().get("/api/v1/workspaces", headers=bearer(expired_token))

    assert from_expired.status_code == from_revoked.status_code == 401
    assert from_expired.json()["code"] == "not_authenticated"
    assert from_expired.content == from_revoked.content

    def headers(response: object) -> dict[str, str]:
        pairs = response.headers.items()  # type: ignore[attr-defined]
        return {k: v for k, v in pairs if k.lower() != "date"}

    assert headers(from_expired) == headers(from_revoked)
    expired.refresh_from_db()
    assert expired.revoked_at is not None


def test_expiry_is_logged_once() -> None:
    set_install_moment(days_ago=400)
    device, token = paired(UserFactory.create())
    age(device, last_seen_days=181, created_days=300)

    first = Client().get("/api/v1/me", headers=bearer(token))
    second = Client().get("/api/v1/me", headers=bearer(token))

    assert first.status_code == second.status_code == 401
    assert audit_rows(AuditCode.DEVICE_EXPIRED) == 1
    row = ServerAuditEvent.objects.get(code=AuditCode.DEVICE_EXPIRED.value)
    assert row.target_id == device.id
    assert row.actor_kind == "system"
    device.refresh_from_db()
    assert device.revoked_at is not None


def test_a_second_detection_of_the_same_expiry_writes_nothing() -> None:
    set_install_moment(days_ago=400)
    device, _token = paired(UserFactory.create())
    age(device, last_seen_days=181, created_days=300)
    stale = Device.objects.get(pk=device.pk)  # loaded before either detection
    now = timezone.now()

    assert services.expire_device(stale, now=now) is True
    first_revoked_at = Device.objects.get(pk=device.pk).revoked_at
    assert services.expire_device(stale, now=now + timedelta(seconds=5)) is False

    assert Device.objects.get(pk=device.pk).revoked_at == first_revoked_at
    assert audit_rows(AuditCode.DEVICE_EXPIRED) == 1


def test_a_used_token_keeps_working() -> None:
    set_install_moment(days_ago=400)
    device, token = paired(UserFactory.create())
    age(device, last_seen_days=1, created_days=300)

    assert Client().get("/api/v1/me", headers=bearer(token)).status_code == 200
    assert audit_rows(AuditCode.DEVICE_EXPIRED) == 0


def test_a_phone_last_seen_a_year_ago_works_on_the_day_of_the_update() -> None:
    # The update was installed just now; the device was last used a year ago.
    set_install_moment(days_ago=0)
    device, token = paired(UserFactory.create())
    age(device, last_seen_days=365, created_days=500)

    assert Client().get("/api/v1/me", headers=bearer(token)).status_code == 200
    device.refresh_from_db()
    assert device.revoked_at is None


def test_a_never_used_device_counts_from_the_update_not_from_pairing() -> None:
    set_install_moment(days_ago=0)
    device, token = paired(UserFactory.create())
    age(device, last_seen_days=None, created_days=500)

    assert Client().get("/api/v1/me", headers=bearer(token)).status_code == 200


def test_it_stops_180_days_after_the_update_when_unused(monkeypatch: pytest.MonkeyPatch) -> None:
    set_install_moment(days_ago=0)
    device, token = paired(UserFactory.create())
    age(device, last_seen_days=365, created_days=500)
    later = timezone.now() + TOKEN_IDLE_LIMIT + timedelta(days=1)
    monkeypatch.setattr(timezone, "now", lambda: later)

    assert Client().get("/api/v1/me", headers=bearer(token)).status_code == 401


def test_expired_devices_are_not_listed() -> None:
    set_install_moment(days_ago=400)
    user = UserFactory.create()
    live, _ = paired(user)
    old, _ = paired(user)
    age(live, last_seen_days=2, created_days=300)
    age(old, last_seen_days=200, created_days=300)
    client = Client()
    client.force_login(user)

    r = client.get("/api/v1/devices")

    assert r.status_code == 200
    assert [d["id"] for d in r.json()] == [str(live.id)]


def test_migration_records_the_install_moment() -> None:
    TokenExpiryStart.objects.all().delete()

    _MIGRATION.record_install_moment(django_apps, None)

    rows = list(TokenExpiryStart.objects.all())
    assert len(rows) == 1
    assert abs(rows[0].started_at - timezone.now()) < timedelta(seconds=1)


@pytest.mark.django_db(transaction=True)
def test_two_connections_expire_the_same_token_once() -> None:
    set_install_moment(days_ago=400)
    device, _token = paired(UserFactory.create())
    age(device, last_seen_days=181, created_days=300)
    stale = Device.objects.get(pk=device.pk)
    outcome: list[bool] = []

    def other_connection() -> None:
        try:
            outcome.append(services.expire_device(stale, now=timezone.now()))
        finally:
            connections.close_all()

    with transaction.atomic():
        # This connection has expired it, but not committed: the other one waits on the row.
        assert services.expire_device(stale, now=timezone.now()) is True
        thread = threading.Thread(target=other_connection)
        thread.start()
        thread.join(timeout=1.0)
        assert thread.is_alive()
    thread.join(timeout=10)

    assert outcome == [False]
    assert audit_rows(AuditCode.DEVICE_EXPIRED) == 1
    connection.close()


def test_expiry_leaves_no_token_in_the_audit_row() -> None:
    set_install_moment(days_ago=400)
    device, token = paired(UserFactory.create())
    age(device, last_seen_days=181, created_days=300)

    Client().get("/api/v1/me", headers=bearer(token))

    row = ServerAuditEvent.objects.get(code=AuditCode.DEVICE_EXPIRED.value)
    text = f"{row.values} {row.target_name} {row.actor_name}"
    assert token not in text
    assert device.token_hash not in text


def test_a_failing_expiry_write_does_not_fail_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    set_install_moment(days_ago=400)
    device, token = paired(UserFactory.create())
    age(device, last_seen_days=181, created_days=300)

    def broken(*args: object, **kwargs: object) -> None:
        raise DatabaseError("boom")

    monkeypatch.setattr(services, "record", broken)
    r = Client().get("/api/v1/me", headers=bearer(token))

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"
    device.refresh_from_db()
    assert device.revoked_at is None  # the write rolled back; the next request tries again
