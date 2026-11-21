"""The two helpers with "Beheerd door" (Mensen slice C, plan Task 11): a manager sees the
post of the person they manage, and that post no longer goes to the sorters. Set here
directly (the column); the service with its preview is tested in test_people_manager."""

import psycopg
import pytest
from django.db import IntegrityError, connection, transaction

from apps.backup.verify import privacy_problems
from apps.documents import visibility
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.visibility_sql import (
    managed_post_functions_reverse_sql,
    managed_post_functions_sql,
)
from tests.visibility_support import Office, as_actor, office, sees


def _manage(o: Office, person: str, manager_id: object) -> None:
    with as_actor(o.workspace, SYSTEM):
        Person.objects.filter(id=o.persons[person].id).update(managed_by_id=manager_id)


def test_manager_sees_managed_persons_post() -> None:
    o = office()  # E off: Jan's letter goes to the sorter Sandra
    letter = o.letters["person_without_account"]
    assert not sees(o.plain, letter)
    _manage(o, "Jan", o.plain.id)
    assert sees(o.plain, letter)
    with as_actor(o.workspace, SYSTEM):
        assert o.plain.id in visibility.viewers([letter.id])[letter.id]


def test_managed_person_no_longer_goes_to_sorters() -> None:
    o = office()
    letter = o.letters["person_without_account"]
    assert sees(o.sorter, letter)
    _manage(o, "Jan", o.plain.id)
    assert not sees(o.sorter, letter)
    # Everyone who sees all post still does.
    o.state(e=True, d=True, a=False)
    assert sees(o.sorter, letter)


def test_department_cannot_be_managed() -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        department = Person.objects.get(id=o.persons["Finance"].id)
        department.managed_by_id = o.plain.id
        with pytest.raises(ValueError, match="without an account"):
            department.save()


def test_managed_requires_no_account() -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        piet = Person.objects.get(id=o.persons["Piet"].id)
        piet.managed_by_id = o.admin.id
        with pytest.raises(ValueError, match="without an account"):
            piet.save()
        with pytest.raises(IntegrityError), transaction.atomic():
            Person.objects.filter(id=piet.id).update(managed_by_id=o.admin.id)


def test_a_manager_must_be_a_member() -> None:
    from tests.factories import UserFactory

    o = office()
    with as_actor(o.workspace, SYSTEM):
        jan = Person.objects.get(id=o.persons["Jan"].id)
        jan.managed_by_id = UserFactory.create().id
        with pytest.raises(ValueError, match="member"):
            jan.save()


def test_a_manager_of_a_person_with_an_account_gains_nothing() -> None:
    """The arm reads `user_id IS NULL`: a stale manager on a linked person (the constraint
    forbids it; belt and braces) gives nothing."""
    (my_ids, covered) = managed_post_functions_sql()
    assert "p.user_id IS NULL" in my_ids
    assert "p.kind = 'person'" in my_ids
    assert "p.user_id IS NULL" in covered


def test_the_reverse_restores_the_helpers_of_0007() -> None:
    for statement in managed_post_functions_reverse_sql():
        assert statement.startswith("CREATE OR REPLACE FUNCTION")
        assert "managed_by_id" not in statement


def test_backup_check_still_finds_helpers() -> None:
    connection.ensure_connection()
    raw = connection.connection
    assert isinstance(raw, psycopg.Connection)
    assert privacy_problems(raw) == []
