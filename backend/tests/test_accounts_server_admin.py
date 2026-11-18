import importlib
from datetime import timedelta
from types import SimpleNamespace

from django.apps import apps
from django.db import connection, migrations
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User
from apps.accounts.services import lock_active_server_admins, other_active_server_admin_exists
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory
from tests.helpers import SETUP_TOKEN, post_json

migration = importlib.import_module("apps.accounts.migrations.0004_first_server_admin")


def _run() -> None:
    migration.mark_first_server_admin(apps, SimpleNamespace(connection=connection))


def _age(workspace: Workspace, user: User, *, days_ago: int) -> None:
    """Make `user`'s membership in `workspace` that many days old."""
    Membership.objects.filter(workspace=workspace, user=user).update(
        created_at=timezone.now() - timedelta(days=days_ago)
    )


def _admins() -> set[str]:
    return set(User.objects.filter(is_server_admin=True).values_list("email", flat=True))


def test_migration_marks_the_first_admin_of_the_oldest_workspace() -> None:
    first = UserFactory.create(email="eerste@example.nl")
    second = UserFactory.create(email="tweede@example.nl")
    other = UserFactory.create(email="ander@example.nl")
    older = create_workspace(name="Oud", kind="home", admin=first)
    Membership.objects.create(user=second, workspace=older, role="admin")
    newer = create_workspace(name="Nieuw", kind="business", admin=other)
    Workspace.objects.filter(id=older.id).update(created_at=timezone.now() - timedelta(days=10))
    Workspace.objects.filter(id=newer.id).update(created_at=timezone.now() - timedelta(days=1))
    _age(older, first, days_ago=9)
    _age(older, second, days_ago=5)
    _age(newer, other, days_ago=20)

    _run()

    assert _admins() == {"eerste@example.nl"}


def test_migration_skips_members_that_are_not_admins() -> None:
    member = UserFactory.create(email="lid@example.nl")
    admin = UserFactory.create(email="beheer@example.nl")
    workspace = create_workspace(name="Thuis", kind="home", admin=admin)
    Membership.objects.create(user=member, workspace=workspace, role="member")
    _age(workspace, member, days_ago=5)
    _age(workspace, admin, days_ago=1)

    _run()

    assert _admins() == {"beheer@example.nl"}


def test_migration_without_a_workspace_takes_the_oldest_active_user() -> None:
    old_inactive = UserFactory.create(email="weg@example.nl", is_active=False)
    old = UserFactory.create(email="oud@example.nl")
    new = UserFactory.create(email="nieuw@example.nl")
    now = timezone.now()
    User.objects.filter(id=old_inactive.id).update(created_at=now - timedelta(days=9))
    User.objects.filter(id=old.id).update(created_at=now - timedelta(days=5))
    User.objects.filter(id=new.id).update(created_at=now - timedelta(days=1))

    _run()

    assert _admins() == {"oud@example.nl"}


def test_migration_changes_nothing_when_a_server_admin_exists() -> None:
    chosen = UserFactory.create(email="gekozen@example.nl", is_server_admin=True)
    admin = UserFactory.create(email="beheer@example.nl")
    create_workspace(name="Thuis", kind="home", admin=admin)

    _run()
    _run()

    assert _admins() == {chosen.email}


def test_migration_is_idempotent() -> None:
    admin = UserFactory.create(email="beheer@example.nl")
    create_workspace(name="Thuis", kind="home", admin=admin)

    _run()
    _run()

    assert _admins() == {"beheer@example.nl"}


def test_migration_on_an_empty_server_does_nothing() -> None:
    _run()

    assert User.objects.count() == 0


def test_migration_reverse_is_a_no_op() -> None:
    operation = migration.Migration.operations[0]
    assert operation.reverse_code is migrations.RunPython.noop


def test_setup_makes_the_first_user_server_admin(client: Client, settings: Settings) -> None:
    settings.BRIEVIE_SETUP_TOKEN = SETUP_TOKEN

    r = post_json(
        client,
        "/setup",
        {
            "setup_token": SETUP_TOKEN,
            "email": "beheer@example.nl",
            "password": "een-lang-wachtwoord-2026",
            "name": "Anna de Vries",
            "workspace_name": "Thuis",
            "workspace_kind": "home",
        },
    )

    assert r.status_code == 201
    assert r.json()["user"]["is_server_admin"] is True
    assert User.objects.get().is_server_admin is True


def test_me_reports_is_server_admin(client: Client, ws_a: Workspace) -> None:
    plain = ws_a.memberships.get().user
    client.force_login(plain)

    assert client.get("/api/v1/me").json()["is_server_admin"] is False

    User.objects.filter(id=plain.id).update(is_server_admin=True)
    assert client.get("/api/v1/me").json()["is_server_admin"] is True


def test_other_active_server_admin_exists_ignores_inactive_admins() -> None:
    me = UserFactory.create(is_server_admin=True)
    gone = UserFactory.create(is_server_admin=True, is_active=False)
    UserFactory.create()  # not a server admin

    assert other_active_server_admin_exists(me) is False

    User.objects.filter(id=gone.id).update(is_active=True)
    assert other_active_server_admin_exists(me) is True
    # The other way round: `gone` has `me` as its other admin.
    assert other_active_server_admin_exists(gone) is True


def test_other_active_server_admin_exists_does_not_count_the_user_itself() -> None:
    me = UserFactory.create(is_server_admin=True)

    assert other_active_server_admin_exists(me) is False


def test_server_admins_are_locked_in_id_order() -> None:
    UserFactory.create(is_server_admin=True)
    UserFactory.create(is_server_admin=True)

    with CaptureQueriesContext(connection) as queries:
        admins = lock_active_server_admins()

    sql = queries[-1]["sql"]
    # NO KEY: an insert that references a server admin (a sign-in, an upload, a notice)
    # takes KEY SHARE, which a plain FOR UPDATE would block; two of these locks still
    # exclude each other, so the "last server admin" checks still run one at a time.
    lock = "FOR NO KEY UPDATE"
    assert "ORDER BY" in sql
    assert sql.index("ORDER BY") < sql.index(lock)
    assert '"accounts_user"."id"' in sql[sql.index("ORDER BY") : sql.index(lock)]
    assert [u.id for u in admins] == sorted(u.id for u in admins)
