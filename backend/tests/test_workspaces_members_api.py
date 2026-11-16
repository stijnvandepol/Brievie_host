from django.test import Client

from apps.people.models import Person
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.factories import UserFactory


def members_url(workspace: Workspace) -> str:
    return f"/api/v1/workspaces/{workspace.id}/members"


def test_list_members_names_their_person(client: Client, ws_a: Workspace) -> None:
    admin = Membership.objects.get(workspace=ws_a, role="admin").user
    viewer = UserFactory.create(name="Zoë Viewer")
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    with workspace_context(ws_a.id):
        admin_person = Person.objects.get(user=admin)
    client.force_login(viewer)

    r = client.get(members_url(ws_a))

    assert r.status_code == 200
    by_user = {m["user_id"]: m for m in r.json()}
    assert by_user[str(admin.id)] == {
        "user_id": str(admin.id),
        "name": admin.name,
        "role": "admin",
        "person_id": str(admin_person.id),
    }
    assert by_user[str(viewer.id)]["person_id"] is None
    assert by_user[str(viewer.id)]["role"] == "viewer"


def test_members_of_another_workspace_are_404(
    client: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    client.force_login(Membership.objects.get(workspace=ws_a, role="admin").user)

    r = client.get(members_url(ws_b))

    assert r.status_code == 404
    assert r.json()["code"] == "workspace_not_found"
