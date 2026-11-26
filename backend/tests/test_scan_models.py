"""The scan folder's tables, settings and the `intake` service (intake plan Task 9)."""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

from apps.documents.models import DocumentEvent
from apps.documents.organise import AUTOMATIC_ORIGINS, UNDOABLE_ORIGINS
from apps.intake.models import ScanFile, ScanFolder, ScanFolderSettings
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = (ROOT / "deploy" / "docker-compose.yml").read_text()
COMPOSE = (ROOT / "deploy" / "compose" / "docker-compose.yml").read_text()
ENV_EXAMPLE = (ROOT / "deploy" / ".env.example").read_text()


def service(text: str, name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|^\S|\Z)", text, re.M | re.S)
    assert match, name
    return match.group(1)


def test_defaults(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        stored = ScanFolderSettings.objects.create(workspace=ws_a)
        stored.refresh_from_db()
    assert stored.enabled is False
    assert stored.folder_name == ""
    assert stored.per_person is False
    assert stored.check_state == "unknown"
    assert (stored.owner_uid, stored.owner_gid) == (0, 0)
    assert stored.checked_at is None
    assert stored.unknown_folders == []


def test_folder_names_differ_ignoring_case(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        ScanFolder.objects.create(workspace=ws_a, name="Anne")
        with pytest.raises(IntegrityError), transaction.atomic():
            ScanFolder.objects.create(workspace=ws_a, name="anne")
        folder = ScanFolder.objects.get()
    assert folder.state == "active"
    assert folder.added_by_admin is False
    assert folder.person_id is None


def test_one_workspace_folder_name_per_server(ws_a: Workspace, ws_b: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        ScanFolderSettings.objects.create(workspace=ws_a, folder_name="kantoor")
    with (
        workspace_context(ws_b.id, actor=SYSTEM),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        ScanFolderSettings.objects.create(workspace=ws_b, folder_name="kantoor")


def test_two_empty_folder_names_are_allowed(ws_a: Workspace, ws_b: Workspace) -> None:
    for workspace in (ws_a, ws_b):
        with workspace_context(workspace.id, actor=SYSTEM):
            ScanFolderSettings.objects.create(workspace=workspace)


def test_one_settings_row_per_workspace(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        ScanFolderSettings.objects.create(workspace=ws_a)
        with pytest.raises(IntegrityError), transaction.atomic():
            ScanFolderSettings.objects.create(workspace=ws_a)


def test_a_file_is_one_row_per_path_and_inode(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        fields = {
            "workspace": ws_a,
            "relative_path": "kantoor/brief.pdf",
            "size": 10,
            "mtime_ns": 1,
            "device": 2,
            "inode": 3,
            "stable_since": timezone.now(),
        }
        row = ScanFile.objects.create(**fields)
        assert row.state == "seen"
        assert row.reason == ""
        with pytest.raises(IntegrityError), transaction.atomic():
            ScanFile.objects.create(**fields)


@pytest.mark.parametrize(
    "table", ["intake_scanfoldersettings", "intake_scanfolder", "intake_scanfile"]
)
def test_rls_is_forced(table: str) -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            [table],
        )
        assert c.fetchone() == (True, True)


def test_rows_stay_in_their_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        ScanFolder.objects.create(workspace=ws_a, name="anne")
    with workspace_context(ws_b.id, actor=SYSTEM):
        assert ScanFolder.objects.count() == 0
        with pytest.raises(DatabaseError), transaction.atomic():
            ScanFolder.objects.create(workspace=ws_a, name="stiekem")


def test_watch_folder_is_an_automatic_undoable_origin() -> None:
    assert DocumentEvent.Origin.WATCH_FOLDER.value == "watch_folder"
    assert DocumentEvent.Origin.WATCH_FOLDER in AUTOMATIC_ORIGINS
    assert DocumentEvent.Origin.WATCH_FOLDER in UNDOABLE_ORIGINS


def test_scan_settings_defaults() -> None:
    assert settings.BRIEVIE_SCAN_ROOT == "/scan"
    assert settings.BRIEVIE_SCAN_DIR_SET is False
    assert settings.BRIEVIE_SCAN_STABLE_SECONDS == 60


@pytest.mark.parametrize("text", [WRAPPER, COMPOSE])
def test_only_intake_mounts_the_scan_folder(text: str) -> None:
    mount = "${BRIEVIE_SCAN_DIR:-scan-unset}:/scan"
    assert mount in service(text, "intake")
    assert text.count(":/scan") == 1
    for name in ("api", "worker", "keeper"):
        assert "/scan" not in service(text, name), name
    assert "\n  scan-unset:\n" in text.split("\nvolumes:\n", 1)[1]


@pytest.mark.parametrize("text", [WRAPPER, COMPOSE])
def test_intake_serves_its_queue_without_a_port(text: str) -> None:
    block = service(text, "intake")
    assert "ports:" not in block
    assert 'group_add: ["${BRIEVIE_SCAN_GID:-100}"]' in block
    assert "check_master_key && exec python manage.py procrastinate worker --queues intake" in (
        block
    )
    assert "BRIEVIE_SCAN_DIR_SET: ${BRIEVIE_SCAN_DIR:+true}" in block
    assert "documents:/data/documents" in block
    assert "--queues intake" not in service(text, "worker")


@pytest.mark.parametrize("text", [WRAPPER, COMPOSE])
def test_intake_is_locked_down(text: str) -> None:
    block = service(text, "intake")
    assert "cap_drop: [ALL]" in block
    assert 'security_opt: ["no-new-privileges:true"]' in block
    assert "read_only: true" in block
    # The parser child writes its answer to a temporary file.
    assert "/tmp" in block.split("tmpfs:", 1)[1]  # noqa: S108 - a text check


@pytest.mark.parametrize("text", [WRAPPER, COMPOSE])
def test_the_api_knows_whether_there_is_a_scan_folder(text: str) -> None:
    # The Scanmap page is offered only when the server has a folder; the api answers it.
    assert "BRIEVIE_SCAN_DIR_SET: ${BRIEVIE_SCAN_DIR:+true}" in service(text, "api")


def test_intake_reaches_only_the_database() -> None:
    # No way out to the internet: a parser taken over by a crafted file cannot send it.
    assert "networks: [db]" in service(COMPOSE, "intake")
    assert "networks: [scan]" in service(WRAPPER, "intake")
    assert re.search(r"^  scan:\n    internal: true\n", WRAPPER.split("\nnetworks:\n", 1)[1], re.M)
    assert "networks: [default, scan]" in service(WRAPPER, "postgres")


def test_env_example_names_the_scan_folder() -> None:
    assert "# BRIEVIE_SCAN_DIR=" in ENV_EXAMPLE
    assert "# BRIEVIE_SCAN_GID=100" in ENV_EXAMPLE
