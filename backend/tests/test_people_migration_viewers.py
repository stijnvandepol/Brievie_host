"""Proof that the Mensen migrations change nobody's sight (spec 7; plan Tasks 1 and 11).

Seeded with the live models, `viewers()` of every letter is read in several switch states;
then `people` is migrated back to 0003 (before the account columns) and forward again, and
every answer must be the same each time. `viewers()` is raw SQL through the policy's own
condition, so it reads the same tables before and after the schema change.
"""

import itertools
from collections.abc import Iterator
from uuid import UUID

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.utils import timezone

from apps.documents import visibility
from apps.documents.models import Document
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.members_support import as_user
from tests.visibility_support import (
    as_actor,
    home,
    office,
    private_letter,
    set_state,
)

# (everyone sees all, departments on) in every combination; admins read all off.
STATES = list(itertools.product((False, True), repeat=2))

Seen = dict[tuple[UUID, bool, bool], dict[UUID, frozenset[UUID]]]


def _migrate(targets: list[tuple[str, str]]) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)


def _leaves() -> list[tuple[str, str]]:
    executor = MigrationExecutor(connection)
    leaves: list[tuple[str, str]] = executor.loader.graph.leaf_nodes()
    return leaves


@pytest.fixture
def migrated_forward_afterwards() -> Iterator[None]:
    """Whatever happens in the test, the database ends fully migrated for the next one."""
    yield
    _migrate(_leaves())


def _seed() -> list[Workspace]:
    """Both kinds of workspace: private letters with share and addressee readers, a person
    without an account, a removed member and a member who unlinked themselves."""
    o = office()
    h = home()
    private_letter(o.workspace, addressees=[o.persons["Piet"]], shared_with=[o.hr])
    private_letter(o.workspace, addressees=[o.persons["Jan"]], scanned_by=o.sorter)
    private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.bob)
    private_letter(h.workspace, addressees=[h.persons["Carla"]])
    # A removed member: their person is switched off.
    r = as_user(o.admin).delete(f"/api/v1/workspaces/{o.workspace.id}/members/{o.plain.id}")
    assert r.status_code == 204, r.content
    # A member who unlinked themselves.
    carla = Client()
    carla.force_login(h.carla)
    response = carla.put(
        f"/api/v1/workspaces/{h.workspace.id}/persons/{h.persons['Carla'].id}/member",
        {"user_id": None},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    # A person switched off before (the display columns filled).
    with as_actor(o.workspace, SYSTEM):
        Person.objects.filter(id=o.persons["Jan"].id).update(
            deactivated_at=timezone.now(), former_user=o.plain
        )
    return [o.workspace, h.workspace]


def _seen(workspaces: list[Workspace]) -> Seen:
    result: Seen = {}
    for workspace in workspaces:
        for e, d in STATES:
            set_state(workspace, e=e, d=d, a=False)
            with as_actor(workspace, SYSTEM):
                ids = list(Document.objects.values_list("id", flat=True))
                result[(workspace.id, e, d)] = visibility.viewers(ids)
    return result


@pytest.mark.django_db(transaction=True)
def test_migration_keeps_every_viewer(migrated_forward_afterwards: None) -> None:
    workspaces = _seed()
    with as_actor(workspaces[0], SYSTEM):
        assert Person.objects.filter(deactivated_at__isnull=False).exists()
    before = _seen(workspaces)
    assert any(viewers for answer in before.values() for viewers in answer.values())

    _migrate([("people", "0003_departments")])
    back = _seen(workspaces)
    _migrate(_leaves())
    after = _seen(workspaces)

    assert back == before
    assert after == before
