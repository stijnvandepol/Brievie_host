"""Workspace settings: who may read and change them, and what they default to."""

from typing import TYPE_CHECKING

from django.test import Client

from apps.audit.codes import AuditCode
from apps.workspaces.models import Membership, Workspace
from tests.members_support import admin_of, as_user, bearer, join, paired, workspace_rows

# The privacy settings and switches of a home workspace (plan Tasks 29, 37), in every answer.
PRIVACY = {
    "care_is_private": True,
    "emergency_access": False,
    "everyone_sees_all": True,
    "departments_enabled": False,
    "admins_read_all": True,
}

if TYPE_CHECKING:
    from tests.helpers import Response


def url(ws: Workspace) -> str:
    return f"/api/v1/workspaces/{ws.id}/settings"


def put(client: Client, ws: Workspace, body: dict[str, object]) -> "Response":
    return client.put(url(ws), body, content_type="application/json")


def test_member_export_is_on_for_home_and_off_for_business(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    assert Workspace.objects.get(id=ws_a.id).allow_member_export is True
    assert Workspace.objects.get(id=ws_b.id).allow_member_export is False


def test_every_member_reads_the_settings(ws_a: Workspace) -> None:
    for role in Membership.Role.values:
        r = as_user(join(ws_a, role)).get(url(ws_a))
        assert r.status_code == 200
        assert r.json() == {**PRIVACY, "allow_member_export": True, "summary_language": "nl"}


def test_only_an_admin_changes_workspace_settings(ws_a: Workspace) -> None:
    for role in (Membership.Role.MEMBER, Membership.Role.VIEWER):
        r = put(as_user(join(ws_a, role)), ws_a, {"allow_member_export": False})
        assert r.status_code == 403
        assert r.json()["code"] == "forbidden"
    assert Workspace.objects.get(id=ws_a.id).allow_member_export is True

    r = put(as_user(admin_of(ws_a)), ws_a, {"allow_member_export": False})
    assert r.status_code == 200
    assert r.json() == {**PRIVACY, "allow_member_export": False, "summary_language": "nl"}
    assert Workspace.objects.get(id=ws_a.id).allow_member_export is False


def test_a_foreign_workspace_is_not_found(ws_a: Workspace, ws_b: Workspace) -> None:
    client = as_user(admin_of(ws_b))

    assert client.get(url(ws_a)).status_code == 404
    assert put(client, ws_a, {"allow_member_export": False}).status_code == 404
    assert Workspace.objects.get(id=ws_a.id).allow_member_export is True


def test_a_device_token_gets_401(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    _, token = paired(admin)
    client = Client()

    assert client.get(url(ws_a), headers=bearer(token)).status_code == 401
    r = client.put(
        url(ws_a),
        {"allow_member_export": False},
        content_type="application/json",
        headers=bearer(token),
    )
    assert r.status_code == 401
    assert Workspace.objects.get(id=ws_a.id).allow_member_export is True


def test_an_omitted_setting_stays_as_it_is(ws_a: Workspace) -> None:
    client = as_user(admin_of(ws_a))
    put(client, ws_a, {"allow_member_export": False})

    r = put(client, ws_a, {})

    assert r.status_code == 200
    assert r.json() == {**PRIVACY, "allow_member_export": False, "summary_language": "nl"}
    assert len(workspace_rows(ws_a, AuditCode.WORKSPACE_SETTING_CHANGED)) == 1


def test_a_change_is_logged_once_and_a_repeat_is_not(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    client = as_user(admin)

    put(client, ws_a, {"allow_member_export": False})
    put(client, ws_a, {"allow_member_export": False})

    (row,) = workspace_rows(ws_a, AuditCode.WORKSPACE_SETTING_CHANGED)
    assert row.actor_id == admin.id
    assert row.values == {"setting": "allow_member_export", "value": False}


def test_an_admin_sets_the_summary_language_and_it_is_logged(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    client = as_user(admin)

    r = put(client, ws_a, {"summary_language": "en"})

    assert r.status_code == 200
    assert r.json() == {**PRIVACY, "allow_member_export": True, "summary_language": "en"}
    assert Workspace.objects.get(id=ws_a.id).summary_language == "en"
    (row,) = workspace_rows(ws_a, AuditCode.WORKSPACE_SETTING_CHANGED)
    assert row.values == {"setting": "summary_language", "value": "en"}


def test_only_an_admin_sets_the_summary_language(ws_a: Workspace) -> None:
    r = put(as_user(join(ws_a, Membership.Role.MEMBER)), ws_a, {"summary_language": "en"})

    assert r.status_code == 403
    assert Workspace.objects.get(id=ws_a.id).summary_language == "nl"


def test_an_unknown_summary_language_is_refused(ws_a: Workspace) -> None:
    r = put(as_user(admin_of(ws_a)), ws_a, {"summary_language": "de"})

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert Workspace.objects.get(id=ws_a.id).summary_language == "nl"
