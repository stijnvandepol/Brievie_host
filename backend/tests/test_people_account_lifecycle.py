"""The account state moves with the existing writers (spec 3.3; plan Task 2): removing or
leaving switches a person off, accepting an invitation for them clears it, archiving
withdraws their invitation, and every member always keeps exactly one person (M8)."""

import re
import uuid

import pytest
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import AccountNotice, User
from apps.audit.models import WorkspaceAuditEvent
from apps.documents import visibility
from apps.documents.models import Document
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Invitation, Membership, Workspace
from tests.factories import TEST_PASSWORD
from tests.helpers import post_json
from tests.members_support import as_user
from tests.visibility_support import (
    Office,
    as_actor,
    home,
    office,
    private_letter,
    readers,
)

LINK = re.compile(r"#(?P<token>[A-Za-z0-9_-]{43})$")


@pytest.fixture(autouse=True)
def _fast_hashing_and_public_url(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def _person(workspace: Workspace, person_id: uuid.UUID) -> Person:
    with as_actor(workspace, SYSTEM):
        return Person.objects.get(id=person_id)


def _remove(admin: User, workspace: Workspace, user: User) -> None:
    r = as_user(admin).delete(f"/api/v1/workspaces/{workspace.id}/members/{user.id}")
    assert r.status_code == 204, r.content


def _invite(
    client: Client, workspace: Workspace, person_id: uuid.UUID, **body: object
) -> "tuple[int, dict[str, object]]":
    r = post_json(
        client,
        f"/workspaces/{workspace.id}/invitations",
        {"person_id": str(person_id), **body},
    )
    return r.status_code, r.json()


def _preview_token(client: Client, workspace: Workspace, person_id: uuid.UUID) -> str:
    r = post_json(
        client,
        f"/workspaces/{workspace.id}/visibility/preview",
        {"member_change": "invite", "member_role": "member", "member_person_id": str(person_id)},
    )
    assert r.status_code == 200, r.json()
    return str(r.json()["preview_token"])


def _token(body: dict[str, object]) -> str:
    match = LINK.search(str(body["link"]))
    assert match is not None
    return match["token"]


def _accept(token: str, *, account: str, email: str, name: str = "") -> None:
    r = post_json(
        Client(),
        "/invitations/accept",
        {
            "token": token,
            "account": account,
            "name": name,
            "email": email,
            "password": TEST_PASSWORD,
        },
    )
    assert r.status_code == 200, r.json()


def _viewers(document: Document) -> frozenset[uuid.UUID]:
    with as_actor(document.workspace, SYSTEM):
        return visibility.viewers([document.id])[document.id]


def test_remove_member_marks_person_switched_off() -> None:
    o = office()
    _remove(o.admin, o.workspace, o.plain)
    person = _person(o.workspace, o.persons["Piet"].id)
    assert person.user_id is None
    assert person.deactivated_at is not None
    assert person.former_user_id == o.plain.id


def test_leave_marks_person_switched_off() -> None:
    h = home()
    r = post_json(as_user(h.bob), f"/workspaces/{h.workspace.id}/leave")
    assert r.status_code == 204, r.content
    person = _person(h.workspace, h.persons["Bob"].id)
    assert (person.user_id, person.former_user_id) == (None, h.bob.id)
    assert person.deactivated_at is not None


def test_accept_for_switched_off_person_clears_mark() -> None:
    h = home()
    _remove(h.anne, h.workspace, h.bob)
    status, body = _invite(as_user(h.anne), h.workspace, h.persons["Bob"].id)
    assert status == 201, body
    _accept(_token(body), account="existing", email=h.bob.email)
    person = _person(h.workspace, h.persons["Bob"].id)
    assert person.user_id == h.bob.id
    assert person.deactivated_at is None
    assert person.former_user_id is None


def test_reinvite_switched_off_person_restores_sight() -> None:
    o = office()  # E off: Piet's own post goes to the sorter once he is gone
    own = o.letters["own_piet"]
    private = private_letter(o.workspace, addressees=[o.persons["Piet"]])
    assert o.plain.id in _viewers(own)
    _remove(o.admin, o.workspace, o.plain)
    assert o.plain.id not in _viewers(own)
    assert o.sorter.id in _viewers(own)
    assert readers(private) == set()

    admin = as_user(o.admin)
    piet = o.persons["Piet"].id
    preview = _preview_token(admin, o.workspace, piet)
    # Private post on the name: only the bound address may accept.
    status, body = _invite(admin, o.workspace, piet, confirmed=True, preview_token=preview)
    assert (status, body["code"]) == (422, "email_required")
    status, body = _invite(
        admin, o.workspace, piet, email=o.plain.email, confirmed=True, preview_token=preview
    )
    assert status == 201, body
    _accept(_token(body), account="existing", email=o.plain.email)

    assert o.plain.id in _viewers(own)
    assert o.sorter.id not in _viewers(own)
    assert readers(private) == {("Piet", "addressee")}
    person = _person(o.workspace, piet)
    assert (person.user_id, person.deactivated_at, person.former_user_id) == (
        o.plain.id,
        None,
        None,
    )


def test_accept_by_other_account_still_allowed() -> None:
    """M4: the invitation decides who becomes the person, never `former_user`."""
    h = home()
    _remove(h.anne, h.workspace, h.bob)
    status, body = _invite(as_user(h.anne), h.workspace, h.persons["Bob"].id)
    assert status == 201, body
    _accept(_token(body), account="new", email="nina@example.nl", name="Nina")
    nina = User.objects.get(email="nina@example.nl")
    person = _person(h.workspace, h.persons["Bob"].id)
    assert (person.user_id, person.deactivated_at, person.former_user_id) == (nina.id, None, None)


def test_create_person_records_person_created(client: Client) -> None:
    h = home()
    client.force_login(h.bob)
    r = post_json(client, f"/workspaces/{h.workspace.id}/persons", {"display_name": "Opa"})
    assert r.status_code == 201, r.json()
    with as_actor(h.workspace, SYSTEM):
        (row,) = WorkspaceAuditEvent.objects.filter(code="person_created")
    assert (row.target_kind, row.target_name, row.values) == ("person", "Opa", {})
    assert (row.actor_id, row.target_id) == (h.bob.id, uuid.UUID(r.json()["id"]))


def test_archiving_revokes_open_invitation(client: Client) -> None:
    h = home()
    admin = as_user(h.anne)
    status, body = _invite(admin, h.workspace, h.persons["Oma"].id)
    assert status == 201, body
    token = _token(body)
    client.force_login(h.bob)
    r = client.put(
        f"/api/v1/workspaces/{h.workspace.id}/persons/{h.persons['Oma'].id}",
        {"display_name": "Oma", "archived": True},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    invitation = Invitation.objects.get(person_id=h.persons["Oma"].id)
    assert invitation.revoked_at is not None
    with as_actor(h.workspace, SYSTEM):
        (row,) = WorkspaceAuditEvent.objects.filter(code="invitation_revoked")
    assert row.values == {"reason": "person_archived"}
    # The old link opens nothing.
    r = post_json(Client(), "/invitations/lookup", {"token": token})
    assert r.json()["code"] == "invitation_invalid"


def test_restoring_or_renaming_revokes_nothing(client: Client) -> None:
    h = home()
    status, body = _invite(as_user(h.anne), h.workspace, h.persons["Oma"].id)
    assert status == 201, body
    client.force_login(h.bob)
    r = client.put(
        f"/api/v1/workspaces/{h.workspace.id}/persons/{h.persons['Oma'].id}",
        {"display_name": "Oma de Vries"},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    assert Invitation.objects.get(person_id=h.persons["Oma"].id).revoked_at is None


def test_sight_unchanged_by_marking() -> None:
    """Switching off moves Piet's post to the sorter (P7), exactly as before the mark."""
    o = office()
    names = {d.id: n for n, d in o.letters.items()}
    before = {names[d]: v for d, v in _viewers_all(o).items()}
    _remove(o.admin, o.workspace, o.plain)
    after = {names[d]: v for d, v in _viewers_all(o).items()}
    expected = {name: viewers - {o.plain.id} for name, viewers in before.items()}
    expected["own_piet"] = expected["own_piet"] | {o.sorter.id}
    assert after == expected


def _viewers_all(o: Office) -> dict[uuid.UUID, frozenset[uuid.UUID]]:
    with as_actor(o.workspace, SYSTEM):
        return visibility.viewers([d.id for d in o.letters.values()])


def _linked_persons(workspace: Workspace) -> dict[uuid.UUID, int]:
    with as_actor(workspace, SYSTEM):
        counts: dict[uuid.UUID, int] = {}
        for user_id in Person.objects.filter(workspace=workspace, user__isnull=False).values_list(
            "user_id", flat=True
        ):
            if user_id is not None:
                counts[user_id] = counts.get(user_id, 0) + 1
    return counts


def _assert_one_person_per_member(workspace: Workspace) -> None:
    members = set(Membership.objects.filter(workspace=workspace).values_list("user_id", flat=True))
    linked = _linked_persons(workspace)
    assert set(linked) == members
    assert set(linked.values()) == {1}


def test_every_member_always_has_a_person(client: Client) -> None:
    """Piet unlinks himself (E off): he gets a fresh person at once, his old one is "niet
    actief" (not switched off), and nobody's sight changes by the fresh one: no
    confirmation is asked and Piet is told of no gain."""
    o = office()
    before = _viewers_all(o)
    client.force_login(o.plain)
    r = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/persons/{o.persons['Piet'].id}/member",
        {"user_id": None},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    old = _person(o.workspace, o.persons["Piet"].id)
    assert (old.user_id, old.deactivated_at, old.former_user_id) == (None, None, None)
    with as_actor(o.workspace, SYSTEM):
        fresh = Person.objects.get(workspace=o.workspace, user=o.plain)
        assert fresh.id != old.id
        assert not Document.objects.filter(addressee_ids__contains=[fresh.id]).exists()
    # As before M8: Piet's letter goes to the sorter.
    after = _viewers_all(o)
    own = o.letters["own_piet"].id
    assert after[own] == (before[own] - {o.plain.id}) | {o.sorter.id}
    assert not AccountNotice.objects.filter(user=o.plain, code="sight_widened").exists()
    _assert_one_person_per_member(o.workspace)


def test_unlink_person_count_invariant(client: Client) -> None:
    """After unlink, removal, leaving and accepting: one person per member."""
    h = home()
    _assert_one_person_per_member(h.workspace)
    client.force_login(h.carla)
    r = client.put(
        f"/api/v1/workspaces/{h.workspace.id}/persons/{h.persons['Carla'].id}/member",
        {"user_id": None},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    _assert_one_person_per_member(h.workspace)
    _remove(h.anne, h.workspace, h.bob)
    _assert_one_person_per_member(h.workspace)
    status, body = _invite(as_user(h.anne), h.workspace, h.persons["Bob"].id)
    assert status == 201, body
    _accept(_token(body), account="existing", email=h.bob.email)
    _assert_one_person_per_member(h.workspace)
    status, body = _invite(as_user(h.anne), h.workspace, h.persons["Oma"].id)
    assert status == 201, body
    _accept(_token(body), account="new", email="opa@example.nl", name="Opa")
    _assert_one_person_per_member(h.workspace)
    # An invitation without a person gives the newcomer their own person.
    r = post_json(as_user(h.anne), f"/workspaces/{h.workspace.id}/invitations", {})
    assert r.status_code == 201, r.json()
    _accept(_token(r.json()), account="new", email="nina@example.nl", name="Nina")
    _assert_one_person_per_member(h.workspace)
    nina = User.objects.get(email="nina@example.nl")
    r = post_json(as_user(nina), f"/workspaces/{h.workspace.id}/leave")
    assert r.status_code == 204, r.content
    _assert_one_person_per_member(h.workspace)
