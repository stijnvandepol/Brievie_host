import uuid
from typing import TYPE_CHECKING

import pytest
from django.db import connection
from django.test import Client

from apps.accounts.models import User
from apps.documents.models import Folder
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.factories import UserFactory
from tests.helpers import post_json

if TYPE_CHECKING:
    from tests.helpers import Response


def folders_url(workspace_id: uuid.UUID | str) -> str:
    return f"/api/v1/workspaces/{workspace_id}/folders"


def create(client: Client, workspace_id: uuid.UUID | str, body: dict[str, object]) -> "Response":
    return post_json(client, f"/workspaces/{workspace_id}/folders", body)


def admin_of(workspace: Workspace) -> User:
    return Membership.objects.get(workspace=workspace, role="admin").user


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def test_create_and_list_folders(client_a: Client, ws_a: Workspace) -> None:
    r = create(client_a, ws_a.id, {"name": "Auto"})
    assert r.status_code == 201
    parent = r.json()
    assert parent["name"] == "Auto"
    assert parent["path"] == "/Auto"
    assert parent["parent_id"] is None

    r = create(client_a, ws_a.id, {"name": "APK", "parent_id": parent["id"]})
    assert r.status_code == 201
    assert r.json()["path"] == "/Auto/APK"
    assert r.json()["parent_id"] == parent["id"]

    r = client_a.get(folders_url(ws_a.id))
    assert r.status_code == 200
    assert [f["path"] for f in r.json()] == ["/Auto", "/Auto/APK"]


def test_created_folder_belongs_to_the_workspace(client_a: Client, ws_a: Workspace) -> None:
    create(client_a, ws_a.id, {"name": "Auto"})

    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Folder.objects.get().workspace_id == ws_a.id


def test_duplicate_folder_is_409(client_a: Client, ws_a: Workspace) -> None:
    assert create(client_a, ws_a.id, {"name": "Auto"}).status_code == 201

    r = create(client_a, ws_a.id, {"name": " Auto "})

    assert r.status_code == 409
    assert r.json()["code"] == "folder_exists"


@pytest.mark.parametrize("name", ["", "   ", "Auto/APK"])
def test_invalid_folder_name_is_422(client_a: Client, ws_a: Workspace, name: str) -> None:
    r = create(client_a, ws_a.id, {"name": name})

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_unknown_parent_is_404(client_a: Client, ws_a: Workspace) -> None:
    r = create(client_a, ws_a.id, {"name": "APK", "parent_id": str(uuid.uuid4())})

    assert r.status_code == 404
    assert r.json()["code"] == "folder_not_found"


def test_parent_from_another_workspace_is_404(
    client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    with workspace_context(ws_b.id, actor=SYSTEM):
        other = Folder.objects.create(workspace=ws_b, name="Geheim")

    r = create(client_a, ws_a.id, {"name": "APK", "parent_id": str(other.id)})

    assert r.status_code == 404
    assert r.json()["code"] == "folder_not_found"


def test_other_workspace_looks_exactly_like_a_missing_one(  # Review Focus 3
    client_a: Client, ws_b: Workspace
) -> None:
    with workspace_context(ws_b.id, actor=SYSTEM):
        Folder.objects.create(workspace=ws_b, name="Geheim")
    unknown = uuid.uuid4()

    for response_pair in (
        (client_a.get(folders_url(ws_b.id)), client_a.get(folders_url(unknown))),
        (create(client_a, ws_b.id, {"name": "X"}), create(client_a, unknown, {"name": "X"})),
    ):
        other, missing = response_pair
        assert other.status_code == missing.status_code == 404
        assert other.json() == missing.json()
        assert other.json()["code"] == "workspace_not_found"
    with workspace_context(ws_b.id, actor=SYSTEM):
        assert list(Folder.objects.values_list("name", flat=True)) == ["Geheim"]


def test_viewer_may_list_but_not_create(client: Client, ws_a: Workspace) -> None:
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    client.force_login(viewer)

    assert client.get(folders_url(ws_a.id)).status_code == 200
    r = create(client, ws_a.id, {"name": "Auto"})

    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Folder.objects.count() == 0


def test_member_may_create(client: Client, ws_a: Workspace) -> None:
    member = UserFactory.create()
    Membership.objects.create(user=member, workspace=ws_a, role="member")
    client.force_login(member)

    assert create(client, ws_a.id, {"name": "Auto"}).status_code == 201


@pytest.mark.django_db(transaction=True)
def test_workspace_setting_ends_with_the_request(client_a: Client, ws_a: Workspace) -> None:
    # A real request transaction (no test transaction around it): once it
    # commits, the same connection no longer carries the workspace.
    assert client_a.get(folders_url(ws_a.id)).status_code == 200

    with connection.cursor() as c:
        c.execute("SELECT current_setting('brievie.workspace_id', true)")
        row = c.fetchone()
    assert row is not None
    assert row[0] in (None, "")
    assert Folder.objects.count() == 0
