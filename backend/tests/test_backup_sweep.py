import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from django.conf import settings as django_settings
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import Client
from django.utils import timezone
from psycopg import errors as pg_errors

from apps.backup import sweep as sweep_module
from apps.backup.errors import REASONS, BackupError
from apps.backup.locks import BACKUP_LOCK_KEY
from apps.backup.sweep import Orphan, delete_orphans, find_orphans, sweep
from apps.documents.models import DocumentPage
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.backup_support import Recorder
from tests.documents_support import admin_of, jpeg, upload, upload_file
from tests.intake_fixtures import make_pdf

pytestmark = pytest.mark.django_db(transaction=True)


def storage_root() -> Path:
    return Path(django_settings.BRIEVIE_STORAGE_ROOT)


def upload_letter(workspace: Workspace) -> None:
    client = Client()
    client.force_login(admin_of(workspace))
    response = upload(client, workspace.id, [jpeg(f"sweep-{workspace.id}")])
    assert response.status_code == 201


def page_file(workspace: Workspace) -> Path:
    with workspace_context(workspace.id, actor=SYSTEM):
        page = DocumentPage.objects.get()
    return storage_root() / page.storage_path


def age(path: Path, delta: timedelta) -> None:
    moment = (datetime.now(UTC) - delta).timestamp()
    os.utime(path, (moment, moment), follow_symlinks=False)


def stray(workspace: Workspace, name: str = "1.page", *, old: bool = True) -> Path:
    """A file in the document layout that no row refers to."""
    folder = storage_root() / str(workspace.id) / str(uuid.uuid4())
    folder.mkdir(parents=True)
    path = folder / name
    path.write_bytes(b"sealed")
    age(path, timedelta(days=2) if old else timedelta(hours=1))
    return path


def relative(path: Path) -> str:
    return path.relative_to(storage_root()).as_posix()


def found(conninfo: str) -> set[str]:
    return {
        o.path
        for o in find_orphans(conninfo=conninfo, storage_root=storage_root(), now=timezone.now())
    }


def test_sweep_keeps_the_pages_of_every_workspace(
    ws_a: Workspace, ws_b: Workspace, backup_conninfo: str
) -> None:
    upload_letter(ws_a)
    upload_letter(ws_b)
    extra_page = stray(ws_a)
    extra_temp = stray(ws_a, ".tmp-x")

    assert found(backup_conninfo) == {relative(extra_page), relative(extra_temp)}
    assert page_file(ws_b).exists()


def upload_original(workspace: Workspace) -> Path:
    """A browser's file upload: its sealed original, `<ws>/<doc>/original`."""
    client = Client()
    client.force_login(admin_of(workspace))
    response = upload_file(client, workspace.id, make_pdf([f"Brief {workspace.id}"]))
    assert response.status_code == 201, response.content
    return storage_root() / str(workspace.id) / str(response.json()["id"]) / "original"


def test_sweep_keeps_a_live_original_and_removes_an_orphan_one(
    ws_a: Workspace, backup_conninfo: str
) -> None:
    # Security review of PR #58: an original left behind (a request that failed after
    # writing it, or an old purge) is user data the sweep must find; a live one stays.
    upload_letter(ws_a)
    live = upload_original(ws_a)
    age(live, timedelta(days=2))
    orphan = stray(ws_a, "original")

    assert found(backup_conninfo) == {relative(orphan)}

    done = sweep(delete=True)
    assert done.deleted == 1
    assert not orphan.exists()
    assert live.exists()


def test_young_orphans_are_left_alone(ws_a: Workspace, backup_conninfo: str) -> None:
    upload_letter(ws_a)
    stray(ws_a, old=False)

    assert found(backup_conninfo) == set()


def test_sweep_lists_but_deletes_only_when_asked(ws_a: Workspace, backup_conninfo: str) -> None:
    upload_letter(ws_a)
    extra = stray(ws_a)

    listed = sweep(delete=False)
    assert [o.path for o in listed.orphans] == [relative(extra)]
    assert listed.deleted == 0
    assert extra.exists()

    done = sweep(delete=True)
    assert done.deleted == 1
    assert not extra.exists()
    assert page_file(ws_a).exists()


def test_sweep_as_the_app_role_is_impossible(ws_a: Workspace) -> None:
    upload_letter(ws_a)
    stray(ws_a)
    settings_dict = connection.settings_dict
    owner = (
        f"dbname={settings_dict['NAME']} user={settings_dict['USER']} "
        f"password={settings_dict['PASSWORD']} host={settings_dict['HOST']} "
        f"port={settings_dict['PORT']}"
    )

    with pytest.raises(pg_errors.InsufficientPrivilege):
        find_orphans(conninfo=owner, storage_root=storage_root(), now=timezone.now())


def test_symlinks_are_never_followed(ws_a: Workspace, backup_conninfo: str, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "1.page"
    target.write_bytes(b"not ours")
    age(target, timedelta(days=2))
    workspace_dir = storage_root() / str(ws_a.id)
    # A symlinked file, a symlinked document directory and a symlinked workspace directory.
    (workspace_dir / str(uuid.uuid4())).mkdir(parents=True)
    link_file = next(workspace_dir.iterdir()) / "2.page"
    link_file.symlink_to(target)
    (workspace_dir / str(uuid.uuid4())).symlink_to(outside, target_is_directory=True)
    (storage_root() / str(uuid.uuid4())).symlink_to(outside, target_is_directory=True)
    age(link_file, timedelta(days=2))

    assert found(backup_conninfo) == set()
    delete_orphans(
        [Orphan(relative(link_file), 1, datetime.now(UTC) - timedelta(days=2))], storage_root()
    )
    assert link_file.is_symlink()
    assert target.exists()


def test_only_the_layout_is_a_candidate(ws_a: Workspace, backup_conninfo: str) -> None:
    root = storage_root()
    root.mkdir(parents=True, exist_ok=True)
    document_dir = root / str(ws_a.id) / str(uuid.uuid4())
    document_dir.mkdir(parents=True)
    odd = [
        document_dir / "..page",
        document_dir / ".tmp-..",
        document_dir / "notes.txt",
        document_dir / "1.page.bak",
        document_dir / "x.page",
        document_dir / "original.pdf",
        document_dir / "Original",
        root / "1.page",
        root / str(ws_a.id) / "1.page",
        root / "not-a-uuid" / str(uuid.uuid4()) / "1.page",
        root / str(ws_a.id) / "not-a-uuid" / "1.page",
        root / str(ws_a.id) / str(uuid.uuid4()) / "deeper" / "1.page",
    ]
    for path in odd:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        age(path, timedelta(days=2))
    directory = document_dir / "2.page"
    directory.mkdir()
    age(directory, timedelta(days=2))

    assert found(backup_conninfo) == set()
    delete_orphans([Orphan("2.page", 0, datetime.now(UTC) - timedelta(days=2))], root)
    assert all(path.exists() for path in odd)
    assert directory.is_dir()


def test_deleting_never_leaves_the_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.page"
    outside.write_bytes(b"x")
    age(outside, timedelta(days=2))
    old = datetime.now(UTC) - timedelta(days=2)
    escapes = [
        Orphan("../outside.page", 1, old),
        Orphan(f"{uuid.uuid4()}/{uuid.uuid4()}/../../../outside.page", 1, old),
        Orphan(str(outside), 1, old),
    ]
    storage_root().mkdir(parents=True, exist_ok=True)

    assert delete_orphans(escapes, storage_root()) == 0
    assert outside.exists()


def test_deleting_checks_the_age_again(ws_a: Workspace, backup_conninfo: str) -> None:
    upload_letter(ws_a)
    extra = stray(ws_a)
    orphans = find_orphans(
        conninfo=backup_conninfo, storage_root=storage_root(), now=timezone.now()
    )
    assert len(orphans) == 1
    extra.write_bytes(b"rewritten just now")  # the file was written again after the listing

    assert delete_orphans(orphans, storage_root()) == 0
    assert extra.exists()


def test_a_path_stored_in_another_form_still_counts_as_known(
    ws_a: Workspace, backup_conninfo: str
) -> None:
    upload_letter(ws_a)
    extra = stray(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        page = DocumentPage.objects.get()
        DocumentPage.objects.filter(pk=page.pk).update(
            storage_path=str(storage_root() / page.storage_path)
        )

    assert found(backup_conninfo) == {relative(extra)}


def test_a_path_form_nobody_matches_is_refused(ws_a: Workspace, backup_conninfo: str) -> None:
    upload_letter(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentPage.objects.update(storage_path="elsewhere/1.page")

    with pytest.raises(BackupError) as raised:
        found(backup_conninfo)
    assert raised.value.code == "internal_error"


def test_an_empty_database_with_page_files_is_refused(
    ws_a: Workspace, backup_conninfo: str
) -> None:
    extra = stray(ws_a)

    with pytest.raises(BackupError) as raised:
        found(backup_conninfo)
    assert raised.value.code == "internal_error"
    with pytest.raises(BackupError):
        sweep(delete=True)
    assert extra.exists()


def test_an_empty_database_with_only_originals_is_refused(
    ws_a: Workspace, backup_conninfo: str
) -> None:
    # A store of uploaded files only, read against an empty or wrong database, must not
    # list every original as an orphan (security review of PR #58).
    extra = stray(ws_a, "original")

    with pytest.raises(BackupError) as raised:
        found(backup_conninfo)
    assert raised.value.code == "internal_error"
    with pytest.raises(BackupError):
        sweep(delete=True)
    assert extra.exists()


def test_a_database_with_pages_but_no_originals_does_not_orphan_every_original(
    ws_a: Workspace, backup_conninfo: str
) -> None:
    # Re-review of PR #58, point 4: the refusal held for pages and originals together, so a
    # database that knows pages but no originals (a wrong or older database) listed every
    # original on disk as an orphan. Each kind now needs a known file of its own.
    upload_letter(ws_a)
    extra = stray(ws_a, "original")

    with pytest.raises(BackupError) as raised:
        found(backup_conninfo)
    assert raised.value.code == "internal_error"
    with pytest.raises(BackupError):
        sweep(delete=True)
    assert extra.exists()
    assert page_file(ws_a).exists()


def test_a_database_with_originals_but_no_pages_does_not_orphan_every_page(
    ws_a: Workspace, backup_conninfo: str
) -> None:
    live = upload_original(ws_a)
    age(live, timedelta(days=2))
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentPage.objects.all().delete()
    extra = stray(ws_a)

    with pytest.raises(BackupError) as raised:
        found(backup_conninfo)
    assert raised.value.code == "internal_error"
    assert extra.exists()


def test_an_empty_database_still_lists_old_temp_files(
    ws_a: Workspace, backup_conninfo: str
) -> None:
    extra = stray(ws_a, ".tmp-x")

    assert found(backup_conninfo) == {relative(extra)}


def test_a_symlinked_storage_root_is_never_listed_nor_deleted_from(
    ws_a: Workspace,
    backup_conninfo: str,
    settings: object,
    tmp_path: Path,
    recorder: Recorder,
) -> None:
    # Round 4: only a root that passed the guard is ever listed (was: listed, not deleted).
    real = storage_root()
    link = tmp_path / "root-link"
    link.symlink_to(real, target_is_directory=True)
    upload_letter(ws_a)
    extra = stray(ws_a)
    orphans = [Orphan(relative(extra), 1, datetime.now(UTC) - timedelta(days=2))]
    settings.BRIEVIE_STORAGE_ROOT = str(link)  # type: ignore[attr-defined]

    for delete in (False, True):
        recorder.arm()  # listing included: nothing is read before the refusal
        with pytest.raises(BackupError) as caught:
            sweep(delete=delete)
        recorder.disarm()
        assert caught.value.code == "unsafe_path"
    recorder.arm()
    with pytest.raises(BackupError) as caught:
        delete_orphans(orphans, link)
    recorder.disarm()
    assert caught.value.code == "unsafe_path"
    assert extra.exists()


def test_the_files_are_listed_before_the_database_is_read(
    ws_a: Workspace, backup_conninfo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    upload_letter(ws_a)
    events: list[str] = []
    real_scan = sweep_module._scan_disk
    real_known = sweep_module._known_paths

    def scan(root: Path) -> object:
        events.append("disk")
        return real_scan(root)

    late: list[Path] = []

    def known(conninfo: str, root: Path) -> set[str]:
        events.append("database")
        # A letter that arrives between the two reads: its page is in the database
        # by now, and it is not a candidate because the files were listed already.
        upload_letter(ws_a)
        with workspace_context(ws_a.id, actor=SYSTEM):
            for page in DocumentPage.objects.all():
                late.append(storage_root() / page.storage_path)
        for path in late:
            age(path, timedelta(days=2))
        return real_known(conninfo, root)

    monkeypatch.setattr(sweep_module, "_scan_disk", scan)
    monkeypatch.setattr(sweep_module, "_known_paths", known)

    assert found(backup_conninfo) == set()
    assert events == ["disk", "database"]


def test_the_command_prints_numbers_and_no_paths(
    ws_a: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    upload_letter(ws_a)
    extra = stray(ws_a)

    call_command("sweep_storage")
    listed = capsys.readouterr().out
    assert str(ws_a.id) not in listed
    assert extra.name not in listed
    assert "1 losse bestanden gevonden (6\xa0bytes)" in listed
    assert extra.exists()

    call_command("sweep_storage", "--delete")
    assert not extra.exists()
    assert str(ws_a.id) not in capsys.readouterr().out


def test_the_command_says_so_when_nothing_is_left(
    ws_a: Workspace, capsys: pytest.CaptureFixture[str]
) -> None:
    upload_letter(ws_a)

    call_command("sweep_storage")

    assert "Geen losse bestanden" in capsys.readouterr().out


def test_the_sweep_refuses_while_a_backup_runs(ws_a: Workspace) -> None:
    other = psycopg.connect(
        f"dbname={connection.settings_dict['NAME']} user={connection.settings_dict['USER']} "
        f"password={connection.settings_dict['PASSWORD']} host={connection.settings_dict['HOST']} "
        f"port={connection.settings_dict['PORT']}",
        autocommit=True,
    )
    try:
        other.execute("SELECT pg_advisory_lock(%s)", [BACKUP_LOCK_KEY])
        with pytest.raises(BackupError) as raised:
            sweep(delete=False)
        assert raised.value.code == "busy"
    finally:
        other.close()


def test_one_unplaceable_stored_path_stops_everything(
    ws_a: Workspace, ws_b: Workspace, backup_conninfo: str
) -> None:
    upload_letter(ws_a)
    upload_letter(ws_b)
    victim = page_file(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentPage.objects.update(storage_path=f"/old-root/{relative(victim)}")
    age(victim, timedelta(days=2))

    with pytest.raises(BackupError) as raised:
        found(backup_conninfo)
    assert raised.value.code == "internal_error"
    with pytest.raises(BackupError):
        sweep(delete=True)
    assert victim.exists()
    assert page_file(ws_b).exists()


def test_a_non_ascii_digit_is_not_a_candidate(ws_a: Workspace, backup_conninfo: str) -> None:
    stray(ws_a, "\u0661.page")

    assert found(backup_conninfo) == set()


def test_a_directory_swapped_for_a_link_cannot_redirect_the_delete(
    ws_a: Workspace, backup_conninfo: str, tmp_path: Path
) -> None:
    upload_letter(ws_a)
    extra = stray(ws_a)
    orphans = find_orphans(
        conninfo=backup_conninfo, storage_root=storage_root(), now=timezone.now()
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / extra.name
    victim.write_bytes(b"not ours")
    age(victim, timedelta(days=2))
    directory = extra.parent
    extra.unlink()
    directory.rmdir()
    directory.symlink_to(outside, target_is_directory=True)

    assert delete_orphans(orphans, storage_root()) == 0
    assert victim.exists()


def test_database_errors_become_one_dutch_sentence(
    ws_a: Workspace, settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = dict(settings.DATABASE_BACKUP)  # type: ignore[attr-defined]
    bad["PASSWORD"] = "secret-wrong-password"
    bad["PORT"] = "1"
    settings.DATABASE_BACKUP = bad  # type: ignore[attr-defined]

    with pytest.raises(BackupError) as raised:
        sweep(delete=False)
    assert raised.value.code == "backup_role_missing"
    with pytest.raises(CommandError) as command_error:
        call_command("sweep_storage")
    message = str(command_error.value)
    assert message == str(REASONS["backup_role_missing"])
    assert "secret-wrong-password" not in message
    assert "host=" not in message
    assert command_error.value.returncode == 1
