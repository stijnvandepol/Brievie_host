"""Changing a member's role (D 6.3): only an admin, never below one admin.

Every refusal is proved through the HTTP endpoint as a real role, and checks that
nothing changed. The race between two admins is decided on the locked membership rows.
"""

import threading
from collections.abc import Callable

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from pytest_django import Settings

from apps.accounts.models import PasswordReset
from apps.audit.codes import AuditCode
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.people.models import Person
from apps.workspaces import members
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.members import change_role, lock_memberships
from apps.workspaces.models import Invitation, Membership, Workspace
from tests.helpers import post_json
from tests.members_support import (
    admin_of,
    as_user,
    bearer,
    join,
    paired,
    role_of,
    workspace_rows,
)

LAST_ADMIN = {
    "code": "last_admin",
    "message": "Een werkruimte heeft altijd minstens één beheerder. "
    "Maak eerst iemand anders beheerder.",
}


@pytest.fixture(autouse=True)
def _public_url(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def role_url(ws: Workspace, user_id: object) -> str:
    return f"/api/v1/workspaces/{ws.id}/members/{user_id}/role"


def put_role(
    client: Client, ws: Workspace, user_id: object, role: str
) -> "tuple[int, dict[str, object]]":
    r = client.put(role_url(ws, user_id), {"role": role}, content_type="application/json")
    return r.status_code, r.json()


def test_admin_changes_a_members_role(ws_a: Workspace) -> None:
    member = join(ws_a)
    member_client = as_user(member)
    assert (
        post_json(
            member_client, f"/workspaces/{ws_a.id}/persons", {"display_name": "Oma"}
        ).status_code
        == 201
    )

    status, body = put_role(as_user(admin_of(ws_a)), ws_a, member.id, "viewer")

    assert status == 200
    assert body["user_id"] == str(member.id)
    assert body["role"] == "viewer"
    assert role_of(ws_a, member) == "viewer"
    # The member's own session works on at once, with the new role.
    r = post_json(member_client, f"/workspaces/{ws_a.id}/persons", {"display_name": "Opa"})
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    assert member_client.get(f"/api/v1/workspaces/{ws_a.id}/members").status_code == 200


def test_last_admin_cannot_demote_themselves(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)

    status, body = put_role(as_user(admin), ws_a, admin.id, "member")

    assert (status, body) == (409, LAST_ADMIN)
    assert role_of(ws_a, admin) == "admin"
    assert workspace_rows(ws_a, AuditCode.MEMBER_ROLE_CHANGED) == []


def test_an_admin_may_step_down_when_another_admin_exists(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    join(ws_a, Membership.Role.ADMIN)

    status, body = put_role(as_user(admin), ws_a, admin.id, "member")

    assert status == 200
    assert body["role"] == "member"
    assert role_of(ws_a, admin) == "member"


def test_a_demoted_actor_cannot_finish_a_change(ws_a: Workspace) -> None:
    a = admin_of(ws_a)
    b = join(ws_a, Membership.Role.ADMIN)
    join(ws_a, Membership.Role.ADMIN)
    change_role(workspace=ws_a, user_id=b.id, role="member", actor=AuditActor.for_user(a))

    with pytest.raises(ApiError) as caught:
        # B was authorised by the endpoint before A's change; the locked rows decide.
        change_role(workspace=ws_a, user_id=a.id, role="member", actor=AuditActor.for_user(b))

    assert (caught.value.status, caught.value.code) == (403, "forbidden")
    assert role_of(ws_a, a) == "admin"
    assert role_of(ws_a, b) == "member"
    assert len(workspace_rows(ws_a, AuditCode.MEMBER_ROLE_CHANGED)) == 1


@pytest.mark.django_db(transaction=True)
def test_two_admins_demoting_each_other_at_once_keep_one(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = admin_of(ws_a)
    b = join(ws_a, Membership.Role.ADMIN)
    c = join(ws_a, Membership.Role.ADMIN)
    clients = {a.id: as_user(a), b.id: as_user(b)}
    barrier = threading.Barrier(2)
    original: Callable[[Workspace], list[Membership]] = members.lock_memberships

    def lock_after_both_were_authorised(workspace: Workspace) -> list[Membership]:
        barrier.wait(timeout=10)
        return original(workspace)

    monkeypatch.setattr(members, "lock_memberships", lock_after_both_were_authorised)
    statuses: list[int] = []

    def run(actor_id: object, target_id: object) -> None:
        try:
            client = clients[actor_id]  # type: ignore[index]
            statuses.append(put_role(client, ws_a, target_id, "member")[0])
        finally:
            connection.close()

    threads = [
        threading.Thread(target=run, args=(a.id, b.id)),
        threading.Thread(target=run, args=(b.id, a.id)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(statuses) == [200, 403]
    roles = {role_of(ws_a, a), role_of(ws_a, b)}
    assert roles == {"admin", "member"}
    assert role_of(ws_a, c) == "admin"


def test_demotion_revokes_open_invitations_and_reset_links(ws_a: Workspace) -> None:
    a = admin_of(ws_a)
    b = join(ws_a, Membership.Role.ADMIN)
    target = join(ws_a)
    b_client = as_user(b)
    r = post_json(b_client, f"/workspaces/{ws_a.id}/invitations", {"role": "admin"})
    assert r.status_code == 201
    r = post_json(b_client, f"/users/{target.id}/password-reset")
    assert r.status_code == 201, r.json()
    a_made = post_json(as_user(a), f"/workspaces/{ws_a.id}/invitations", {"role": "member"})
    assert a_made.status_code == 201

    status, _ = put_role(as_user(a), ws_a, b.id, "member")

    assert status == 200
    assert Invitation.objects.get(created_by=b).revoked_at is not None
    assert Invitation.objects.get(created_by=a).revoked_at is None
    assert PasswordReset.objects.get(created_by=b).revoked_at is not None
    revoked = workspace_rows(ws_a, AuditCode.INVITATION_REVOKED)
    assert [row.values for row in revoked] == [{"reason": "creator_lost_role"}]


def test_promotion_keeps_what_the_member_made(ws_a: Workspace) -> None:
    b = join(ws_a, Membership.Role.ADMIN)
    post_json(as_user(b), f"/workspaces/{ws_a.id}/invitations", {"role": "member"})

    status, _ = put_role(as_user(admin_of(ws_a)), ws_a, b.id, "admin")

    assert status == 200
    assert Invitation.objects.get(created_by=b).revoked_at is None


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_members_cannot_change_roles(ws_a: Workspace, role: str) -> None:
    caller = join(ws_a, role)
    other = join(ws_a, Membership.Role.VIEWER)
    client = as_user(caller)

    # Nobody raises their own role, and nobody below admin changes another's.
    for target, new_role in ((caller, "admin"), (other, "member"), (admin_of(ws_a), "viewer")):
        status, body = put_role(client, ws_a, target.id, new_role)
        assert (status, body["code"]) == (403, "forbidden")

    assert role_of(ws_a, caller) == role
    assert role_of(ws_a, other) == "viewer"
    assert workspace_rows(ws_a, AuditCode.MEMBER_ROLE_CHANGED) == []


def test_a_device_token_cannot_change_roles(client: Client, ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    member = join(ws_a)
    _, token = paired(admin)

    r = client.put(
        role_url(ws_a, member.id),
        {"role": "viewer"},
        content_type="application/json",
        headers=bearer(token),
    )

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"
    assert role_of(ws_a, member) == "member"


def test_unknown_member_is_404(ws_a: Workspace, ws_b: Workspace) -> None:
    stranger = admin_of(ws_b)
    client = as_user(admin_of(ws_a))

    for user_id in (stranger.id, "6f1c1d4e-0000-4000-8000-000000000000"):
        status, body = put_role(client, ws_a, user_id, "viewer")
        assert (status, body["code"]) == (404, "member_not_found")

    assert role_of(ws_b, stranger) == "admin"


def test_another_workspace_answers_as_unknown(ws_a: Workspace, ws_b: Workspace) -> None:
    member_b = join(ws_b)

    status, body = put_role(as_user(admin_of(ws_a)), ws_b, member_b.id, "viewer")

    assert (status, body["code"]) == (404, "workspace_not_found")
    assert role_of(ws_b, member_b) == "member"


def test_an_unknown_role_is_422(ws_a: Workspace) -> None:
    member = join(ws_a)

    status, _ = put_role(as_user(admin_of(ws_a)), ws_a, member.id, "owner")

    assert status == 422
    assert role_of(ws_a, member) == "member"


def test_role_change_is_logged_with_from_and_to(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    member = join(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        person = Person.objects.create(workspace=ws_a, display_name="Papa", user=member)

    put_role(as_user(admin), ws_a, member.id, "admin")
    put_role(as_user(admin), ws_a, member.id, "admin")  # the same role: no second row

    rows = workspace_rows(ws_a, AuditCode.MEMBER_ROLE_CHANGED)
    assert len(rows) == 1
    row = rows[0]
    assert row.values == {"from": "member", "to": "admin"}
    assert (row.actor_id, row.target_kind, row.target_id) == (admin.id, "user", member.id)
    assert row.target_name == person.display_name


def test_member_changes_take_the_memberships_lock_in_id_order(ws_a: Workspace) -> None:
    join(ws_a)
    join(ws_a)

    with CaptureQueriesContext(connection) as queries:
        locked = lock_memberships(ws_a)

    sql = queries[-1]["sql"]
    assert 'FROM "workspaces_membership"' in sql
    assert sql.index("ORDER BY") < sql.index("FOR UPDATE")
    assert '"workspaces_membership"."id"' in sql[sql.index("ORDER BY") : sql.index("FOR UPDATE")]
    assert [m.id for m in locked] == sorted(m.id for m in locked)
    assert len(locked) == 3


def test_change_role_takes_that_lock(ws_a: Workspace) -> None:
    member = join(ws_a)

    with CaptureQueriesContext(connection) as queries:
        change_role(
            workspace=ws_a,
            user_id=member.id,
            role="viewer",
            actor=AuditActor.for_user(admin_of(ws_a)),
        )

    locks = [
        q["sql"]
        for q in queries.captured_queries
        if 'FROM "workspaces_membership"' in q["sql"] and "FOR UPDATE" in q["sql"]
    ]
    assert len(locks) == 1
