from typing import TYPE_CHECKING

import pytest
from django.test import Client, override_settings

from apps.audit.codes import AuditCode
from apps.audit.models import WorkspaceAuditEvent
from apps.pipeline import settings_service
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, bearer_for
from tests.members_support import as_user, join

if TYPE_CHECKING:
    from tests.helpers import Response

BODY: dict[str, object] = {"auto_organise": True, "auto_confirm": False, "acknowledged": True}


def url(workspace: Workspace) -> str:
    return f"/api/v1/workspaces/{workspace.id}/ai/autopilot"


def put(client: Client, workspace: Workspace, body: dict[str, object]) -> "Response":
    return client.put(url(workspace), body, content_type="application/json")


def external(workspace: Workspace) -> None:
    with workspace_context(workspace.id, actor=SYSTEM):
        settings_service.configure(
            workspace_id=workspace.id,
            mode="external",
            base_url="",
            model="",
            api_key="nep-sleutel-voor-tests",
            consent=True,
        )


def stored(workspace: Workspace) -> tuple[bool, bool]:
    with workspace_context(workspace.id, actor=SYSTEM):
        state = settings_service.autopilot(workspace.id)
    return state.organise, state.confirm


def audit_rows(workspace: Workspace) -> list[WorkspaceAuditEvent]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return list(
            WorkspaceAuditEvent.objects.filter(
                code=AuditCode.WORKSPACE_SETTING_CHANGED,
                values__setting__in=["ai_auto_organise", "ai_auto_confirm"],
            )
        )


@pytest.fixture
def admin(ws_a: Workspace) -> Client:
    return as_user(admin_of(ws_a))


def test_a_member_reads_the_autopilot_and_its_notice(ws_a: Workspace) -> None:
    r = as_user(join(ws_a, "viewer")).get(url(ws_a))

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"auto_organise", "auto_confirm", "allowed", "notice", "notice_outdated"}
    assert (body["auto_organise"], body["auto_confirm"], body["allowed"]) == (False, False, True)
    assert body["notice"] == settings_service.autopilot_notice()


def test_reading_needs_a_login_and_membership(
    client: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    assert client.get(url(ws_a)).status_code == 401
    r = as_user(admin_of(ws_b)).get(url(ws_a))
    assert (r.status_code, r.json()["code"]) == (404, "workspace_not_found")


@pytest.mark.parametrize("role", ["member", "viewer"])
def test_only_an_admin_changes_the_autopilot(ws_a: Workspace, role: str) -> None:
    external(ws_a)

    r = put(as_user(join(ws_a, role)), ws_a, BODY)

    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    assert stored(ws_a) == (False, False)


def test_an_admin_switches_it_on_and_it_is_audited(admin: Client, ws_a: Workspace) -> None:
    external(ws_a)

    r = put(admin, ws_a, BODY)

    assert r.status_code == 200, r.content
    body = r.json()
    assert (body["auto_organise"], body["auto_confirm"], body["allowed"]) == (True, False, True)
    assert stored(ws_a) == (True, False)
    (row,) = audit_rows(ws_a)
    assert row.actor_id == admin_of(ws_a).id
    assert row.values == {"setting": "ai_auto_organise", "value": True}


def test_a_device_token_is_refused(client: Client, ws_a: Workspace) -> None:
    external(ws_a)

    r = client.put(
        url(ws_a),
        BODY,
        content_type="application/json",
        headers=bearer_for(admin_of(ws_a)),
    )

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"
    assert stored(ws_a) == (False, False)


def test_without_acknowledgement_it_is_409(admin: Client, ws_a: Workspace) -> None:
    external(ws_a)

    r = put(admin, ws_a, BODY | {"acknowledged": False})

    assert (r.status_code, r.json()["code"]) == (409, "ai_autopilot_ack_required")
    assert r.json()["message"]
    assert stored(ws_a) == (False, False)


def test_acknowledged_defaults_to_false(admin: Client, ws_a: Workspace) -> None:
    external(ws_a)

    r = put(admin, ws_a, {"auto_organise": True, "auto_confirm": False})

    assert (r.status_code, r.json()["code"]) == (409, "ai_autopilot_ack_required")


def test_without_ai_it_is_409(admin: Client, ws_a: Workspace) -> None:
    r = put(admin, ws_a, BODY)

    assert (r.status_code, r.json()["code"]) == (409, "ai_autopilot_needs_ai")
    assert stored(ws_a) == (False, False)


def test_when_the_server_forbids_it_is_409(admin: Client, ws_a: Workspace) -> None:
    external(ws_a)

    with override_settings(BRIEVIE_AI_AUTOPILOT_ALLOWED=False):
        r = put(admin, ws_a, BODY)
        read = admin.get(url(ws_a))

    assert (r.status_code, r.json()["code"]) == (409, "ai_autopilot_forbidden")
    assert read.json()["allowed"] is False


def test_switching_off_needs_no_acknowledgement(admin: Client, ws_a: Workspace) -> None:
    external(ws_a)
    put(admin, ws_a, BODY)

    r = put(admin, ws_a, {"auto_organise": False, "auto_confirm": False})

    assert r.status_code == 200
    assert stored(ws_a) == (False, False)


def test_the_ai_status_carries_the_switches(admin: Client, ws_a: Workspace) -> None:
    external(ws_a)
    put(admin, ws_a, BODY)

    body = admin.get(f"/api/v1/workspaces/{ws_a.id}/ai").json()

    assert (body["auto_organise"], body["auto_confirm"], body["autopilot_allowed"]) == (
        True,
        False,
        True,
    )


def test_the_ai_status_without_settings_has_the_defaults(admin: Client, ws_a: Workspace) -> None:
    body = admin.get(f"/api/v1/workspaces/{ws_a.id}/ai").json()

    assert (body["auto_organise"], body["auto_confirm"], body["autopilot_allowed"]) == (
        False,
        False,
        True,
    )
