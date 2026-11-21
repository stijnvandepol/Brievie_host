"""Zorg and the emergency-access switch (plan Task 29, PB T15)."""

from django.test import Client

from apps.accounts.models import AccountNotice, User
from apps.audit.models import WorkspaceAuditEvent
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory
from tests.members_support import bearer, paired
from tests.visibility_support import Home, as_actor, home

JSON = "application/json"


def _settings_url(h: Home) -> str:
    return f"/api/v1/workspaces/{h.workspace.id}/settings"


def _switch_url(workspace: Workspace) -> str:
    return f"/api/v1/workspaces/{workspace.id}/emergency-access"


def _rows(workspace: Workspace, code: str) -> list[WorkspaceAuditEvent]:
    with as_actor(workspace, SYSTEM):
        return list(WorkspaceAuditEvent.objects.filter(code=code))


def _fresh(workspace: Workspace) -> Workspace:
    return Workspace.objects.get(id=workspace.id)


def test_defaults_per_kind_for_new_workspaces() -> None:
    at_home = create_workspace(name="Thuis", kind="home", admin=UserFactory.create())
    office = create_workspace(name="Kantoor", kind="business", admin=UserFactory.create())
    assert (at_home.care_is_private, at_home.emergency_access) == (True, False)
    assert (office.care_is_private, office.emergency_access) == (False, True)


def test_admin_switches_care_is_private(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    r = client.put(_settings_url(h), {"care_is_private": False}, content_type=JSON)
    assert r.status_code == 200, r.json()
    assert r.json()["care_is_private"] is False
    assert _fresh(h.workspace).care_is_private is False
    (row,) = _rows(h.workspace, "workspace_setting_changed")
    assert row.values == {"setting": "care_is_private", "value": False}


def test_member_cannot_switch_care_is_private(client: Client) -> None:
    h = home()
    client.force_login(h.bob)
    r = client.put(_settings_url(h), {"care_is_private": False}, content_type=JSON)
    assert r.status_code == 403
    assert _fresh(h.workspace).care_is_private is True


def test_a_part_a_body_keeps_care_is_private(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    r = client.put(_settings_url(h), {"allow_member_export": False}, content_type=JSON)
    assert r.status_code == 200
    assert _fresh(h.workspace).care_is_private is True


def test_an_empty_body_changes_nothing(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    assert client.put(_settings_url(h), {}, content_type=JSON).status_code == 200
    assert _rows(h.workspace, "workspace_setting_changed") == []


def test_every_member_reads_the_settings(client: Client) -> None:
    h = home()
    client.force_login(h.bob)
    body = client.get(_settings_url(h)).json()
    assert (body["care_is_private"], body["emergency_access"]) == (True, False)


def test_only_a_server_admin_switches_emergency_access(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    r = client.put(_switch_url(h.workspace), {"enabled": True}, content_type=JSON)
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    assert _fresh(h.workspace).emergency_access is False


def test_a_server_admin_switches_it_without_being_a_member(client: Client) -> None:
    h = home()
    server_admin = UserFactory.create(is_server_admin=True)
    client.force_login(server_admin)
    r = client.put(_switch_url(h.workspace), {"enabled": True}, content_type=JSON)
    assert r.status_code == 200, r.json()
    assert r.json() == {"enabled": True}
    assert _fresh(h.workspace).emergency_access is True


def test_switching_emergency_access_notifies_every_member(client: Client) -> None:
    h = home()
    User.objects.filter(id=h.anne.id).update(is_server_admin=True)
    client.force_login(h.anne)
    client.put(_switch_url(h.workspace), {"enabled": True}, content_type=JSON)
    for user in (h.anne, h.bob, h.carla):
        (notice,) = AccountNotice.objects.filter(user=user, code="emergency_access_switched_on")
        assert notice.values == {"workspace": "Thuis"}


def test_switching_emergency_access_is_audited(client: Client) -> None:
    h = home()
    User.objects.filter(id=h.anne.id).update(is_server_admin=True)
    client.force_login(h.anne)
    client.put(_switch_url(h.workspace), {"enabled": True}, content_type=JSON)
    client.put(_switch_url(h.workspace), {"enabled": True}, content_type=JSON)
    client.put(_switch_url(h.workspace), {"enabled": False}, content_type=JSON)
    assert len(_rows(h.workspace, "emergency_access_switched_on")) == 1
    assert len(_rows(h.workspace, "emergency_access_switched_off")) == 1
    assert AccountNotice.objects.filter(user=h.bob).count() == 2


def test_unknown_workspace_is_404(client: Client) -> None:
    import uuid

    client.force_login(UserFactory.create(is_server_admin=True))
    r = client.put(f"/api/v1/workspaces/{uuid.uuid4()}/emergency-access", {"enabled": True}, JSON)
    assert (r.status_code, r.json()["code"]) == (404, "workspace_not_found")


def test_a_device_token_cannot_switch_it(client: Client) -> None:
    h = home()
    User.objects.filter(id=h.anne.id).update(is_server_admin=True)
    _device, token = paired(h.anne)
    r = Client().put(
        _switch_url(h.workspace), {"enabled": True}, content_type=JSON, headers=bearer(token)
    )
    assert r.status_code == 401
