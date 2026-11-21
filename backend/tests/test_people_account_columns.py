"""The two display columns of Mensen (spec 3.1, M4): when a person's account was switched
off, and which account it was. They never decide who sees what."""

import inspect

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.people.models import Person
from apps.workspaces import visibility_sql
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.factories import UserFactory


def test_new_columns_default_empty(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        person = Person.objects.create(workspace=ws_a, display_name="Oma")
        person.refresh_from_db()
    assert person.deactivated_at is None
    assert person.former_user_id is None


def test_department_cannot_be_deactivated(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        department = Person.objects.create(
            workspace=ws_a, display_name="Finance", kind=Person.Kind.DEPARTMENT
        )
        department.deactivated_at = timezone.now()
        with pytest.raises(ValueError, match="department"):
            department.save()


def test_former_user_needs_deactivated_at(ws_a: Workspace) -> None:
    former = UserFactory.create()
    with workspace_context(ws_a.id, actor=SYSTEM):
        person = Person.objects.create(workspace=ws_a, display_name="Oma")
        person.former_user = former
        with pytest.raises(ValueError, match="switched off"):
            person.save()
        # The database refuses it too, also past `save()`.
        with pytest.raises(IntegrityError), transaction.atomic():
            Person.objects.filter(id=person.id).update(former_user=former)


def test_deleting_user_keeps_person(ws_a: Workspace) -> None:
    former = UserFactory.create()
    with workspace_context(ws_a.id, actor=SYSTEM):
        person = Person.objects.create(
            workspace=ws_a,
            display_name="Oma",
            deactivated_at=timezone.now(),
            former_user=former,
        )
    # Django sets the column to null itself, so it must see the person (forced RLS).
    with workspace_context(ws_a.id, actor=SYSTEM):
        User.objects.filter(id=former.id).delete()
        person.refresh_from_db()
    assert person.former_user_id is None
    assert person.deactivated_at is not None


def test_visibility_sql_never_reads_former_user() -> None:
    """M4: `former_user` grants nothing. No string of the SQL rule names it."""
    source = inspect.getsource(visibility_sql)
    assert "former_user" not in source
    assert "deactivated_at" not in source
