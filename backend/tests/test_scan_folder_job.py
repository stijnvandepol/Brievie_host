"""The scan folder's round (intake plan Task 12). The share is a folder in pytest's
`tmp_path`; the clock is passed in. The job only stores letters and queues the pipeline,
so no OCR runs here."""

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from django.db import DatabaseError
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User
from apps.documents import visibility
from apps.documents.models import Document, DocumentAddressee, DocumentEvent, DocumentReader
from apps.documents.schemas import DocumentEventOut
from apps.documents.services import list_document_events
from apps.intake import scan_files, scan_folder
from apps.intake.models import ScanFile, ScanFolder, ScanFolderSettings
from apps.intake.scan_folder import scan_round
from apps.people.models import Person
from apps.people.services import ensure_person_for_member
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory
from tests.intake_fixtures import make_encrypted_pdf, make_jpeg
from tests.visibility_support import as_actor, set_state

T0 = timezone.now()
LATER = 61


@dataclass(frozen=True)
class World:
    workspace: Workspace
    admin: User
    member: User
    colleague: User
    member_person: Person
    colleague_person: Person
    department: Person


@pytest.fixture(autouse=True)
def share(settings: Settings, tmp_path: Path) -> Path:
    root = tmp_path / "scan"
    root.mkdir()
    settings.BRIEVIE_SCAN_ROOT = str(root)
    settings.BRIEVIE_SCAN_DIR_SET = True
    assert root.resolve().is_relative_to(tmp_path.resolve())
    return root


def _member(workspace: Workspace, name: str) -> User:
    user = UserFactory.create(name=name)
    Membership.objects.create(user=user, workspace=workspace, role=Membership.Role.MEMBER)
    return user


def make_world(*, per_person: bool = True, enabled: bool = True) -> World:
    admin = UserFactory.create(name="Beheerder")
    workspace = create_workspace(name="Kantoor", kind="business", admin=admin)
    member = _member(workspace, "Medewerker")
    colleague = _member(workspace, "Collega")
    with workspace_context(workspace.id, actor=SYSTEM):
        member_person = ensure_person_for_member(workspace=workspace, user=member)
        colleague_person = ensure_person_for_member(workspace=workspace, user=colleague)
        department = Person.objects.create(
            workspace=workspace, display_name="Financien", kind=Person.Kind.DEPARTMENT
        )
        ScanFolderSettings.objects.create(
            workspace=workspace, enabled=enabled, folder_name="kantoor", per_person=per_person
        )
    # Not everyone sees all post, departments on, admins do not read all.
    set_state(workspace, e=False, d=True, a=False)
    if enabled:
        # The first look makes and claims the workspace's folder (review of PR #64, M3).
        scan_round(now=at(-LATER))
    return World(workspace, admin, member, colleague, member_person, colleague_person, department)


def at(seconds: int) -> Callable[[], datetime]:
    return lambda: T0 + timedelta(seconds=seconds)


def two_rounds() -> None:
    scan_round(now=at(0))
    scan_round(now=at(LATER))


def put(share: Path, *parts: str, data: bytes | None = None) -> Path:
    path = share.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if data is not None else make_jpeg(40, 30))
    return path


def letters(world: World) -> list[Document]:
    with workspace_context(world.workspace.id, actor=SYSTEM):
        return list(Document.objects.filter(source=Document.Source.WATCH_FOLDER))


def files(world: World) -> list[ScanFile]:
    with workspace_context(world.workspace.id, actor=SYSTEM):
        return list(ScanFile.objects.order_by("relative_path"))


def settings_row(world: World) -> ScanFolderSettings:
    with workspace_context(world.workspace.id, actor=SYSTEM):
        return ScanFolderSettings.objects.get()


def test_an_unstable_file_waits(share: Path) -> None:
    world = make_world(per_person=False)
    put(share, "kantoor", "brief.jpg")
    scan_round(now=at(0))
    assert letters(world) == []
    assert [row.state for row in files(world)] == ["seen"]
    assert (share / "kantoor" / "brief.jpg").exists()
    assert (share / "kantoor" / "mislukt").is_dir()


def test_a_stable_file_becomes_a_letter_and_leaves_the_share(share: Path) -> None:
    world = make_world(per_person=False)
    source = put(share, "kantoor", "brief.jpg")
    two_rounds()
    [document] = letters(world)
    assert document.status == Document.Status.PROCESSING
    assert document.scanned_by_id is None
    assert document.original_name == "brief.jpg"
    assert not source.exists()
    [row] = files(world)
    assert row.state == "imported"
    assert row.document_id == document.id
    assert row.removed_at is not None
    assert settings_row(world).check_state == "ok"


def test_a_crash_before_the_removal_makes_no_second_letter(
    share: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = make_world(per_person=False)
    source = put(share, "kantoor", "brief.jpg")
    real = scan_files.remove_imported
    calls: list[int] = []

    def broken_once(root_fd: int, imported: scan_files.ImportedFile) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("cut off")
        real(root_fd, imported)

    monkeypatch.setattr(scan_files, "remove_imported", broken_once)
    two_rounds()
    assert source.exists()
    [first] = letters(world)
    # A failed removal waits an hour before the next try.
    scan_round(now=at(2 * LATER))
    assert source.exists()
    # The row's time is the real clock's (T0 is fixed when the module loads).
    scan_round(now=lambda: timezone.now() + timedelta(hours=2))
    assert [d.id for d in letters(world)] == [first.id]
    assert not source.exists()


def test_an_encrypted_pdf_goes_to_mislukt_with_its_reason(share: Path) -> None:
    world = make_world(per_person=False)
    put(share, "kantoor", "beveiligd.pdf", data=make_encrypted_pdf())
    two_rounds()
    assert letters(world) == []
    failed = share / "kantoor" / "mislukt"
    assert (failed / "beveiligd.pdf").exists()
    reason = (failed / "beveiligd.pdf.reden.txt").read_text(encoding="utf-8")
    assert "beveiligd met een wachtwoord" in reason
    [row] = files(world)
    assert (row.state, row.reason) == ("failed", "file_encrypted")
    assert row.relative_path == "kantoor/mislukt/beveiligd.pdf"


def test_files_in_mislukt_are_never_touched(share: Path) -> None:
    world = make_world(per_person=False)
    kept = put(share, "kantoor", "mislukt", "oud.jpg")
    before = (kept.read_bytes(), kept.stat().st_mtime_ns)
    two_rounds()
    scan_round(now=at(2 * LATER))
    assert (kept.read_bytes(), kept.stat().st_mtime_ns) == before
    assert letters(world) == []
    assert files(world) == []


def test_a_file_in_a_persons_folder_is_their_letter(share: Path) -> None:
    world = make_world()
    scan_round(now=at(0))
    assert (share / "kantoor" / "Medewerker").is_dir()
    put(share, "kantoor", "Medewerker", "brief.jpg")
    scan_round(now=at(LATER))
    scan_round(now=at(2 * LATER))
    [document] = letters(world)
    with workspace_context(world.workspace.id, actor=SYSTEM):
        link = DocumentAddressee.objects.get(document=document)
        event = DocumentEvent.objects.get(
            document=document, kind=DocumentEvent.Kind.ADDRESSEE_ADDED
        )
        document.refresh_from_db()
    assert link.person_id == world.member_person.id
    assert link.automatic is True
    assert event.origin == "watch_folder"
    assert event.actor_id is None
    assert document.addressee_ids == [world.member_person.id]


def test_a_department_folder_makes_the_department_the_addressee(share: Path) -> None:
    world = make_world()
    scan_round(now=at(0))
    put(share, "kantoor", "Financien", "factuur.jpg")
    scan_round(now=at(LATER))
    scan_round(now=at(2 * LATER))
    [document] = letters(world)
    assert document.addressee_ids == [world.department.id]


def test_department_folder_paused_while_departments_off(share: Path) -> None:
    world = make_world()
    scan_round(now=at(0))
    set_state(world.workspace, e=False, d=False, a=False)
    waiting = put(share, "kantoor", "Financien", "factuur.jpg")
    scan_round(now=at(LATER))
    scan_round(now=at(2 * LATER))
    assert letters(world) == []
    assert waiting.exists()
    with workspace_context(world.workspace.id, actor=SYSTEM):
        assert ScanFolder.objects.get(person=world.department).state == "paused"
    assert [row.relative_path for row in files(world)] == ["kantoor/Financien/factuur.jpg"]
    set_state(world.workspace, e=False, d=True, a=False)
    scan_round(now=at(3 * LATER))
    assert len(letters(world)) == 1
    assert not waiting.exists()


def test_a_deleted_persons_folder_sends_its_files_to_mislukt(share: Path) -> None:
    world = make_world()
    scan_round(now=at(0))
    with workspace_context(world.workspace.id, actor=SYSTEM):
        Person.objects.filter(id=world.colleague_person.id).update(
            archived_at=timezone.now(), user=None
        )
    put(share, "kantoor", "Collega", "brief.jpg")
    scan_round(now=at(LATER))
    scan_round(now=at(2 * LATER))
    assert letters(world) == []
    failed = share / "kantoor" / "mislukt"
    assert (failed / "brief.jpg").exists()
    assert "persoon of afdeling" in (failed / "brief.jpg.reden.txt").read_text(encoding="utf-8")
    assert (share / "kantoor" / "Collega").is_dir()


def test_unknown_subfolder_is_left_alone(share: Path) -> None:
    world = make_world()
    kept = put(share, "kantoor", "Oude scans", "brief.jpg")
    before = (kept.read_bytes(), kept.stat().st_mtime_ns)
    with workspace_context(world.workspace.id, actor=SYSTEM):
        persons_before = Person.objects.count()
    two_rounds()
    scan_round(now=at(2 * LATER))
    assert (kept.read_bytes(), kept.stat().st_mtime_ns) == before
    assert files(world) == []
    assert settings_row(world).unknown_folders == [{"name": "Oude scans", "files": 1}]
    with workspace_context(world.workspace.id, actor=SYSTEM):
        assert Person.objects.count() == persons_before


def test_a_hand_made_folder_with_the_expected_name_is_adopted(share: Path) -> None:
    world = make_world()
    put(share, "kantoor", "Medewerker", "brief.jpg")
    two_rounds()
    [document] = letters(world)
    assert document.addressee_ids == [world.member_person.id]


def test_a_person_without_an_account_gets_a_folder_only_when_added(share: Path) -> None:
    world = make_world()
    with workspace_context(world.workspace.id, actor=SYSTEM):
        stagiair = Person.objects.create(workspace=world.workspace, display_name="Stagiair")
    scan_round(now=at(0))
    assert not (share / "kantoor" / "Stagiair").exists()
    with workspace_context(world.workspace.id, actor=SYSTEM):
        ScanFolder.objects.create(
            workspace=world.workspace, person=stagiair, name="Stagiair", added_by_admin=True
        )
    scan_round(now=at(LATER))
    assert (share / "kantoor" / "Stagiair").is_dir()


def test_person_folder_letter_seen_by_that_person_only(share: Path) -> None:
    world = make_world()
    put(share, "kantoor", "Medewerker", "brief.jpg")
    two_rounds()
    [document] = letters(world)
    memberships = {m.user_id: m for m in Membership.objects.filter(workspace=world.workspace)}
    with as_actor(world.workspace, world.member.id):
        assert (
            visibility.visible_documents(memberships[world.member.id])
            .filter(id=document.id)
            .exists()
        )
    with as_actor(world.workspace, world.colleague.id):
        assert (
            not visibility.visible_documents(memberships[world.colleague.id])
            .filter(id=document.id)
            .exists()
        )
    with workspace_context(world.workspace.id, actor=SYSTEM):
        assert not DocumentReader.objects.filter(document_id=document.id).exists()


def test_the_folders_addressee_can_be_undone(share: Path) -> None:
    world = make_world()
    put(share, "kantoor", "Medewerker", "brief.jpg")
    two_rounds()
    [document] = letters(world)
    # The letter is on the member's name; admins do not read all post here.
    membership = Membership.objects.get(workspace=world.workspace, user=world.member)
    with as_actor(world.workspace, world.member.id):
        events = list_document_events(membership=membership, document_id=document.id)
    [added] = [e for e in events if e.kind == DocumentEvent.Kind.ADDRESSEE_ADDED]
    assert added.origin == "watch_folder"
    assert added.actor_id is None
    assert DocumentEventOut.resolve_can_undo(added) is True


def test_twenty_files_a_round(share: Path) -> None:
    world = make_world(per_person=False)
    for number in range(25):
        put(share, "kantoor", f"brief-{number:02}.jpg")
    two_rounds()
    assert len(letters(world)) == 20
    scan_round(now=at(2 * LATER))
    assert len(letters(world)) == 25


def test_a_workspace_with_the_folder_off_is_not_read(share: Path) -> None:
    world = make_world(enabled=False)
    source = put(share, "kantoor", "brief.jpg")
    two_rounds()
    assert letters(world) == []
    assert source.exists()
    assert files(world) == []
    assert not (share / "kantoor" / "mislukt").exists()


def test_file_put_back_from_failed_is_tried_again(share: Path) -> None:
    world = make_world(per_person=False)
    source = put(share, "kantoor", "foto.jpg", data=b"\xff\xd8\xff" + bytes(40))
    two_rounds()
    failed = share / "kantoor" / "mislukt" / "foto.jpg"
    assert failed.exists()
    inode = failed.stat().st_ino
    os.rename(failed, source)
    with source.open("r+b") as handle:
        handle.write(make_jpeg(40, 30))
    assert source.stat().st_ino == inode
    scan_round(now=at(2 * LATER))
    scan_round(now=at(3 * LATER))
    assert len(letters(world)) == 1
    assert not source.exists()


def test_a_file_removed_from_mislukt_by_hand_is_forgotten(share: Path) -> None:
    world = make_world(per_person=False)
    put(share, "kantoor", "beveiligd.pdf", data=make_encrypted_pdf())
    two_rounds()
    assert [row.state for row in files(world)] == ["failed"]
    (share / "kantoor" / "mislukt" / "beveiligd.pdf").unlink()
    scan_round(now=at(2 * LATER))
    assert files(world) == []


def test_a_database_error_leaves_the_file(share: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(per_person=False)
    source = put(share, "kantoor", "brief.jpg")

    def broken(**_kwargs: object) -> None:
        raise DatabaseError("gone")

    monkeypatch.setattr(scan_folder, "create_document_from_file", broken)
    two_rounds()
    assert letters(world) == []
    assert source.exists()
    assert not (share / "kantoor" / "mislukt" / "brief.jpg").exists()


def test_no_file_or_folder_name_reaches_the_log(
    share: Path, caplog: pytest.LogCaptureFixture
) -> None:
    make_world()
    put(share, "kantoor", "Medewerker", "geheimnaam-een.jpg")
    put(share, "kantoor", "geheimmap", "geheimnaam-twee.jpg")
    put(share, "kantoor", "geheimnaam-drie.pdf", data=make_encrypted_pdf())
    with caplog.at_level(logging.DEBUG):
        two_rounds()
    assert "geheim" not in caplog.text
    assert "Medewerker" not in caplog.text


def test_a_folder_another_workspace_claimed_is_not_read(share: Path) -> None:
    world = make_world(per_person=False)
    source = put(share, "kantoor", "brief.jpg")
    (share / "kantoor" / ".brievie-werkruimte").write_text("een-andere-werkruimte\n")
    two_rounds()
    assert letters(world) == []
    assert source.exists()
    assert settings_row(world).check_state == "taken"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_a_file_brievie_may_not_read_goes_to_mislukt_and_holds_nothing_up(
    share: Path,
) -> None:
    world = make_world(per_person=False)
    closed = put(share, "kantoor", "a-dicht.jpg")
    put(share, "kantoor", "b-open.jpg")
    closed.chmod(0o000)
    two_rounds()
    assert len(letters(world)) == 1
    assert (share / "kantoor" / "mislukt" / "a-dicht.jpg").exists()


def test_a_purged_letter_whose_source_stayed_holds_nothing_up(
    share: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = make_world(per_person=False)
    kept = put(share, "kantoor", "brief.jpg")
    monkeypatch.setattr(scan_files, "remove_imported", _refuse)
    two_rounds()
    [document] = letters(world)
    with workspace_context(world.workspace.id, actor=SYSTEM):
        ScanFile.objects.update(document=None)
        Document.objects.filter(id=document.id).delete()
    put(share, "kantoor", "tweede.jpg")
    scan_round(now=at(2 * LATER + 3600))
    scan_round(now=at(3 * LATER + 3600))
    assert len(letters(world)) == 1
    assert kept.exists()


def _refuse(root_fd: int, imported: scan_files.ImportedFile) -> None:
    raise scan_files.ScanRefusedError("changed")


# Security review of PR #64 (M1, M4)


def test_a_file_dated_past_2262_is_still_taken(share: Path) -> None:
    world = make_world(per_person=False)
    source = put(share, "kantoor", "toekomst.jpg")
    far = 10_400_000_000 * 10**9  # the year 2299, past a signed 64-bit count of ns
    try:
        os.utime(source, ns=(far, far))
    except (OSError, OverflowError, ValueError):
        pytest.skip("this filesystem cannot hold that date")
    two_rounds()
    assert len(letters(world)) == 1
    assert settings_row(world).check_state == "ok"


def test_a_flooded_folder_is_capped_and_reported(
    share: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = make_world(per_person=False)
    monkeypatch.setattr(scan_folder, "CANDIDATES_PER_FOLDER", 3)
    for number in range(5):
        put(share, "kantoor", f"brief-{number}.jpg")
    scan_round(now=at(0))
    assert len(files(world)) == 3
    assert settings_row(world).skipped_count == 2


def test_the_rows_of_a_workspace_are_capped(share: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = make_world(per_person=False)
    monkeypatch.setattr(scan_folder, "ROWS_MAX", 2)
    for number in range(4):
        put(share, "kantoor", f"brief-{number}.jpg")
    scan_round(now=at(0))
    assert len(files(world)) == 2
    assert settings_row(world).skipped_count == 2


def test_an_empty_file_goes_to_mislukt_after_an_hour(share: Path) -> None:
    world = make_world(per_person=False)
    put(share, "kantoor", "leeg.pdf", data=b"")
    scan_round(now=at(0))
    scan_round(now=at(LATER))
    assert (share / "kantoor" / "leeg.pdf").exists()
    scan_round(now=at(3700))
    assert not (share / "kantoor" / "leeg.pdf").exists()
    assert (share / "kantoor" / "mislukt" / "leeg.pdf").exists()
    assert [(row.state, row.reason) for row in files(world)] == [("failed", "never_settled")]


def test_an_unchanged_waiting_row_is_not_written_again(share: Path) -> None:
    world = make_world()
    scan_round(now=at(0))
    set_state(world.workspace, e=False, d=False, a=False)
    put(share, "kantoor", "Financien", "factuur.jpg")
    scan_round(now=at(LATER))
    [before] = files(world)
    scan_round(now=at(2 * LATER))
    [after] = files(world)
    assert after.updated_at == before.updated_at


def test_imports_wait_while_many_letters_are_still_processing(
    share: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = make_world(per_person=False)
    monkeypatch.setattr(scan_folder, "PROCESSING_MAX", 1)
    for number in range(3):
        put(share, "kantoor", f"brief-{number}.jpg")
    two_rounds()
    scan_round(now=at(2 * LATER))
    # The pipeline does not run here: the first letter stays `processing`.
    assert len(letters(world)) == 1


def test_a_round_that_breaks_is_shown_on_the_page(
    share: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = make_world(per_person=False)

    def broken(*_args: object, **_kwargs: object) -> None:
        raise DatabaseError("kapot")

    monkeypatch.setattr(scan_folder, "_record", broken)
    scan_round(now=at(LATER))
    assert settings_row(world).check_state == "failing"


# Security review of PR #64 (M3)


def test_an_existing_folder_with_files_is_never_taken_in(share: Path) -> None:
    kept = put(share, "kantoor", "hr-brief.jpg")
    before = kept.read_bytes()
    world = make_world(per_person=False)
    two_rounds()
    assert letters(world) == []
    assert kept.read_bytes() == before
    assert sorted(p.name for p in (share / "kantoor").iterdir()) == ["hr-brief.jpg"]
    assert settings_row(world).check_state == "not_empty"


def test_an_existing_empty_folder_is_used(share: Path) -> None:
    (share / "kantoor").mkdir()
    world = make_world(per_person=False)
    put(share, "kantoor", "brief.jpg")
    two_rounds()
    assert len(letters(world)) == 1
