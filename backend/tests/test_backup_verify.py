"""Checking a restored installation against its manifest (Task 10, D 4.6 step 8)."""

import dataclasses
import os
import subprocess
import uuid
from pathlib import Path
from typing import Literal

import psycopg
import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client
from pytest_django import Settings

from apps.backup.manifest import Manifest, build_manifest, manifest_to_json
from apps.backup.verify import VerifyReport, verify_restore
from apps.devices.pki import certificate_sha256
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, jpeg, upload

MASTER = bytes.fromhex("5a" * 32)  # the autouse fixture's BRIEVIE_MASTER_KEY
CA_FIXTURE = Path(__file__).parent / "fixtures" / "ca_root.crt"
NAMES = ("Thuis", "Bedrijf")


def upload_letter(workspace: Workspace, pages: int = 1) -> None:
    client = Client()
    client.force_login(admin_of(workspace))
    response = upload(
        client, workspace.id, [jpeg(f"p{n}-{workspace.id}-{uuid.uuid4()}") for n in range(pages)]
    )
    assert response.status_code == 201


def first_page(conninfo: str, workspace: Workspace) -> tuple[uuid.UUID, str]:
    """Document id and storage path of a workspace's first page, read past row security."""
    with psycopg.connect(conninfo) as conn:
        conn.execute("SET row_security = off")
        row = conn.execute(
            "SELECT document_id, storage_path FROM documents_documentpage"
            " WHERE workspace_id = %s ORDER BY document_id, index LIMIT 1",
            [workspace.id],
        ).fetchone()
    assert row is not None
    return row[0], row[1]


def storage_root() -> Path:
    return Path(django_settings.BRIEVIE_STORAGE_ROOT)


def authority_dir() -> Path:
    """Where the installation fixture puts its authority (next to the storage root)."""
    return storage_root().parent / "authority"


def manifest_now(conninfo: str, authority_dir: Path) -> Manifest:
    return build_manifest(
        run_id=uuid.uuid4(),
        conninfo=conninfo,
        storage_root=storage_root(),
        authority_dir=authority_dir,
    )


@pytest.fixture
def installation(
    ws_a: Workspace, ws_b: Workspace, backup_conninfo: str, tmp_path: Path
) -> Manifest:
    """Two workspaces with a letter each, an authority, and the manifest of that moment."""
    upload_letter(ws_a, pages=2)
    upload_letter(ws_b)
    authority = authority_dir()
    authority.mkdir()
    (authority / "root.crt").write_text(CA_FIXTURE.read_text())
    return manifest_now(backup_conninfo, authority)


def verify(
    conninfo: str,
    manifest: Manifest,
    *,
    master_key: bytes = MASTER,
    authority: Path | Literal["default"] | None = "default",
) -> VerifyReport:
    return verify_restore(
        conninfo=conninfo,
        storage_root=storage_root(),
        manifest=manifest,
        master_key=master_key,
        authority_dir=authority_dir() if authority == "default" else authority,
    )


def assert_plain(problem: str) -> None:
    """One sentence, ids only: no names of workspaces and no storage path."""
    assert problem.endswith(".")
    assert "\n" not in problem
    for name in NAMES:
        assert name not in problem
    assert str(storage_root()) not in problem


@pytest.mark.django_db(transaction=True)
def test_verify_passes_on_a_complete_installation(
    installation: Manifest, backup_conninfo: str, tmp_path: Path
) -> None:
    report = verify(backup_conninfo, installation, authority=tmp_path / "authority")
    assert report == VerifyReport(problems=[], workspaces=2, pages_opened=2)


@pytest.mark.django_db(transaction=True)
def test_verify_reports_a_missing_page_file(
    installation: Manifest, backup_conninfo: str, ws_b: Workspace
) -> None:
    document_id, storage_path = first_page(backup_conninfo, ws_b)
    (storage_root() / storage_path).unlink()

    report = verify(backup_conninfo, installation)

    (problem,) = report.problems
    assert str(document_id) in problem
    assert str(ws_b.id) in problem
    assert_plain(problem)
    assert report.pages_opened == 1


@pytest.mark.django_db(transaction=True)
def test_verify_reports_a_wrong_master_key(installation: Manifest, backup_conninfo: str) -> None:
    report = verify(backup_conninfo, installation, master_key=bytes.fromhex("00" * 32))
    assert len(report.problems) == 2
    for workspace_id in installation.workspaces:
        assert any(str(workspace_id) in problem for problem in report.problems)
    for problem in report.problems:
        assert_plain(problem)
    assert report.pages_opened == 0


@pytest.mark.django_db(transaction=True)
def test_verify_reports_a_page_that_does_not_open(
    installation: Manifest, backup_conninfo: str, ws_a: Workspace
) -> None:
    document_id, storage_path = first_page(backup_conninfo, ws_a)
    path = storage_root() / storage_path
    data = path.read_bytes()
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))

    report = verify(backup_conninfo, installation)

    (problem,) = report.problems
    assert str(document_id) in problem
    assert_plain(problem)
    assert report.pages_opened == 1


@pytest.mark.django_db(transaction=True)
def test_verify_reports_another_authority(
    installation: Manifest, backup_conninfo: str, tmp_path: Path
) -> None:
    other = dataclasses.replace(installation, authority_sha256="ab" * 32)
    (problem,) = verify(backup_conninfo, other, authority=tmp_path / "authority").problems
    assert_plain(problem)
    assert certificate_sha256(CA_FIXTURE.read_text()) not in problem


@pytest.mark.django_db(transaction=True)
def test_verify_reports_a_missing_authority(
    installation: Manifest, backup_conninfo: str, tmp_path: Path
) -> None:
    (problem,) = verify(backup_conninfo, installation, authority=tmp_path / "empty").problems
    assert_plain(problem)


@pytest.mark.django_db(transaction=True)
def test_verify_without_an_authority_in_the_backup_checks_none(
    installation: Manifest, backup_conninfo: str, tmp_path: Path
) -> None:
    public = dataclasses.replace(installation, authority_sha256=None)
    assert verify(backup_conninfo, public, authority=tmp_path / "empty").problems == []


@pytest.mark.django_db(transaction=True)
def test_verify_reports_counts_that_differ_from_the_manifest(
    installation: Manifest, backup_conninfo: str, ws_a: Workspace
) -> None:
    upload_letter(ws_a, pages=3)

    report = verify(backup_conninfo, installation)

    assert len(report.problems) == 2  # one document more, three pages more
    for problem in report.problems:
        assert str(ws_a.id) in problem
        assert_plain(problem)


@pytest.mark.django_db(transaction=True)
def test_verify_reports_a_workspace_of_the_manifest_that_is_missing(
    installation: Manifest, backup_conninfo: str
) -> None:
    missing = uuid.uuid4()
    counts = next(iter(installation.workspaces.values()))
    more = dataclasses.replace(
        installation, workspaces={**installation.workspaces, missing: counts}
    )

    (problem,) = verify(backup_conninfo, more).problems

    assert str(missing) in problem
    assert_plain(problem)


@pytest.mark.django_db(transaction=True)
def test_verify_reports_a_workspace_the_manifest_does_not_know(
    installation: Manifest, backup_conninfo: str, ws_b: Workspace
) -> None:
    fewer = dataclasses.replace(
        installation,
        workspaces={k: v for k, v in installation.workspaces.items() if k != ws_b.id},
    )
    report = verify(backup_conninfo, fewer)
    (problem,) = report.problems
    assert str(ws_b.id) in problem
    assert_plain(problem)


@pytest.mark.django_db(transaction=True)
def test_verify_reads_in_one_read_only_repeatable_read_transaction_without_row_security(
    installation: Manifest, backup_conninfo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[psycopg.Connection[tuple[object, ...]]] = []
    statements: list[str] = []
    real_connect = psycopg.connect

    def spy(conninfo: str) -> psycopg.Connection[tuple[object, ...]]:
        conn = real_connect(conninfo)
        opened.append(conn)
        real_execute = conn.execute

        def execute(query: str, *args: object, **kwargs: object) -> object:
            statements.append(query)
            return real_execute(query, *args, **kwargs)  # type: ignore[call-overload]

        conn.execute = execute  # type: ignore[assignment,method-assign]
        return conn

    monkeypatch.setattr("apps.backup.verify.psycopg.connect", spy)
    verify(backup_conninfo, installation)

    (conn,) = opened
    assert conn.isolation_level == psycopg.IsolationLevel.REPEATABLE_READ
    assert conn.read_only is True
    assert statements[0] == "SET row_security = off"


@pytest.mark.django_db(transaction=True)
def test_verify_as_a_role_bound_by_row_security_fails_loudly(
    installation: Manifest, owner_conninfo: str
) -> None:
    # Never a pass on a view of zero rows.
    from apps.backup.errors import BackupError

    with pytest.raises(BackupError) as caught:
        verify(owner_conninfo, installation)
    assert caught.value.code == "restore_failed"


@pytest.mark.django_db(transaction=True)
def test_verify_starts_no_process(
    installation: Manifest, backup_conninfo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The master key stays in this process: nothing is started, so no argv can hold it.
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a process was started")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    assert verify(backup_conninfo, installation).problems == []


# --- the command -----------------------------------------------------------------------


@pytest.fixture
def as_superuser(settings: Settings) -> str:
    """The back-up role stands in for the superuser (it reads past row security)."""
    backup = django_settings.DATABASE_BACKUP
    assert backup is not None
    settings.DATABASE_SUPERUSER = dict(backup)
    return str(connection.settings_dict["NAME"])


@pytest.mark.django_db(transaction=True)
def test_verify_restore_command_prints_the_counts(
    installation: Manifest,
    as_superuser: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest_to_json(installation))
    call_command(
        "verify_restore",
        "--dbname",
        as_superuser,
        "--storage-root",
        str(storage_root()),
        "--manifest",
        str(manifest_path),
        "--authority-dir",
        str(tmp_path / "authority"),
    )
    out = capsys.readouterr().out
    assert "workspaces=2" in out.splitlines()
    assert "pages_opened=2" in out.splitlines()


@pytest.mark.django_db(transaction=True)
def test_verify_restore_command_exits_1_with_the_problems(
    installation: Manifest,
    as_superuser: str,
    tmp_path: Path,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings.BRIEVIE_MASTER_KEY = "00" * 32
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest_to_json(installation))
    with pytest.raises(CommandError) as caught:
        call_command(
            "verify_restore",
            "--dbname",
            as_superuser,
            "--storage-root",
            str(storage_root()),
            "--manifest",
            str(manifest_path),
        )
    assert caught.value.returncode == 1
    err = capsys.readouterr().err
    for workspace_id in installation.workspaces:
        assert str(workspace_id) in err
    assert "00" * 32 not in err + str(caught.value)


@pytest.mark.django_db(transaction=True)
def test_verify_restore_command_refuses_an_unreadable_manifest(
    as_superuser: str, tmp_path: Path
) -> None:
    with pytest.raises(CommandError) as caught:
        call_command(
            "verify_restore",
            "--dbname",
            as_superuser,
            "--storage-root",
            str(tmp_path),
            "--manifest",
            str(tmp_path / "missing.json"),
        )
    assert str(caught.value) == "De inhoudsopgave van de back-up kon niet worden gelezen."


@pytest.mark.django_db(transaction=True)
def test_verify_reports_an_authority_it_was_not_given(
    installation: Manifest, backup_conninfo: str
) -> None:
    # The back-up has an authority: no --authority-dir is not a silent pass.
    (problem,) = verify(backup_conninfo, installation, authority=None).problems
    assert "niet gecontroleerd" in problem
    assert_plain(problem)


@pytest.mark.django_db(transaction=True)
def test_verify_reports_a_page_file_that_is_a_link_out_of_the_storage(
    installation: Manifest, backup_conninfo: str, ws_a: Workspace, tmp_path: Path
) -> None:
    document_id, storage_path = first_page(backup_conninfo, ws_a)
    page = storage_root() / storage_path
    outside = tmp_path / "outside.page"
    outside.write_bytes(page.read_bytes())  # it would even open
    page.unlink()
    page.symlink_to(outside)

    report = verify(backup_conninfo, installation)

    (problem,) = report.problems
    assert str(document_id) in problem
    assert_plain(problem)


@pytest.mark.django_db(transaction=True)
def test_verify_reports_a_link_inside_the_storage_as_well(
    installation: Manifest, backup_conninfo: str, ws_a: Workspace
) -> None:
    document_id, storage_path = first_page(backup_conninfo, ws_a)
    page = storage_root() / storage_path
    other = page.with_name("copy.bin")
    os.replace(page, other)
    page.symlink_to(other)
    (problem,) = verify(backup_conninfo, installation).problems
    assert str(document_id) in problem


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("stored", ["../outside/{doc}/1.page", "/etc/{doc}/1.page"])
def test_verify_reports_a_stored_path_that_leaves_the_storage(
    installation: Manifest,
    backup_conninfo: str,
    owner_conninfo: str,
    ws_b: Workspace,
    tmp_path: Path,
    stored: str,
) -> None:
    from apps.workspaces.rls import SETTING_NAME

    document_id, storage_path = first_page(backup_conninfo, ws_b)
    escaped = stored.format(doc=document_id)
    if escaped.startswith(".."):
        # A real file there, so only the confinement can catch it.
        target = (storage_root() / escaped).resolve()
        target.parent.mkdir(parents=True)
        target.write_bytes((storage_root() / storage_path).read_bytes())
    with psycopg.connect(owner_conninfo) as conn:
        conn.execute("SELECT set_config(%s, %s, true)", [SETTING_NAME, str(ws_b.id)])
        conn.execute(
            "UPDATE documents_documentpage SET storage_path = %s WHERE document_id = %s",
            [escaped, document_id],
        )

    report = verify(backup_conninfo, installation)

    assert any(str(document_id) in problem for problem in report.problems)
    for problem in report.problems:
        assert_plain(problem)
    assert report.pages_opened == 1


@pytest.mark.parametrize(
    ("count", "sentence"),
    [
        (1, "Het herstel is niet compleet: 1 probleem gevonden."),
        (2, "Het herstel is niet compleet: 2 problemen gevonden."),
    ],
)
def test_verify_restore_command_counts_problems_in_good_dutch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, count: int, sentence: str
) -> None:
    report = VerifyReport(problems=["Probleem."] * count, workspaces=1, pages_opened=0)
    monkeypatch.setattr("apps.backup.verify.verify_installation", lambda **_kwargs: report)
    with pytest.raises(CommandError) as caught:
        call_command(
            "verify_restore",
            "--dbname",
            "x",
            "--storage-root",
            str(tmp_path),
            "--manifest",
            str(tmp_path / "m.json"),
        )
    assert str(caught.value) == sentence
