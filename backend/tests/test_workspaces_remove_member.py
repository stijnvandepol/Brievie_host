"""Removing a member and leaving (D 6.3, D 21): one transaction, never without an admin.

Every refusal is proved through the HTTP endpoints as a real role, and checks that
nothing changed. The membership goes; the person stays without its account link; the
member's tasks here lose their assignee. With the last membership the account is
switched off: its sessions end and its devices stop.
"""

import re
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connection
from django.http import HttpResponse
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import PasswordReset, User, UserSession
from apps.accounts.services import lock_active_server_admins
from apps.audit.codes import AuditCode
from apps.audit.services import AuditActor, record
from apps.common.errors import ApiError
from apps.devices.models import Device, PairingCode
from apps.devices.services import create_pairing_code, pair_device
from apps.documents import intake
from apps.documents.models import Document
from apps.people.models import Person
from apps.pipeline.jobs import defer_processing
from apps.tasks.models import Task
from apps.workspaces import members
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Invitation, Membership, Workspace
from tests.documents_support import jpeg, upload
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json
from tests.members_support import (
    admin_of,
    as_user,
    bearer,
    join,
    paired,
    role_of,
    server_rows,
    workspace_rows,
)

LAST_ADMIN = {
    "code": "last_admin",
    "message": "Een werkruimte heeft altijd minstens één beheerder. "
    "Maak eerst iemand anders beheerder.",
}
LAST_SERVER_ADMIN = {
    "code": "last_admin",
    "message": "Er is altijd minstens één serverbeheerder nodig. Dit account kan daarom niet weg.",
}
LINK = re.compile(r"#(?P<token>[A-Za-z0-9_-]{43})$")


@pytest.fixture(autouse=True)
def _public_url(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def member_url(ws: Workspace, user: User) -> str:
    return f"/api/v1/workspaces/{ws.id}/members/{user.id}"


def remove(client: Client, ws: Workspace, user: User) -> "tuple[int, dict[str, object]]":
    r = client.delete(member_url(ws, user))
    return r.status_code, (r.json() if r.content else {})


def leave(client: Client, ws: Workspace) -> "tuple[int, dict[str, object]]":
    r = post_json(client, f"/workspaces/{ws.id}/leave")
    return r.status_code, (r.json() if r.content else {})


def person_of(ws: Workspace, user: User, name: str = "Papa") -> Person:
    with workspace_context(ws.id, actor=SYSTEM):
        person = Person.objects.filter(workspace=ws, user=user).first()
        if person is None:
            person = Person.objects.create(workspace=ws, display_name=name, user=user)
        return person


def linked_user(ws: Workspace, person: Person) -> uuid.UUID | None:
    with workspace_context(ws.id, actor=SYSTEM):
        return Person.objects.get(id=person.id).user_id


def task_for(ws: Workspace, user: User) -> Task:
    with workspace_context(ws.id, actor=SYSTEM):
        return Task.objects.create(
            workspace=ws, type=Task.Type.PAY, description="Betalen", assigned_to=user
        )


def assignee(ws: Workspace, task: Task) -> uuid.UUID | None:
    with workspace_context(ws.id, actor=SYSTEM):
        return Task.objects.get(id=task.id).assigned_to_id


def active_devices(user: User) -> int:
    return Device.objects.filter(user=user, revoked_at=None).count()


def server_admin(ws: Workspace, role: str = Membership.Role.MEMBER) -> User:
    return join(ws, role, UserFactory.create(is_server_admin=True))


# --- The last admin ---


def test_last_admin_cannot_leave_or_remove_themselves(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    client = as_user(admin)

    assert remove(client, ws_a, admin) == (409, LAST_ADMIN)
    assert leave(client, ws_a) == (409, LAST_ADMIN)

    assert role_of(ws_a, admin) == "admin"
    assert User.objects.get(id=admin.id).is_active
    assert client.get("/api/v1/me").status_code == 200
    assert workspace_rows(ws_a, AuditCode.MEMBER_REMOVED) == []
    assert workspace_rows(ws_a, AuditCode.MEMBER_LEFT) == []


def test_an_admin_may_leave_when_another_admin_exists(ws_a: Workspace, ws_b: Workspace) -> None:
    admin = admin_of(ws_a)
    join(ws_a, Membership.Role.ADMIN)
    join(ws_b, user=admin)

    assert leave(as_user(admin), ws_a) == (204, {})

    assert role_of(ws_a, admin) is None


# --- Removing ---


def test_removal_unlinks_the_person_and_keeps_it(ws_a: Workspace, ws_b: Workspace) -> None:
    member = join(ws_a)
    join(ws_b, user=member)
    person = person_of(ws_a, member)

    assert remove(as_user(admin_of(ws_a)), ws_a, member) == (204, {})

    assert role_of(ws_a, member) is None
    assert linked_user(ws_a, person) is None
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Person.objects.filter(id=person.id).exists()
    rows = workspace_rows(ws_a, AuditCode.PERSON_UNLINKED)
    assert [(r.target_kind, r.target_id, r.target_name) for r in rows] == [
        ("person", person.id, "Papa")
    ]


def test_removal_unassigns_the_members_tasks(ws_a: Workspace, ws_b: Workspace) -> None:
    member = join(ws_a)
    join(ws_b, user=member)
    here = task_for(ws_a, member)
    there = task_for(ws_b, member)

    assert remove(as_user(admin_of(ws_a)), ws_a, member)[0] == 204

    assert assignee(ws_a, here) is None
    assert assignee(ws_b, there) == member.id


def test_removing_the_last_membership_deactivates_the_account(
    client: Client, ws_a: Workspace
) -> None:
    member = join(ws_a)
    member_client = as_user(member)
    _, token = paired(member, "iPhone")
    paired(member, "iPad")
    _, code = create_pairing_code(member)

    assert remove(as_user(admin_of(ws_a)), ws_a, member) == (204, {})

    member.refresh_from_db()
    assert not member.is_active
    assert member_client.get("/api/v1/me").status_code == 401
    r = client.get("/api/v1/workspaces", headers=bearer(token))
    assert (r.status_code, r.json()["code"]) == (401, "not_authenticated")
    assert active_devices(member) == 0
    assert not PairingCode.objects.filter(user=member, used_at=None).exists()
    with pytest.raises(ApiError):
        pair_device(code=code, device_name="Nieuw")
    assert not UserSession.objects.filter(user=member).exists()
    removed = workspace_rows(ws_a, AuditCode.MEMBER_REMOVED)
    assert [r.values for r in removed] == [{"account_deactivated": True}]
    assert len(server_rows(AuditCode.DEVICE_REVOKED)) == 2
    ended = server_rows(AuditCode.SESSION_ENDED)
    assert [r.values for r in ended] == [{"count": 1, "reason": "deactivated"}]


def test_a_member_left_elsewhere_keeps_account_sessions_and_devices(
    client: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    member = join(ws_a)
    join(ws_b, user=member)
    member_client = as_user(member)
    _, token = paired(member)

    assert remove(as_user(admin_of(ws_a)), ws_a, member)[0] == 204

    member.refresh_from_db()
    assert member.is_active
    assert member_client.get("/api/v1/me").status_code == 200
    r = client.get("/api/v1/workspaces", headers=bearer(token))
    assert [w["id"] for w in r.json()] == [str(ws_b.id)]
    r = client.get(f"/api/v1/workspaces/{ws_a.id}/documents", headers=bearer(token))
    assert (r.status_code, r.json()["code"]) == (404, "workspace_not_found")
    assert active_devices(member) == 1
    removed = workspace_rows(ws_a, AuditCode.MEMBER_REMOVED)
    assert [r.values for r in removed] == [{"account_deactivated": False}]
    assert server_rows(AuditCode.DEVICE_REVOKED) == []


def test_removed_member_cannot_upload_or_confirm(client: Client, ws_a: Workspace) -> None:
    member = join(ws_a)
    member_client = as_user(member)
    _, token = paired(member)

    remove(as_user(admin_of(ws_a)), ws_a, member)

    r = upload(client, ws_a.id, [jpeg()], headers=bearer(token))
    assert r.status_code == 401
    r = post_json(member_client, f"/workspaces/{ws_a.id}/documents/{uuid.uuid4()}/confirm", {})
    assert r.status_code == 401
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert not Document.objects.exists()


def test_members_and_viewers_cannot_remove(ws_a: Workspace) -> None:
    for role in (Membership.Role.MEMBER, Membership.Role.VIEWER):
        caller = join(ws_a, role)
        other = join(ws_a)
        person = person_of(ws_a, other, name=f"Persoon {role}")
        status, body = remove(as_user(caller), ws_a, other)
        assert (status, body["code"]) == (403, "forbidden")
        assert role_of(ws_a, other) == "member"
        assert linked_user(ws_a, person) == other.id
    assert workspace_rows(ws_a, AuditCode.MEMBER_REMOVED) == []
    assert workspace_rows(ws_a, AuditCode.PERSON_UNLINKED) == []


def test_unknown_member_is_404(ws_a: Workspace, ws_b: Workspace) -> None:
    stranger = join(ws_b)

    status, body = remove(as_user(admin_of(ws_a)), ws_a, stranger)

    assert (status, body["code"]) == (404, "member_not_found")
    assert role_of(ws_b, stranger) == "member"
    status, body = remove(as_user(admin_of(ws_a)), ws_b, stranger)
    assert (status, body["code"]) == (404, "workspace_not_found")


def test_a_device_token_cannot_remove_or_leave(client: Client, ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    member = join(ws_a)
    _, admin_token = paired(admin)
    _, member_token = paired(member)

    r = client.delete(member_url(ws_a, member), headers=bearer(admin_token))
    assert (r.status_code, r.json()["code"]) == (401, "not_authenticated")
    r = post_json(client, f"/workspaces/{ws_a.id}/leave", headers=bearer(member_token))
    assert (r.status_code, r.json()["code"]) == (401, "not_authenticated")

    assert role_of(ws_a, member) == "member"


# --- Server admins ---


def test_workspace_admin_cannot_deactivate_a_server_admin(ws_a: Workspace) -> None:
    boss = server_admin(ws_a)

    status, body = remove(as_user(admin_of(ws_a)), ws_a, boss)

    assert (status, body["code"]) == (403, "server_admin_only")
    boss.refresh_from_db()
    assert boss.is_active
    assert boss.is_server_admin
    assert role_of(ws_a, boss) == "member"


def test_workspace_admin_may_remove_a_server_admin_who_stays_elsewhere(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    boss = server_admin(ws_a)
    join(ws_b, user=boss)

    assert remove(as_user(admin_of(ws_a)), ws_a, boss)[0] == 204

    boss.refresh_from_db()
    assert boss.is_active
    assert boss.is_server_admin


def test_last_server_admin_cannot_be_deactivated(ws_a: Workspace) -> None:
    boss = server_admin(ws_a)
    client = as_user(boss)

    assert leave(client, ws_a) == (409, LAST_SERVER_ADMIN)

    boss.refresh_from_db()
    assert boss.is_active
    assert boss.is_server_admin
    assert role_of(ws_a, boss) == "member"
    assert client.get("/api/v1/me").status_code == 200


def test_a_deactivated_server_admin_is_one_no_more(ws_a: Workspace) -> None:
    s1 = server_admin(ws_a, Membership.Role.ADMIN)
    s2 = server_admin(ws_a)

    assert remove(as_user(s1), ws_a, s2) == (204, {})

    s2.refresh_from_db()
    assert not s2.is_active
    assert not s2.is_server_admin
    rows = server_rows(AuditCode.SERVER_ADMIN_REVOKED)
    assert [(r.actor_id, r.target_id) for r in rows] == [(s1.id, s2.id)]

    # A plain workspace admin invites them back: the account returns without the power.
    r = post_json(as_user(admin_of(ws_a)), f"/workspaces/{ws_a.id}/invitations", {})
    assert r.status_code == 201
    match = LINK.search(r.json()["link"])
    assert match is not None
    body = {
        "token": match["token"],
        "account": "existing",
        "email": s2.email,
        "password": TEST_PASSWORD,
    }
    r = post_json(Client(), "/invitations/accept", body)
    assert r.status_code == 200, r.json()
    s2.refresh_from_db()
    assert s2.is_active
    assert not s2.is_server_admin
    assert role_of(ws_a, s2) == "member"


@pytest.mark.django_db(transaction=True)
def test_two_server_admins_leaving_at_once_keep_one(
    ws_a: Workspace, ws_b: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    s1 = server_admin(ws_a)
    s2 = server_admin(ws_b)
    clients = {ws_a.id: as_user(s1), ws_b.id: as_user(s2)}
    barrier = threading.Barrier(2)
    original: Callable[[], list[User]] = lock_active_server_admins

    def lock_after_both_checked() -> list[User]:
        barrier.wait(timeout=10)
        return original()

    monkeypatch.setattr(members, "lock_active_server_admins", lock_after_both_checked)
    outcomes: list[tuple[int, object]] = []

    def run(ws: Workspace) -> None:
        try:
            status, body = leave(clients[ws.id], ws)
            outcomes.append((status, body.get("code")))
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(ws,)) for ws in (ws_a, ws_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(outcomes, key=str) == [(204, None), (409, "last_admin")]
    assert User.objects.filter(is_server_admin=True, is_active=True).count() == 1


# --- What the removed admin made, leaving, coming back ---


def test_removal_revokes_what_the_admin_made(ws_a: Workspace, ws_b: Workspace) -> None:
    b = join(ws_a, Membership.Role.ADMIN)
    join(ws_b, user=b)
    target = join(ws_a)
    b_client = as_user(b)
    assert post_json(b_client, f"/workspaces/{ws_a.id}/invitations", {}).status_code == 201
    assert post_json(b_client, f"/users/{target.id}/password-reset").status_code == 201

    assert remove(as_user(admin_of(ws_a)), ws_a, b)[0] == 204

    assert Invitation.objects.get(created_by=b).revoked_at is not None
    assert PasswordReset.objects.get(created_by=b).revoked_at is not None
    revoked = workspace_rows(ws_a, AuditCode.INVITATION_REVOKED)
    assert [r.values for r in revoked] == [{"reason": "creator_lost_role"}]


def test_member_leaves(ws_a: Workspace, ws_b: Workspace) -> None:
    member = join(ws_a, Membership.Role.VIEWER)
    join(ws_b, user=member)
    person = person_of(ws_a, member)

    assert leave(as_user(member), ws_a) == (204, {})

    assert role_of(ws_a, member) is None
    assert linked_user(ws_a, person) is None
    rows = workspace_rows(ws_a, AuditCode.MEMBER_LEFT)
    assert [(r.actor_id, r.target_id, r.values) for r in rows] == [
        (member.id, member.id, {"account_deactivated": False})
    ]
    assert len(workspace_rows(ws_a, AuditCode.PERSON_UNLINKED)) == 1
    assert workspace_rows(ws_a, AuditCode.MEMBER_REMOVED) == []


def test_leaving_the_last_workspace_answers_204_and_signs_out(ws_a: Workspace) -> None:
    member = join(ws_a)
    client = as_user(member)

    r = post_json(client, f"/workspaces/{ws_a.id}/leave")

    assert r.status_code == 204
    assert r.content == b""
    assert client.get("/api/v1/me").status_code == 401
    member.refresh_from_db()
    assert not member.is_active
    assert not UserSession.objects.filter(user=member).exists()


def test_re_invited_member_gets_their_person_back(ws_a: Workspace) -> None:
    member = join(ws_a)
    person = person_of(ws_a, member)
    admin_client = as_user(admin_of(ws_a))
    remove(admin_client, ws_a, member)

    r = post_json(admin_client, f"/workspaces/{ws_a.id}/invitations", {"person_id": str(person.id)})
    assert r.status_code == 201, r.json()
    match = LINK.search(r.json()["link"])
    assert match is not None
    body = {
        "token": match["token"],
        "account": "existing",
        "email": member.email,
        "password": TEST_PASSWORD,
    }
    r = post_json(Client(), "/invitations/accept", body)

    assert r.status_code == 200, r.json()
    assert linked_user(ws_a, person) == member.id
    member.refresh_from_db()
    assert member.is_active


def test_removal_calls_the_part_b_seam(ws_a: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    member = join(ws_a)
    leaver = join(ws_a)
    calls: list[tuple[uuid.UUID, uuid.UUID]] = []
    monkeypatch.setattr(members, "on_member_removed", lambda w, u: calls.append((w, u)))

    remove(as_user(admin_of(ws_a)), ws_a, member)
    leave(as_user(leaver), ws_a)

    assert calls == [(ws_a.id, member.id), (ws_a.id, leaver.id)]


def test_the_person_is_unlinked_before_the_membership_goes(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = join(ws_a)
    person = person_of(ws_a, member)
    seen: list[tuple[bool, uuid.UUID | None]] = []

    def seam(workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
        still_member = Membership.objects.filter(workspace_id=workspace_id, user_id=user_id)
        seen.append((still_member.exists(), linked_user(ws_a, person)))

    monkeypatch.setattr(members, "on_member_removed", seam)

    remove(as_user(admin_of(ws_a)), ws_a, member)

    assert seen == [(True, None)]


# --- One transaction ---


def test_a_failure_at_the_last_step_leaves_everything_as_it_was(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = join(ws_a, Membership.Role.ADMIN)
    member_client = as_user(member)
    _, token = paired(member)
    person = person_of(ws_a, member)
    task = task_for(ws_a, member)
    admin_client = as_user(admin_of(ws_a))
    post_json(as_user(member), f"/workspaces/{ws_a.id}/invitations", {})
    original = record

    def fail_at_the_end(code: AuditCode, **kwargs: object) -> None:
        if code == AuditCode.MEMBER_REMOVED:
            raise RuntimeError("the last step fails")
        original(code, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(members, "record", fail_at_the_end)
    admin_client.raise_request_exception = False

    r = admin_client.delete(member_url(ws_a, member))

    assert r.status_code == 500
    member.refresh_from_db()
    assert member.is_active
    assert role_of(ws_a, member) == "admin"
    assert linked_user(ws_a, person) == member.id
    assert assignee(ws_a, task) == member.id
    assert active_devices(member) == 1
    assert Invitation.objects.get(created_by=member).revoked_at is None
    assert member_client.get("/api/v1/me").status_code == 200
    assert Client().get("/api/v1/workspaces", headers=bearer(token)).status_code == 200
    assert workspace_rows(ws_a, AuditCode.PERSON_UNLINKED) == []
    assert server_rows(AuditCode.DEVICE_REVOKED) == []


# --- The accepted race: a removal during an upload (plan decision 26) ---


@pytest.mark.django_db(transaction=True)
def test_an_upload_running_during_the_removal_commits_whole(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = join(ws_a)
    device, token = paired(member)
    # A device seen just now is not written by this request (no row lock on it).
    Device.objects.filter(id=device.id).update(last_seen_at=timezone.now())
    admin_client = as_user(admin_of(ws_a))
    uploading = threading.Event()
    removed = threading.Event()
    original = defer_processing

    def pause_mid_upload(*, workspace_id: uuid.UUID, document_id: uuid.UUID) -> None:
        uploading.set()
        removed.wait(timeout=10)
        original(workspace_id=workspace_id, document_id=document_id)

    monkeypatch.setattr(intake, "defer_processing", pause_mid_upload)
    results: dict[str, int] = {}

    def run_upload() -> None:
        try:
            r = upload(Client(), ws_a.id, [jpeg("een"), jpeg("twee")], headers=bearer(token))
            results["upload"] = r.status_code
        finally:
            connection.close()

    def run_removal() -> None:
        try:
            results["removal"] = remove(admin_client, ws_a, member)[0]
        finally:
            connection.close()

    uploader = threading.Thread(target=run_upload)
    uploader.start()
    assert uploading.wait(timeout=10)
    remover = threading.Thread(target=run_removal)
    remover.start()
    remover.join(timeout=10)
    removal_finished_first = not remover.is_alive()
    removed.set()
    uploader.join(timeout=30)
    remover.join(timeout=30)

    assert removal_finished_first  # no lock was added for the race
    assert results == {"upload": 201, "removal": 204}
    with workspace_context(ws_a.id, actor=SYSTEM):
        document = Document.objects.get()
        paths = list(document.pages.order_by("index").values_list("storage_path", flat=True))
    assert document.scanned_by_id == member.id
    assert len(paths) == 2
    root = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    assert all((root / p).is_file() for p in paths)
    assert not User.objects.get(id=member.id).is_active


# --- Invitations bound to the removed account's address ---


def _invite_link(client: Client, ws: Workspace, **body: object) -> str:
    r = post_json(client, f"/workspaces/{ws.id}/invitations", body)
    assert r.status_code == 201, r.json()
    match = LINK.search(r.json()["link"])
    assert match is not None
    return match["token"]


def test_an_open_invitation_bound_to_a_removed_member_stops(ws_a: Workspace) -> None:
    admin_client = as_user(admin_of(ws_a))
    m = UserFactory.create(email="m@example.nl")
    # L1: an admin link bound to m's address, made before m joined some other way.
    l1 = _invite_link(admin_client, ws_a, role="admin", email="m@example.nl")
    join(ws_a, user=m)

    assert remove(admin_client, ws_a, m) == (204, {})

    body = {"token": l1, "account": "existing", "email": m.email, "password": TEST_PASSWORD}
    r = post_json(Client(), "/invitations/accept", body)
    assert (r.status_code, r.json()["code"]) == (400, "invitation_invalid")
    m.refresh_from_db()
    assert not m.is_active
    assert role_of(ws_a, m) is None
    bound = Invitation.objects.get(email="m@example.nl")
    assert bound.revoked_at is not None
    rows = workspace_rows(ws_a, AuditCode.INVITATION_REVOKED)
    assert [(r.target_id, r.values) for r in rows] == [(bound.id, {"reason": "member_removed"})]


def test_leaving_stops_invitations_bound_to_the_leaver(ws_a: Workspace, ws_b: Workspace) -> None:
    m = UserFactory.create(email="m@example.nl")
    _invite_link(as_user(admin_of(ws_a)), ws_a, email="m@example.nl")
    join(ws_a, user=m)
    join(ws_b, user=m)

    assert leave(as_user(m), ws_a) == (204, {})

    assert Invitation.objects.get(email="m@example.nl").revoked_at is not None


def test_removal_takes_invitations_before_server_admins_before_the_user(ws_a: Workspace) -> None:
    member = join(ws_a, Membership.Role.ADMIN)
    request = RequestFactory().delete("/")
    SessionMiddleware(lambda r: HttpResponse()).process_request(request)
    request.user = AnonymousUser()

    with CaptureQueriesContext(connection) as queries:
        members.remove_member(
            request=request,
            workspace=ws_a,
            user_id=member.id,
            actor=AuditActor.for_user(admin_of(ws_a)),
            left=False,
        )

    sqls = [q["sql"] for q in queries.captured_queries]

    def first(*parts: str) -> int:
        return next(i for i, sql in enumerate(sqls) if all(p in sql for p in parts))

    invitations = first('FROM "workspaces_invitation"', "FOR UPDATE")
    server_admins = first('FROM "accounts_user"', 'AND "accounts_user"."is_server_admin"', " FOR ")
    the_user = first('FROM "accounts_user"', 'WHERE "accounts_user"."id" =', "FOR NO KEY UPDATE")
    assert invitations < server_admins < the_user
