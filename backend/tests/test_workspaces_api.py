from django.test import Client

from apps.workspaces.models import Membership, Workspace
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory


def test_list_workspaces_returns_only_the_callers(
    client: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    user = UserFactory.create()
    Membership.objects.create(user=user, workspace=ws_a, role="viewer")
    own = create_workspace(name="Eigen", kind="business", admin=user)
    client.force_login(user)

    r = client.get("/api/v1/workspaces")

    assert r.status_code == 200
    assert sorted(r.json(), key=lambda w: w["name"]) == [
        {"id": str(own.id), "name": "Eigen", "kind": "business", "role": "admin"},
        {"id": str(ws_a.id), "name": "Thuis", "kind": "home", "role": "viewer"},
    ]


def test_list_workspaces_is_empty_without_memberships(client: Client, ws_a: Workspace) -> None:
    client.force_login(UserFactory.create())

    r = client.get("/api/v1/workspaces")

    assert r.status_code == 200
    assert r.json() == []
