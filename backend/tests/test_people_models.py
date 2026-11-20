import importlib

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, connection, transaction

from apps.people.models import Person
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.factories import UserFactory

_migration = importlib.import_module("apps.people.migrations.0002_persons_for_members")


def test_person_is_invisible_from_another_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        Person.objects.create(workspace=ws_a, display_name="Oma")

    with workspace_context(ws_b.id, actor=SYSTEM):
        assert not Person.objects.filter(display_name="Oma").exists()
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Person.objects.filter(display_name="Oma").exists()


def test_display_name_unique_ignoring_case_in_the_database(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        Person.objects.create(workspace=ws_a, display_name="Papa")
        with pytest.raises(IntegrityError), transaction.atomic():
            Person.objects.create(workspace=ws_a, display_name="papa")


def test_linked_user_must_be_a_member(ws_a: Workspace) -> None:
    outsider = UserFactory.create()

    with workspace_context(ws_a.id, actor=SYSTEM), pytest.raises(ValueError, match="member"):
        Person.objects.create(workspace=ws_a, display_name="Buurman", user=outsider)


def test_migration_created_persons_for_existing_members() -> None:
    # A workspace made without create_workspace, so without the hook.
    workspace = Workspace.objects.create(name="Oud", kind="home")
    admin = UserFactory.create(name="Anna de Boer")
    member = UserFactory.create(name="Bram")
    Membership.objects.create(user=admin, workspace=workspace, role="admin")
    Membership.objects.create(user=member, workspace=workspace, role="member")
    with workspace_context(workspace.id, actor=SYSTEM):
        Person.objects.create(workspace=workspace, display_name="bram")

    with connection.schema_editor() as editor:
        _migration.create_persons_for_members(django_apps, editor)
        # Running it twice changes nothing.
        _migration.create_persons_for_members(django_apps, editor)

    with workspace_context(workspace.id, actor=SYSTEM):
        linked = {p.user_id: p for p in Person.objects.exclude(user=None)}
        assert set(linked) == {admin.id, member.id}
        assert linked[admin.id].display_name == "Anna de Boer"
        assert linked[admin.id].name_variants == ["Anna de Boer"]
        assert linked[member.id].display_name == "Bram (2)"
    with connection.cursor() as c:
        c.execute("SELECT current_setting('brievie.workspace_id', true)")
        row = c.fetchone()
    assert row is not None
    assert row[0] in (None, "")
