"""The version a database belongs to (server-level, one row)."""

import importlib
from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.updates import services
from apps.updates.models import InstalledVersion, ServerUpdate
from apps.updates.release import ReleaseError, Version

APP_ROLE = "brievie_app"
TABLE = "updates_installedversion"

pytestmark = pytest.mark.django_db


def v(text: str) -> Version:
    return Version.parse(text)


def has_privilege(privilege: str) -> bool:
    with connection.cursor() as c:
        c.execute("SELECT has_table_privilege(%s, %s, %s)", [APP_ROLE, TABLE, privilege])
        row = c.fetchone()
    assert row is not None
    return bool(row[0])


def test_nothing_recorded_gives_none() -> None:
    assert services.installed_version() is None


def test_falls_back_to_the_newest_finished_update() -> None:
    ServerUpdate.objects.create(
        from_version="0.2.0", to_version="0.3.0", finished_at=timezone.now()
    )
    ServerUpdate.objects.create(
        from_version="0.3.0",
        to_version="0.4.0",
        finished_at=timezone.now() + timedelta(minutes=1),
    )
    assert services.installed_version() == v("0.4.0")


def test_the_row_wins_over_the_update_history() -> None:
    ServerUpdate.objects.create(
        from_version="0.2.0", to_version="0.9.0", finished_at=timezone.now()
    )
    services.set_installed_version(v("0.4.0"))
    assert services.installed_version() == v("0.4.0")


def test_set_is_an_upsert_and_keeps_one_row() -> None:
    services.set_installed_version(v("0.4.0"))
    services.set_installed_version(v("0.4.0"))
    services.set_installed_version(v("0.5.0"))
    assert list(InstalledVersion.objects.values_list("version", flat=True)) == ["0.5.0"]


def test_a_lower_version_is_refused() -> None:
    services.set_installed_version(v("0.5.0"))
    with pytest.raises(ReleaseError) as caught:
        services.set_installed_version(v("0.4.9"))
    assert caught.value.code == "downgrade"
    assert services.installed_version() == v("0.5.0")


def test_a_second_row_is_impossible() -> None:
    InstalledVersion.objects.create(version="0.4.0")
    with pytest.raises(IntegrityError), transaction.atomic():
        InstalledVersion.objects.create(version="0.5.0")


def test_the_singleton_flag_cannot_be_false() -> None:
    with pytest.raises(IntegrityError), transaction.atomic():
        InstalledVersion.objects.create(version="0.4.0", singleton=False)


def test_app_role_may_read_add_and_change_but_not_delete() -> None:
    assert has_privilege("SELECT")
    assert has_privilege("INSERT")
    assert has_privilege("UPDATE")
    assert not has_privilege("DELETE")
    assert not has_privilege("TRUNCATE")


def test_the_migration_revokes_what_default_privileges_grant() -> None:
    migration = importlib.import_module("apps.updates.migrations.0002_installedversion")
    with connection.cursor() as c:
        c.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON {TABLE} TO {APP_ROLE}")
        assert has_privilege("DELETE")
        c.execute(migration.LIMIT_APP_ROLE)
    assert not has_privilege("DELETE")
    assert not has_privilege("TRUNCATE")
