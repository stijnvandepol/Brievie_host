"""Refusals the code already makes, proven over HTTP with the state checked afterwards:
the cells of the security reading's endpoint matrix that had no test of their own."""

import pytest
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import AccountNotice, User
from apps.accounts.notices import notify
from apps.audit.models import WorkspaceAuditEvent
from apps.documents.jobs import build_member_export
from apps.documents.models import MemberExport
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Invitation, Membership, Workspace
from tests.factories import TEST_PASSWORD
from tests.helpers import post_json
from tests.members_support import admin_of, as_user, bearer, join, paired, role_of

NEW_PASSWORD = "een-lang-nieuw-wachtwoord-2026"


@pytest.fixture(autouse=True)
def _public_url(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def test_change_password_refuses_a_device_token(client: Client, ws_a: Workspace) -> None:
    member = join(ws_a)
    _, token = paired(member)

    r = post_json(
        client,
        "/me/password",
        {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD},
        headers=bearer(token),
    )

    assert (r.status_code, r.json()["code"]) == (401, "not_authenticated")
    assert User.objects.get(pk=member.pk).check_password(TEST_PASSWORD)


def test_marking_notices_seen_refuses_a_device_token(client: Client, ws_a: Workspace) -> None:
    member = join(ws_a)
    notify(member, AccountNotice.Code.SESSIONS_ENDED, actor_name="Anne")
    notice = AccountNotice.objects.get(user=member)
    _, token = paired(member)

    r = post_json(
        client, "/me/notices/seen", {"notice_ids": [str(notice.id)]}, headers=bearer(token)
    )

    assert (r.status_code, r.json()["code"]) == (401, "not_authenticated")
    assert AccountNotice.objects.get(pk=notice.pk).seen_at is None


def test_leaving_a_workspace_one_is_not_in_answers_not_found(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    member = join(ws_a)

    r = post_json(as_user(member), f"/workspaces/{ws_b.id}/leave")

    assert (r.status_code, r.json()["code"]) == (404, "workspace_not_found")
    assert role_of(ws_a, member) == "member"
    assert User.objects.get(pk=member.pk).is_active


def _audit_count(ws: Workspace) -> int:
    with workspace_context(ws.id):
        return WorkspaceAuditEvent.objects.count()


def _exports(ws: Workspace) -> int:
    with workspace_context(ws.id):
        return MemberExport.objects.count()


def test_a_member_removed_from_one_workspace_is_a_stranger_to_its_endpoints(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    # An admin of ws_a who is also in ws_b, with an export of their own in ws_a.
    member = join(ws_a, Membership.Role.ADMIN)
    join(ws_b, user=member)
    client = as_user(member)
    r = post_json(client, f"/workspaces/{ws_a.id}/my-export")
    assert r.status_code == 202
    with workspace_context(ws_a.id):
        row = MemberExport.objects.get(user=member)
    build_member_export(workspace_id=str(ws_a.id), export_id=str(row.id))
    other = join(ws_a)
    r = as_user(admin_of(ws_a)).delete(f"/api/v1/workspaces/{ws_a.id}/members/{member.id}")
    assert r.status_code == 204
    invitations_before = Invitation.objects.count()
    audit_before = _audit_count(ws_a)
    exports_before = _exports(ws_a)
    base = f"/api/v1/workspaces/{ws_a.id}"

    answers = [
        client.get(f"{base}/members"),
        client.get(f"{base}/settings"),
        client.get(f"{base}/invitations"),
        post_json(client, f"/workspaces/{ws_a.id}/invitations", {}),
        client.put(
            f"{base}/members/{other.id}/role", {"role": "admin"}, content_type="application/json"
        ),
        client.delete(f"{base}/members/{other.id}"),
        client.get(f"{base}/audit-events"),
        client.get(f"{base}/audit-events/csv"),
        post_json(client, f"/workspaces/{ws_a.id}/my-export"),
        client.get(f"{base}/my-export"),
        client.get(f"{base}/my-export/file"),
        post_json(client, f"/workspaces/{ws_a.id}/leave"),
    ]

    assert [(a.status_code, a.json()["code"]) for a in answers] == [
        (404, "workspace_not_found")
    ] * len(answers)
    assert client.get("/api/v1/me").status_code == 200  # still a member of ws_b
    assert role_of(ws_a, member) is None
    assert role_of(ws_a, other) == "member"
    assert Invitation.objects.count() == invitations_before
    assert _audit_count(ws_a) == audit_before
    assert _exports(ws_a) == exports_before
