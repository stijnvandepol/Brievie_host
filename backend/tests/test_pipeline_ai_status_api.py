import pytest
from django.test import Client

from apps.pipeline.settings_service import configure
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of
from tests.factories import UserFactory


def ai_url(workspace: Workspace) -> str:
    return f"/api/v1/workspaces/{workspace.id}/ai"


def test_ai_status_is_off_without_settings(client: Client, ws_a: Workspace) -> None:
    client.force_login(admin_of(ws_a))

    r = client.get(ai_url(ws_a))

    assert r.status_code == 200
    assert r.json() == {
        "mode": "off", "supplier": None, "host": None, "since": None, "has_api_key": False,
        "model": None, "auto_organise": False, "auto_confirm": False, "autopilot_allowed": True,
    }  # fmt: skip


@pytest.mark.parametrize("role", ["viewer", "admin"])
def test_ai_status_has_no_address_model_or_key(client: Client, ws_a: Workspace, role: str) -> None:
    with workspace_context(ws_a.id):
        configure(
            workspace_id=ws_a.id,
            mode="external",
            base_url="",
            model="",
            api_key="sk-geheim",
            consent=True,
        )
    user = admin_of(ws_a)
    if role == "viewer":
        user = UserFactory.create()
        Membership.objects.create(user=user, workspace=ws_a, role="viewer")
    client.force_login(user)

    r = client.get(ai_url(ws_a))

    assert r.status_code == 200
    assert set(r.json()) == {
        "mode", "supplier", "host", "since", "has_api_key", "model",
        "auto_organise", "auto_confirm", "autopilot_allowed",
    }  # fmt: skip
    assert (r.json()["mode"], r.json()["host"]) == ("external", "api.mistral.ai")
    for hidden in ("/v1", "sk-geheim"):
        assert hidden not in r.content.decode()
    # Only an administrator gets the model, for the settings form.
    assert r.json()["model"] == ("mistral-small-latest" if role == "admin" else None)


def test_ai_status_of_another_workspace_is_404(
    client: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    client.force_login(admin_of(ws_a))

    r = client.get(ai_url(ws_b))

    assert r.status_code == 404
    assert r.json()["code"] == "workspace_not_found"
