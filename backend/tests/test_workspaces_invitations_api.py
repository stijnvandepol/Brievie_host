"""Invitations (D 6.2): an admin makes a one-time link; only its hash is stored."""

import hashlib
import re
from datetime import timedelta
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.audit.models import WorkspaceAuditEvent
from apps.audit.services import AuditActor
from apps.common.models import WorkspaceModel
from apps.people.models import Person
from apps.workspaces.context import workspace_context
from apps.workspaces.invitations import revoke_invitations_made_by
from apps.workspaces.models import Invitation, Membership, Workspace
from tests.documents_support import admin_of, bearer_for
from tests.factories import UserFactory
from tests.helpers import post_json

LINK = re.compile(r"^https://brievie\.local/uitnodiging#(?P<token>[A-Za-z0-9_-]{43})$")


@pytest.fixture(autouse=True)
def _public_url(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def url(ws: Workspace, suffix: str = "") -> str:
    return f"/workspaces/{ws.id}/invitations{suffix}"


def as_admin(ws: Workspace) -> Client:
    client = Client()
    client.force_login(admin_of(ws))
    return client


def join(ws: Workspace, role: str = "member") -> Client:
    user = UserFactory.create()
    Membership.objects.create(user=user, workspace=ws, role=role)
    client = Client()
    client.force_login(user)
    return client


def create(client: Client, ws: Workspace, **body: object) -> dict[str, object]:
    r = post_json(client, url(ws), body)
    assert r.status_code == 201, r.json()
    created: dict[str, object] = r.json()
    return created


def token_of(created: dict[str, object]) -> str:
    match = LINK.match(str(created["link"]))
    assert match is not None, created["link"]
    return match["token"]


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def person(ws: Workspace, name: str) -> Person:
    with workspace_context(ws.id):
        return Person.objects.create(workspace=ws, display_name=name)


def test_create_invitation_returns_a_fragment_link_once(ws_a: Workspace) -> None:
    admin = as_admin(ws_a)

    r = post_json(admin, url(ws_a), {"note": "Anne"})

    assert r.status_code == 201
    assert r["Cache-Control"] == "no-store"
    body = r.json()
    assert set(body) == {"invitation", "link", "home_network_only"}
    token = token_of(body)
    (invitation,) = Invitation.objects.filter(workspace=ws_a)
    assert invitation.token_hash == sha256(token)
    row = Invitation.objects.filter(pk=invitation.pk).values().get()
    assert token not in {str(value) for value in row.values()}
    listed = admin.get(f"/api/v1{url(ws_a)}")
    assert listed.status_code == 200
    assert token not in listed.content.decode()
    assert sha256(token) not in listed.content.decode()
    assert set(listed.json()[0]) == {
        "id", "role", "email", "note", "person_id", "person_name", "created_by_name",
        "created_at", "expires_at", "status",
    }  # fmt: skip


def test_default_role_is_member_and_validity_seven_days(ws_a: Workspace) -> None:
    before = timezone.now()

    created = create(as_admin(ws_a), ws_a)

    invitation = created["invitation"]
    assert isinstance(invitation, dict)
    assert invitation["role"] == "member"
    assert invitation["status"] == "open"
    assert invitation["created_by_name"] == admin_of(ws_a).name
    stored = Invitation.objects.get(workspace=ws_a)
    assert stored.role == "member"
    assert before + timedelta(days=7) <= stored.expires_at <= timezone.now() + timedelta(days=7)
    assert stored.created_by == admin_of(ws_a)


@pytest.mark.parametrize("days", [1, 7, 30])
def test_validity_choices(ws_a: Workspace, days: int) -> None:
    create(as_admin(ws_a), ws_a, expires_in_days=days)

    stored = Invitation.objects.get(workspace=ws_a)
    validity = stored.expires_at - stored.created_at
    assert abs(validity - timedelta(days=days)) < timedelta(minutes=1)


def test_validity_must_be_one_seven_or_thirty_days(ws_a: Workspace) -> None:
    for bad in (3, 0, 31, -7):
        r = post_json(as_admin(ws_a), url(ws_a), {"expires_in_days": bad})
        assert r.status_code == 422, bad
    r = post_json(as_admin(ws_a), url(ws_a), {"role": "owner"})
    assert r.status_code == 422
    assert not Invitation.objects.exists()


def test_the_role_and_email_are_stored(ws_a: Workspace) -> None:
    create(as_admin(ws_a), ws_a, role="viewer", email="  Anne@Example.NL ")

    stored = Invitation.objects.get(workspace=ws_a)
    assert (stored.role, stored.email) == ("viewer", "anne@example.nl")


def test_an_invalid_email_is_422(ws_a: Workspace) -> None:
    r = post_json(as_admin(ws_a), url(ws_a), {"email": "geen-adres"})

    assert r.status_code == 422
    assert not Invitation.objects.exists()


def test_the_note_is_bsn_masked(ws_a: Workspace) -> None:
    create(as_admin(ws_a), ws_a, note="Anne 111222333")

    assert Invitation.objects.get(workspace=ws_a).note == "Anne [BSN]"


def test_an_invitation_for_a_person_names_them(ws_a: Workspace) -> None:
    papa = person(ws_a, "Papa")

    created = create(as_admin(ws_a), ws_a, person_id=str(papa.id))

    invitation = created["invitation"]
    assert isinstance(invitation, dict)
    assert (invitation["person_id"], invitation["person_name"]) == (str(papa.id), "Papa")
    listed = as_admin(ws_a).get(f"/api/v1{url(ws_a)}").json()
    assert listed[0]["person_name"] == "Papa"


def test_a_linked_person_cannot_be_invited_again(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        own = Person.objects.get(user=admin_of(ws_a))

    r = post_json(as_admin(ws_a), url(ws_a), {"person_id": str(own.id)})

    assert r.status_code == 409
    assert r.json() == {
        "code": "person_already_linked",
        "message": "Deze persoon is al gekoppeld aan een lid.",
    }
    assert not Invitation.objects.exists()


def test_a_person_of_another_workspace_or_archived_is_404(ws_a: Workspace, ws_b: Workspace) -> None:
    elsewhere = person(ws_b, "Buurman")
    archived = person(ws_a, "Opa")
    with workspace_context(ws_a.id):
        Person.objects.filter(pk=archived.pk).update(archived_at=timezone.now())

    for person_id in (elsewhere.id, archived.id, uuid4()):
        r = post_json(as_admin(ws_a), url(ws_a), {"person_id": str(person_id)})
        assert r.status_code == 404
        assert r.json()["code"] == "person_not_found"
    assert not Invitation.objects.exists()


def test_a_person_that_requires_an_email_needs_one(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("apps.workspaces.invitations.person_requires_email", lambda person: True)
    papa = person(ws_a, "Papa")

    r = post_json(as_admin(ws_a), url(ws_a), {"person_id": str(papa.id)})

    assert r.status_code == 422
    assert r.json()["code"] == "email_required"
    assert not Invitation.objects.exists()
    create(as_admin(ws_a), ws_a, person_id=str(papa.id), email="papa@example.nl")


def test_email_of_a_member_is_refused(ws_a: Workspace) -> None:
    member = UserFactory.create(email="lid@example.nl")
    Membership.objects.create(user=member, workspace=ws_a, role="viewer")

    r = post_json(as_admin(ws_a), url(ws_a), {"email": "LID@example.nl"})

    assert r.status_code == 409
    assert r.json()["code"] == "already_member"
    assert not Invitation.objects.exists()


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_members_and_viewers_cannot_invite(ws_a: Workspace, role: str) -> None:
    client = join(ws_a, role)
    create(as_admin(ws_a), ws_a)
    invitation = Invitation.objects.get()

    answers = [
        post_json(client, url(ws_a), {}),
        client.get(f"/api/v1{url(ws_a)}"),
        client.delete(f"/api/v1{url(ws_a, f'/{invitation.id}')}"),
        post_json(client, url(ws_a, f"/{invitation.id}/renew")),
    ]

    assert [(r.status_code, r.json()["code"]) for r in answers] == [(403, "forbidden")] * 4
    assert Invitation.objects.count() == 1
    assert Invitation.objects.get().revoked_at is None


def test_a_device_token_cannot_manage_invitations(ws_a: Workspace) -> None:
    create(as_admin(ws_a), ws_a)
    invitation = Invitation.objects.get()
    phone = Client()
    headers = bearer_for(admin_of(ws_a))

    answers = [
        post_json(phone, url(ws_a), {}, headers=headers),
        phone.get(f"/api/v1{url(ws_a)}", headers=headers),
        phone.delete(f"/api/v1{url(ws_a, f'/{invitation.id}')}", headers=headers),
        post_json(phone, url(ws_a, f"/{invitation.id}/renew"), headers=headers),
    ]

    assert [(r.status_code, r.json()["code"]) for r in answers] == [(401, "not_authenticated")] * 4
    assert Invitation.objects.count() == 1
    assert Invitation.objects.get().revoked_at is None


def test_revoke_and_renew(ws_a: Workspace) -> None:
    admin = as_admin(ws_a)
    papa = person(ws_a, "Papa")
    first = create(admin, ws_a, role="viewer", person_id=str(papa.id), note="Papa", email="p@x.nl")
    old = Invitation.objects.get()
    Invitation.objects.filter(pk=old.pk).update(
        expires_at=old.created_at + timedelta(days=30), created_at=old.created_at
    )

    r = post_json(admin, url(ws_a, f"/{old.id}/renew"))

    assert r.status_code == 201
    assert r["Cache-Control"] == "no-store"
    renewed = r.json()
    assert token_of(renewed) != token_of(first)
    old.refresh_from_db()
    assert old.revoked_at is not None
    new = Invitation.objects.get(revoked_at=None)
    assert (new.role, new.person_id, new.email, new.note) == ("viewer", papa.id, "p@x.nl", "Papa")
    assert abs(new.expires_at - new.created_at - timedelta(days=30)) < timedelta(minutes=1)
    listed = admin.get(f"/api/v1{url(ws_a)}").json()
    assert [i["id"] for i in listed] == [str(new.id)]

    r = admin.delete(f"/api/v1{url(ws_a, f'/{new.id}')}")

    assert r.status_code == 204
    assert admin.get(f"/api/v1{url(ws_a)}").json() == []
    again = admin.delete(f"/api/v1{url(ws_a, f'/{new.id}')}")
    assert again.status_code == 404
    assert again.json()["code"] == "invitation_not_found"
    assert post_json(admin, url(ws_a, f"/{old.id}/renew")).status_code == 404


def test_an_expired_invitation_is_listed_as_expired_and_can_be_renewed(ws_a: Workspace) -> None:
    admin = as_admin(ws_a)
    create(admin, ws_a)
    old = Invitation.objects.get()
    Invitation.objects.filter(pk=old.pk).update(expires_at=timezone.now())

    listed = admin.get(f"/api/v1{url(ws_a)}").json()

    assert [i["status"] for i in listed] == ["expired"]
    assert post_json(admin, url(ws_a, f"/{old.id}/renew")).status_code == 201
    assert [i["status"] for i in admin.get(f"/api/v1{url(ws_a)}").json()] == ["open"]


def test_the_list_is_newest_first_and_leaves_out_used_and_revoked(ws_a: Workspace) -> None:
    admin = as_admin(ws_a)
    for note in ("een", "twee", "drie", "vier"):
        create(admin, ws_a, note=note)
    Invitation.objects.filter(note="twee").update(revoked_at=timezone.now())
    Invitation.objects.filter(note="drie").update(accepted_at=timezone.now())

    listed = admin.get(f"/api/v1{url(ws_a)}").json()

    assert [i["note"] for i in listed] == ["vier", "een"]


def test_invitations_of_another_workspace_are_404(ws_a: Workspace, ws_b: Workspace) -> None:
    create(as_admin(ws_b), ws_b)
    theirs = Invitation.objects.get(workspace=ws_b)
    admin_a = as_admin(ws_a)

    not_member = [
        post_json(admin_a, url(ws_b), {}),
        admin_a.get(f"/api/v1{url(ws_b)}"),
        admin_a.delete(f"/api/v1{url(ws_b, f'/{theirs.id}')}"),
        post_json(admin_a, url(ws_b, f"/{theirs.id}/renew")),
    ]
    # Through the own workspace's path: the id of another workspace's invitation.
    wrong_path = [
        admin_a.delete(f"/api/v1{url(ws_a, f'/{theirs.id}')}"),
        post_json(admin_a, url(ws_a, f"/{theirs.id}/renew")),
    ]

    assert [(r.status_code, r.json()["code"]) for r in not_member] == [
        (404, "workspace_not_found")
    ] * 4
    assert [(r.status_code, r.json()["code"]) for r in wrong_path] == [
        (404, "invitation_not_found")
    ] * 2
    assert theirs.id not in {i["id"] for i in admin_a.get(f"/api/v1{url(ws_a)}").json()}
    assert Invitation.objects.count() == 1
    assert Invitation.objects.get().revoked_at is None


def test_home_network_only_is_reported(ws_a: Workspace, settings: Settings) -> None:
    assert create(as_admin(ws_a), ws_a)["home_network_only"] is True

    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.example.nl", "https://brievie.local"]
    created = create(as_admin(ws_a), ws_a)

    assert created["home_network_only"] is False
    assert str(created["link"]).startswith("https://brievie.example.nl/uitnodiging#")


def test_invitation_events_are_logged(ws_a: Workspace) -> None:
    admin = as_admin(ws_a)
    created = create(admin, ws_a, role="viewer", expires_in_days=30, note="Anne")
    token = token_of(created)
    first = Invitation.objects.get()
    post_json(admin, url(ws_a, f"/{first.id}/renew"))
    second = Invitation.objects.get(revoked_at=None)
    admin.delete(f"/api/v1{url(ws_a, f'/{second.id}')}")

    with workspace_context(ws_a.id):
        rows = list(
            WorkspaceAuditEvent.objects.filter(code__startswith="invitation_").order_by(
                "created_at", "id"
            )
        )
    assert [(r.code, r.target_id, r.values) for r in rows] == [
        ("invitation_created", first.id, {"role": "viewer", "expires_in_days": 30}),
        ("invitation_revoked", first.id, {"reason": "renewed"}),
        ("invitation_created", second.id, {"role": "viewer", "expires_in_days": 30}),
        ("invitation_revoked", second.id, {"reason": "admin"}),
    ]
    assert {(r.target_kind, r.target_name, r.actor_id) for r in rows} == {
        ("invitation", "Anne", admin_of(ws_a).id)
    }
    assert all(token not in str(r.values) and sha256(token) not in str(r.values) for r in rows)


def test_the_audit_name_is_the_email_without_a_note(ws_a: Workspace) -> None:
    create(as_admin(ws_a), ws_a, email="anne@example.nl")

    with workspace_context(ws_a.id):
        row = WorkspaceAuditEvent.objects.get(code="invitation_created")
    assert row.target_name == "anne@example.nl"


def test_creating_is_rate_limited_per_admin(ws_a: Workspace) -> None:
    admin = as_admin(ws_a)
    statuses = [post_json(admin, url(ws_a), {}).status_code for _ in range(31)]

    assert statuses[:30] == [201] * 30
    assert statuses[30] == 429
    # Renewing makes a new link too, so it shares the count.
    some = Invitation.objects.filter(revoked_at=None).first()
    assert some is not None
    assert post_json(admin, url(ws_a, f"/{some.id}/renew")).status_code == 429
    # Another admin is not held back.
    other = UserFactory.create()
    Membership.objects.create(user=other, workspace=ws_a, role="admin")
    client = Client()
    client.force_login(other)
    assert post_json(client, url(ws_a), {}).status_code == 201


def test_revoke_invitations_made_by_stops_only_that_admins_open_links(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    first_admin = admin_of(ws_a)
    first = as_admin(ws_a)
    second_admin = UserFactory.create()
    Membership.objects.create(user=second_admin, workspace=ws_a, role="admin")
    Membership.objects.create(user=first_admin, workspace=ws_b, role="admin")
    second = Client()
    second.force_login(second_admin)
    for _ in range(2):
        create(first, ws_a)
    create(second, ws_a)
    create(first, ws_b)  # the same admin, another workspace
    accepted = Invitation.objects.filter(workspace=ws_a, created_by=first_admin).first()
    assert accepted is not None
    Invitation.objects.filter(pk=accepted.pk).update(accepted_at=timezone.now())

    count = revoke_invitations_made_by(
        workspace_id=ws_a.id, user_id=first_admin.id, actor=AuditActor.system()
    )

    assert count == 1
    open_rows = Invitation.objects.filter(revoked_at=None, accepted_at=None)
    assert {(i.workspace_id, i.created_by_id) for i in open_rows} == {
        (ws_a.id, second_admin.id),
        (ws_b.id, first_admin.id),
    }
    with workspace_context(ws_a.id):
        rows = list(WorkspaceAuditEvent.objects.filter(code="invitation_revoked"))
    assert [(r.values, r.actor_kind) for r in rows] == [({"reason": "creator_lost_role"}, "system")]


def test_invitation_is_not_a_workspace_table() -> None:
    assert not issubclass(Invitation, WorkspaceModel)
