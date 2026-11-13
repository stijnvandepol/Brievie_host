import pytest
from django.db import DatabaseError, connection

from apps.documents.models import Folder
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory


def current_workspace_setting() -> str | None:
    with connection.cursor() as c:
        c.execute("SELECT current_setting('brievie.workspace_id', true)")
        row = c.fetchone()
    assert row is not None
    value: str | None = row[0]
    return value


def test_folder_visible_only_inside_its_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    with workspace_context(ws_a.id):
        Folder.objects.create(workspace=ws_a, name="Auto")
    with workspace_context(ws_b.id):
        assert Folder.objects.count() == 0
    with workspace_context(ws_a.id):
        assert Folder.objects.count() == 1


def test_no_context_sees_zero_rows(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        Folder.objects.create(workspace=ws_a, name="Auto")
    assert Folder.objects.count() == 0


def test_cannot_insert_into_other_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    with workspace_context(ws_a.id), pytest.raises(DatabaseError):
        Folder.objects.create(workspace=ws_b, name="Stiekem")


def test_cannot_insert_without_context(ws_a: Workspace) -> None:
    with pytest.raises(DatabaseError):
        Folder.objects.create(workspace=ws_a, name="Zonder context")


def test_setting_does_not_leak_after_context(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        pass
    assert current_workspace_setting() in (None, "")


def test_setting_is_restored_after_an_exception(ws_a: Workspace) -> None:
    with pytest.raises(RuntimeError), workspace_context(ws_a.id):
        raise RuntimeError
    assert current_workspace_setting() in (None, "")


def test_setting_is_restored_after_a_failed_query_inside_the_context(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    with workspace_context(ws_a.id), pytest.raises(DatabaseError):
        Folder.objects.create(workspace=ws_b, name="Stiekem")
    assert current_workspace_setting() in (None, "")


def test_nested_contexts_unwind_to_the_outer_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    with workspace_context(ws_a.id):
        with workspace_context(ws_b.id):
            assert current_workspace_setting() == str(ws_b.id)
        assert current_workspace_setting() == str(ws_a.id)
    assert current_workspace_setting() in (None, "")


def test_folder_path_is_built_from_parent(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        auto = Folder.objects.create(workspace=ws_a, name="Auto")
        apk = Folder.objects.create(workspace=ws_a, parent=auto, name="APK")
    assert auto.path == "/Auto"
    assert apk.path == "/Auto/APK"


def test_folder_parent_must_be_in_the_same_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    with workspace_context(ws_a.id):
        auto = Folder.objects.create(workspace=ws_a, name="Auto")
    with workspace_context(ws_b.id), pytest.raises(ValueError, match="workspace"):
        Folder.objects.create(workspace=ws_b, parent=auto, name="APK")


def test_folder_path_is_unique_per_workspace(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        Folder.objects.create(workspace=ws_a, name="Auto")
        with pytest.raises(DatabaseError):
            Folder.objects.create(workspace=ws_a, name="Auto")


def test_create_workspace_makes_the_creator_admin() -> None:
    user = UserFactory.create()
    workspace = create_workspace(name="Thuis", kind="home", admin=user)
    membership = Membership.objects.get(workspace=workspace)
    assert membership.user == user
    assert membership.role == "admin"
    assert workspace.kind == "home"
    assert workspace.modules == {}


def test_create_workspace_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        create_workspace(name="Iets", kind="hobby", admin=UserFactory.create())
    assert Workspace.objects.count() == 0
