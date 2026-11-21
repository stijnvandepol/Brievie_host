"""Removing a member and inviting one, with private post (plan Task 27, PB T13)."""

import pytest
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import User
from apps.documents.models import DocumentEvent, DocumentShare
from apps.people.models import DepartmentMember, Person
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from apps.workspaces.services import create_workspace
from tests.factories import TEST_PASSWORD
from tests.test_workspaces_accept_invitation import accept, invite
from tests.visibility_support import Home, as_actor, home, private_letter, readers, sees


@pytest.fixture(autouse=True)
def _public_url(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def _remove(h: Home, user_id: object) -> None:
    client = Client()
    client.force_login(h.anne)
    r = client.delete(f"/api/v1/workspaces/{h.workspace.id}/members/{user_id}")
    assert r.status_code in (200, 204), r.content


def _department_with(h: Home, *users: User) -> Person:
    with as_actor(h.workspace, SYSTEM):
        department = Person.objects.create(
            workspace=h.workspace, display_name="Ouders", kind=Person.Kind.DEPARTMENT
        )
        for user in users:
            DepartmentMember.objects.create(
                workspace=h.workspace,
                department=department,
                user=user,
            )
    return department


def test_removed_member_loses_reader_rows_shares_and_departments() -> None:
    h = home()
    department = _department_with(h, h.carla)
    on_name = private_letter(h.workspace, addressees=[h.persons["Carla"]])
    shared = private_letter(h.workspace, addressees=[h.persons["Bob"]], shared_with=[h.carla])
    of_department = private_letter(h.workspace, addressees=[department], scanned_by=h.bob)
    _remove(h, h.carla.id)
    for doc in (on_name, shared, of_department):
        assert all(name != "Carla" for name, _reason in readers(doc))
    with as_actor(h.workspace, SYSTEM):
        assert not DocumentShare.objects.filter(user=h.carla).exists()
        assert not DepartmentMember.objects.filter(user=h.carla).exists()


def test_private_post_of_removed_member_stays_locked() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Carla"]])
    _remove(h, h.carla.id)
    assert not sees(h.anne, doc)
    assert not sees(h.bob, doc)


def test_task_on_hidden_private_letter_is_unassigned_on_removal() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Carla"]])
    with as_actor(h.workspace, SYSTEM):
        Task.objects.filter(document=doc).update(assigned_to=h.carla)
    _remove(h, h.carla.id)
    with as_actor(h.workspace, SYSTEM):
        assert not Task.objects.filter(assigned_to=h.carla).exists()


def test_member_of_two_workspaces_keeps_readers_in_the_other() -> None:
    h = home()
    other = create_workspace(name="Werk", kind="business", admin=h.anne)
    Membership.objects.create(user=h.carla, workspace=other, role=Membership.Role.MEMBER)
    elsewhere = private_letter(other, shared_with=[h.carla], scanned_by=h.anne)
    here = private_letter(h.workspace, shared_with=[h.carla], scanned_by=h.bob)
    _remove(h, h.carla.id)
    assert ("Carla", "share") not in readers(here)
    assert ("Carla", "share") in readers(elsewhere)


def test_reinvited_with_this_is_person_reads_again() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Carla"]])
    _remove(h, h.carla.id)
    token = invite(h.workspace, person_id=str(h.persons["Carla"].id), email=h.carla.email)
    status, body = accept(
        Client(), token, account="existing", email=h.carla.email, password=TEST_PASSWORD
    )
    assert status in (200, 201), body
    assert readers(doc) == {("Carla", "addressee")}
    with as_actor(h.workspace, h.carla.id):
        (line,) = DocumentEvent.objects.filter(document=doc, kind="access_granted")
    assert line.actor_id == h.anne.id


def test_invitation_for_person_with_private_post_needs_email() -> None:
    h = home()
    private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.bob)
    client = Client()
    client.force_login(h.anne)
    r = client.post(
        f"/api/v1/workspaces/{h.workspace.id}/invitations",
        {"person_id": str(h.persons["Oma"].id)},
        content_type="application/json",
    )
    assert r.status_code == 422
    assert r.json()["code"] == "email_required"


def test_invitation_for_person_with_private_post_accepts_only_that_address() -> None:
    h = home()
    private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.bob)
    token = invite(h.workspace, person_id=str(h.persons["Oma"].id), email="oma@example.nl")
    status, body = accept(Client(), token, email="iemand@example.nl")
    assert status == 403
    assert body["code"] == "invitation_email_mismatch"
