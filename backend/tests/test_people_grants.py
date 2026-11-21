"""Linking a person is a grant (plan Task 27, PB T12, D 8.1)."""

from typing import TYPE_CHECKING

from django.test import Client

from apps.audit.models import WorkspaceAuditEvent
from apps.documents.models import DocumentEvent
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.factories import UserFactory
from tests.visibility_support import Home, as_actor, home, private_letter, readers, sees

if TYPE_CHECKING:
    from tests.helpers import Response

JSON = "application/json"


def _link(client: Client, h: Home, person: Person, user_id: object) -> "Response":
    return client.put(
        f"/api/v1/workspaces/{h.workspace.id}/persons/{person.id}/member",
        {"user_id": None if user_id is None else str(user_id)},
        content_type=JSON,
    )


def _status(r: "Response") -> tuple[int, str]:
    body = r.json()
    return r.status_code, str(body.get("code", "")) if isinstance(body, dict) else ""


def test_admin_cannot_link_self_to_person_with_private_post(client: Client) -> None:
    h = home()
    with as_actor(h.workspace, SYSTEM):
        Person.objects.filter(id=h.persons["Anne"].id).update(user=None)
    doc = private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.bob)
    client.force_login(h.anne)
    assert _status(_link(client, h, h.persons["Oma"], h.anne.id)) == (409, "cannot_grant_self")
    assert ("Anne", "addressee") not in readers(doc)


def test_admin_links_another_member_and_they_become_reader(client: Client) -> None:
    h = home()
    newcomer = UserFactory.create(name="Opa")
    Membership.objects.create(user=newcomer, workspace=h.workspace, role=Membership.Role.MEMBER)
    doc = private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.bob)
    client.force_login(h.anne)
    assert _status(_link(client, h, h.persons["Oma"], newcomer.id))[0] == 200
    assert ("Opa", "addressee") in readers(doc)
    assert sees(newcomer, doc)
    with as_actor(h.workspace, h.bob.id):
        (line,) = DocumentEvent.objects.filter(document=doc, kind="access_granted")
    assert (line.note, line.actor_id) == ("Opa", h.anne.id)


def test_admin_cannot_unlink_another_member(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    assert _status(_link(client, h, h.persons["Bob"], None)) == (409, "cannot_unlink")
    with as_actor(h.workspace, SYSTEM):
        assert Person.objects.get(id=h.persons["Bob"].id).user_id == h.bob.id


def test_member_unlinks_self_and_loses_the_letters(client: Client) -> None:
    h = home()
    Membership.objects.filter(user=h.bob).update(role=Membership.Role.VIEWER)
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    client.force_login(h.bob)
    assert _status(_link(client, h, h.persons["Bob"], None))[0] == 200
    assert readers(doc) == set()
    assert not sees(h.bob, doc)


def test_relinking_to_another_account_moves_the_readers(client: Client) -> None:
    h = home()
    with as_actor(h.workspace, SYSTEM):
        Person.objects.filter(id=h.persons["Carla"].id).update(user=None)
        Person.objects.filter(id=h.persons["Anne"].id).update(user=None)
        Person.objects.filter(id=h.persons["Oma"].id).update(user=h.anne)
    doc = private_letter(h.workspace, addressees=[h.persons["Oma"]])
    assert readers(doc) == {("Anne", "addressee")}
    client.force_login(h.anne)
    assert _status(_link(client, h, h.persons["Oma"], h.carla.id))[0] == 200
    assert readers(doc) == {("Carla", "addressee")}


def test_viewer_cannot_link(client: Client) -> None:
    h = home()
    Membership.objects.filter(user=h.bob).update(role=Membership.Role.VIEWER)
    newcomer = UserFactory.create()
    Membership.objects.create(user=newcomer, workspace=h.workspace, role=Membership.Role.MEMBER)
    client.force_login(h.bob)
    assert _status(_link(client, h, h.persons["Oma"], newcomer.id)) == (403, "forbidden")


def test_link_writes_one_audit_row(client: Client) -> None:
    h = home()
    newcomer = UserFactory.create()
    Membership.objects.create(user=newcomer, workspace=h.workspace, role=Membership.Role.MEMBER)
    private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.bob)
    client.force_login(h.anne)
    _link(client, h, h.persons["Oma"], newcomer.id)
    with as_actor(h.workspace, SYSTEM):
        assert WorkspaceAuditEvent.objects.filter(code="person_linked").count() == 1
