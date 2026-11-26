"""The Scanmap page's service and endpoints (intake plan Task 13)."""

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.audit.models import WorkspaceAuditEvent
from apps.intake.models import ScanFile, ScanFolder, ScanFolderSettings
from apps.intake.scan_settings import scan_folder_status
from apps.people.models import Person
from apps.people.services import ensure_person_for_member
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, bearer_for
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.visibility_support import set_state

if TYPE_CHECKING:
    from tests.helpers import Response


@pytest.fixture(autouse=True)
def share(settings: Settings, tmp_path: Path) -> Path:
    root = tmp_path / "scan"
    root.mkdir()
    settings.BRIEVIE_SCAN_ROOT = str(root)
    settings.BRIEVIE_SCAN_DIR_SET = True
    return root


def path(workspace: Workspace, rest: str = "") -> str:
    return f"/api/v1/workspaces/{workspace.id}/scan-folder{rest}"


def put(client: Client, workspace: Workspace, body: dict[str, object]) -> "Response":
    return client.put(path(workspace), body, content_type="application/json")


@pytest.fixture
def admin(client: Client, ws_b: Workspace) -> Client:
    client.force_login(admin_of(ws_b))
    return client


def audit_rows(workspace: Workspace) -> list[WorkspaceAuditEvent]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return list(
            WorkspaceAuditEvent.objects.filter(code="workspace_setting_changed").order_by(
                "created_at"
            )
        )


def stored(workspace: Workspace) -> ScanFolderSettings:
    with workspace_context(workspace.id, actor=SYSTEM):
        return ScanFolderSettings.objects.get()


def member_of(workspace: Workspace, role: str = Membership.Role.MEMBER) -> Membership:
    user = UserFactory.create(name="Lid")
    return Membership.objects.create(user=user, workspace=workspace, role=role)


def test_a_member_may_not(client: Client, ws_b: Workspace) -> None:
    client.force_login(member_of(ws_b).user)
    assert client.get(path(ws_b)).status_code == 403
    assert put(client, ws_b, {"enabled": True}).status_code == 403
    url = f"/workspaces/{ws_b.id}/scan-folder/folders"
    assert post_json(client, url, {"person_id": str(ws_b.id)}).status_code == 403
    assert client.delete(path(ws_b, f"/folders/{ws_b.id}")).status_code == 403


def test_a_device_token_gets_401(client: Client, ws_b: Workspace) -> None:
    headers = bearer_for(admin_of(ws_b))
    assert client.get(path(ws_b), headers=headers).status_code == 401
    response = client.put(
        path(ws_b), {"enabled": True}, content_type="application/json", headers=headers
    )
    assert response.status_code == 401
    folders = client.post(
        path(ws_b, "/folders"),
        {"person_id": str(ws_b.id)},
        content_type="application/json",
        headers=headers,
    )
    assert folders.status_code == 401
    assert client.delete(path(ws_b, f"/folders/{ws_b.id}"), headers=headers).status_code == 401


def test_a_server_without_a_folder(admin: Client, ws_b: Workspace, settings: Settings) -> None:
    settings.BRIEVIE_SCAN_DIR_SET = False
    body = admin.get(path(ws_b)).json()
    assert body["available"] is False
    assert body["suggested_name"] == "bedrijf"
    response = put(admin, ws_b, {"enabled": True})
    assert response.status_code == 409
    assert response.json()["code"] == "scan_folder_unavailable"


def test_switching_on_with_the_suggested_name(admin: Client, ws_b: Workspace) -> None:
    response = put(admin, ws_b, {"enabled": True})
    assert response.status_code == 200
    body = response.json()
    assert (body["enabled"], body["folder_name"], body["check"]) == (True, "bedrijf", "unknown")
    [row] = audit_rows(ws_b)
    assert row.values == {"setting": "scan_folder", "value": True}
    assert row.actor_id == admin_of(ws_b).id


def test_nothing_changed_writes_no_row_and_the_name_is_never_in_one(
    admin: Client, ws_b: Workspace
) -> None:
    put(admin, ws_b, {"enabled": True, "folder_name": "post-kantoor"})
    put(admin, ws_b, {"enabled": True, "folder_name": "post-kantoor"})
    put(admin, ws_b, {"enabled": True, "folder_name": "andere-naam"})
    put(admin, ws_b, {"enabled": True, "folder_name": "andere-naam", "per_person": True})
    rows = audit_rows(ws_b)
    assert [r.values for r in rows] == [
        {"setting": "scan_folder", "value": True},
        {"setting": "scan_folder_per_person", "value": True},
    ]
    for row in rows:
        assert "kantoor" not in str(row.values)
        assert "andere-naam" not in str(row.values)
        assert "naam" not in row.target_name


def test_a_name_another_workspace_uses_is_taken(
    admin: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        ScanFolderSettings.objects.create(workspace=ws_a, folder_name="kantoor")
    response = put(admin, ws_b, {"enabled": True, "folder_name": "kantoor"})
    assert response.status_code == 409
    assert response.json()["code"] == "scan_folder_taken"


@pytest.mark.parametrize("name", ["Kantoor", "kantoor den haag", "mislukt", "a" * 61])
def test_a_bad_name_is_422(admin: Client, ws_b: Workspace, name: str) -> None:
    response = put(admin, ws_b, {"enabled": True, "folder_name": name})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def _person(workspace: Workspace, name: str, *, kind: str = Person.Kind.PERSON) -> Person:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Person.objects.create(workspace=workspace, display_name=name, kind=kind)


def test_a_folder_for_a_person_without_an_account(admin: Client, ws_b: Workspace) -> None:
    put(admin, ws_b, {"enabled": True, "per_person": True})
    stagiair = _person(ws_b, "Stagiair")
    response = post_json(
        admin, f"/workspaces/{ws_b.id}/scan-folder/folders", {"person_id": str(stagiair.id)}
    )
    assert response.status_code == 200
    [folder] = response.json()["folders"]
    assert (folder["name"], folder["person_name"], folder["state"]) == (
        "Stagiair",
        "Stagiair",
        "active",
    )
    with workspace_context(ws_b.id, actor=SYSTEM):
        assert ScanFolder.objects.get().added_by_admin is True
    assert audit_rows(ws_b)[-1].values == {"setting": "scan_folder_person", "value": "added"}
    assert "Stagiair" not in str(audit_rows(ws_b)[-1].values)


def test_linking_an_unknown_folder(admin: Client, ws_b: Workspace) -> None:
    put(admin, ws_b, {"enabled": True, "per_person": True})
    with workspace_context(ws_b.id, actor=SYSTEM):
        ScanFolderSettings.objects.update(unknown_folders=[{"name": "Oude scans", "files": 2}])
    archief = _person(ws_b, "Archief")
    assert admin.get(path(ws_b)).json()["unknown_folders"] == [{"name": "Oude scans", "files": 2}]
    response = post_json(
        admin,
        f"/workspaces/{ws_b.id}/scan-folder/folders",
        {"person_id": str(archief.id), "name": "Oude scans"},
    )
    assert response.status_code == 200
    assert [f["name"] for f in response.json()["folders"]] == ["Oude scans"]


def test_a_name_a_department_has_is_taken(admin: Client, ws_b: Workspace) -> None:
    set_state(ws_b, e=True, d=True, a=True)
    put(admin, ws_b, {"enabled": True, "per_person": True})
    department = _person(ws_b, "Financien", kind=Person.Kind.DEPARTMENT)
    stagiair = _person(ws_b, "Stagiair")
    url = f"/workspaces/{ws_b.id}/scan-folder/folders"
    assert post_json(admin, url, {"person_id": str(department.id)}).status_code == 200
    response = post_json(admin, url, {"person_id": str(stagiair.id), "name": "financien"})
    assert response.status_code == 409
    assert response.json()["code"] == "scan_subfolder_taken"


def test_names_that_fold_to_the_same_are_taken(admin: Client, ws_b: Workspace) -> None:
    put(admin, ws_b, {"enabled": True, "per_person": True})
    first = _person(ws_b, "Straatkant")
    second = _person(ws_b, "Tweede")
    url = f"/workspaces/{ws_b.id}/scan-folder/folders"
    assert post_json(admin, url, {"person_id": str(first.id), "name": "Straße"}).status_code == 200
    response = post_json(admin, url, {"person_id": str(second.id), "name": "STRASSE"})
    assert response.status_code == 409
    assert response.json()["code"] == "scan_subfolder_taken"


def test_a_department_while_departments_are_off(admin: Client, ws_b: Workspace) -> None:
    set_state(ws_b, e=True, d=False, a=True)
    department = _person(ws_b, "Financien", kind=Person.Kind.DEPARTMENT)
    response = post_json(
        admin, f"/workspaces/{ws_b.id}/scan-folder/folders", {"person_id": str(department.id)}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "scan_folder_departments_off"


def test_an_unknown_person_is_404(admin: Client, ws_a: Workspace, ws_b: Workspace) -> None:
    elsewhere = _person(ws_a, "Elders")
    response = post_json(
        admin, f"/workspaces/{ws_b.id}/scan-folder/folders", {"person_id": str(elsewhere.id)}
    )
    assert response.status_code == 404
    assert response.json()["code"] == "person_not_found"


def test_stopping_a_folder_leaves_the_share(admin: Client, ws_b: Workspace, share: Path) -> None:
    put(admin, ws_b, {"enabled": True, "per_person": True})
    stagiair = _person(ws_b, "Stagiair")
    body = post_json(
        admin, f"/workspaces/{ws_b.id}/scan-folder/folders", {"person_id": str(stagiair.id)}
    ).json()
    folder_id = body["folders"][0]["id"]
    on_disk = share / "bedrijf" / "Stagiair"
    on_disk.mkdir(parents=True)
    (on_disk / "brief.pdf").write_bytes(b"%PDF-1.4")
    response = admin.delete(path(ws_b, f"/folders/{folder_id}"))
    assert response.status_code == 200
    assert response.json()["folders"][0]["state"] == "stopped"
    assert (on_disk / "brief.pdf").exists()
    assert audit_rows(ws_b)[-1].values == {"setting": "scan_folder_person", "value": "removed"}
    assert admin.delete(path(ws_b, f"/folders/{ws_b.id}")).status_code == 404


def test_the_check_is_unknown_without_a_recent_look(ws_b: Workspace) -> None:
    now = timezone.now()
    with workspace_context(ws_b.id, actor=SYSTEM):
        ScanFolderSettings.objects.create(
            workspace=ws_b,
            enabled=True,
            folder_name="bedrijf",
            check_state="ok",
            checked_at=now - timedelta(minutes=6),
        )
        assert scan_folder_status(ws_b.id, now=now).check == "unknown"
        assert scan_folder_status(ws_b.id, now=now - timedelta(minutes=2)).check == "ok"


def test_waiting_and_failed_come_from_the_rows(ws_b: Workspace) -> None:
    now = timezone.now()
    with workspace_context(ws_b.id, actor=SYSTEM):
        ScanFolderSettings.objects.create(workspace=ws_b, enabled=True, folder_name="bedrijf")
        common = {"workspace": ws_b, "size": 1, "mtime_ns": 1, "device": 1, "stable_since": now}
        ScanFile.objects.create(relative_path="bedrijf/een.pdf", inode=1, **common)
        ScanFile.objects.create(relative_path="bedrijf/twee.pdf", inode=2, **common)
        ScanFile.objects.create(
            relative_path="bedrijf/mislukt/drie.pdf",
            inode=3,
            state="failed",
            reason="file_encrypted",
            **common,
        )
        status = scan_folder_status(ws_b.id, now=now + timedelta(minutes=90))
    assert status.waiting_count == 2
    assert status.oldest_waiting_minutes >= 89
    assert [(f.name, f.reason) for f in status.failed] == [("drie.pdf", "file_encrypted")]


def test_a_person_folders_waiting_count(ws_b: Workspace) -> None:
    now = timezone.now()
    member = member_of(ws_b)
    with workspace_context(ws_b.id, actor=SYSTEM):
        person = ensure_person_for_member(workspace=ws_b, user=member.user)
        ScanFolderSettings.objects.create(
            workspace=ws_b, enabled=True, folder_name="bedrijf", per_person=True
        )
        ScanFolder.objects.create(workspace=ws_b, person=person, name="Lid")
        ScanFile.objects.create(
            workspace=ws_b,
            relative_path="bedrijf/Lid/brief.pdf",
            size=1,
            mtime_ns=1,
            device=1,
            inode=1,
            stable_since=now,
        )
        status = scan_folder_status(ws_b.id, now=now)
    [folder] = status.folders
    assert (folder.name, folder.kind, folder.waiting_count) == ("Lid", "person", 1)
