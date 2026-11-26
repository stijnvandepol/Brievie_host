"""The guarded file functions of the scan folder (intake plan Task 10).

No database. Every test works in its own pytest `tmp_path`: an autouse guard points the
scan root and the storage root there and fails the test when either lies outside the
session's temporary directory.
"""

import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytest_django import Settings

from apps.intake import scan_files
from apps.intake.models import ScanFile
from apps.intake.scan_files import (
    Fingerprint,
    ImportedFile,
    ScanRefusedError,
    claim_folder,
    ensure_folder,
    list_candidates,
    list_failed,
    list_subfolders,
    move_to_failed,
    open_root,
    probe,
    read_candidate,
    remove_imported,
)

MAX = 1024 * 1024


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these functions read and write files only."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


@pytest.fixture(autouse=True)
def scan_root(settings: Settings, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.getbasetemp().resolve()
    root = tmp_path / "scan"
    storage = tmp_path / "storage"
    root.mkdir()
    storage.mkdir()
    settings.BRIEVIE_SCAN_ROOT = str(root)
    settings.BRIEVIE_SCAN_DIR_SET = True
    settings.BRIEVIE_STORAGE_ROOT = str(storage)
    for configured in (settings.BRIEVIE_SCAN_ROOT, settings.BRIEVIE_STORAGE_ROOT):
        if not Path(configured).resolve().is_relative_to(base):
            pytest.fail(f"{configured} is outside the test's temporary directory")
    return root


@pytest.fixture
def root_fd(scan_root: Path) -> Iterator[int]:
    fd = open_root()
    yield fd
    os.close(fd)


def write(path: Path, data: bytes = b"%PDF-1.4 brief") -> Fingerprint:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return fingerprint_of(path)


def fingerprint_of(path: Path) -> Fingerprint:
    info = path.stat()
    return Fingerprint(
        device=info.st_dev, inode=info.st_ino, size=info.st_size, mtime_ns=info.st_mtime_ns
    )


def refused(code: str) -> pytest.RaisesExc[ScanRefusedError]:
    return pytest.raises(ScanRefusedError, match=f"^{re.escape(code)}$")


# The root


def test_an_unset_root_is_refused(settings: Settings) -> None:
    settings.BRIEVIE_SCAN_DIR_SET = False
    with refused("root_unset"):
        open_root()


def test_slash_is_refused(settings: Settings) -> None:
    settings.BRIEVIE_SCAN_ROOT = "/"
    with refused("root_is_slash"):
        open_root()


def test_a_missing_root_is_refused(settings: Settings, tmp_path: Path) -> None:
    settings.BRIEVIE_SCAN_ROOT = str(tmp_path / "nowhere")
    with refused("root_missing"):
        open_root()


def test_the_storage_root_is_refused(settings: Settings, tmp_path: Path) -> None:
    settings.BRIEVIE_SCAN_ROOT = str(tmp_path / "storage")
    with refused("root_overlaps_storage"):
        open_root()


def test_a_root_inside_the_storage_is_refused(settings: Settings, tmp_path: Path) -> None:
    inside = tmp_path / "storage" / "scan"
    inside.mkdir()
    settings.BRIEVIE_SCAN_ROOT = str(inside)
    with refused("root_overlaps_storage"):
        open_root()


def test_a_root_containing_the_storage_is_refused(settings: Settings, tmp_path: Path) -> None:
    settings.BRIEVIE_SCAN_ROOT = str(tmp_path)
    with refused("root_overlaps_storage"):
        open_root()


def test_a_root_reached_through_a_link_to_the_storage_is_refused(
    settings: Settings, tmp_path: Path
) -> None:
    (tmp_path / "link").symlink_to(tmp_path / "storage")
    settings.BRIEVIE_SCAN_ROOT = str(tmp_path / "link")
    with refused("root_overlaps_storage"):
        open_root()


# Paths


@pytest.mark.parametrize(
    "path",
    [
        ("..", "brief.pdf"),
        ("kantoor", ".."),
        ("kantoor", "."),
        ("/etc", "passwd"),
        ("kantoor/../..", "brief.pdf"),
        ("kantoor", "bri\x00ef.pdf"),
        ("kantoor", ""),
        ("kantoor", ".verborgen.pdf"),
        ("kantoor", "a", "b", "brief.pdf"),
        (),
    ],
)
def test_bad_paths_are_refused(root_fd: int, path: tuple[str, ...]) -> None:
    expected = Fingerprint(device=0, inode=0, size=1, mtime_ns=0)
    with refused("bad_path"):
        read_candidate(root_fd, path, expected, max_bytes=MAX)
    with refused("bad_path"):
        move_to_failed(root_fd, path, reason_text="x")


# Links, odd files and changed files


def test_a_symlinked_file_is_no_candidate_and_is_never_read(
    root_fd: int, scan_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.pdf"
    print_ = write(outside, b"%PDF-1.4 geheim")
    (scan_root / "kantoor").mkdir()
    (scan_root / "kantoor" / "link.pdf").symlink_to(outside)
    assert list_candidates(root_fd, ("kantoor",)) == []
    with refused("not_a_file"):
        read_candidate(root_fd, ("kantoor", "link.pdf"), print_, max_bytes=MAX)
    with refused("not_a_file"):
        move_to_failed(root_fd, ("kantoor", "link.pdf"), reason_text="x")
    assert outside.read_bytes() == b"%PDF-1.4 geheim"


def test_a_symlinked_workspace_folder_is_refused(
    root_fd: int, scan_root: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    print_ = write(elsewhere / "brief.pdf")
    (scan_root / "kantoor").symlink_to(elsewhere)
    with refused("not_a_folder"):
        list_candidates(root_fd, ("kantoor",))
    with refused("not_a_folder"):
        read_candidate(root_fd, ("kantoor", "brief.pdf"), print_, max_bytes=MAX)
    with refused("not_a_folder"):
        ensure_folder(root_fd, ("kantoor", "mislukt"))
    assert (elsewhere / "brief.pdf").exists()
    assert not (elsewhere / "mislukt").exists()


def test_a_symlinked_person_folder_is_refused(
    root_fd: int, scan_root: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    print_ = write(elsewhere / "brief.pdf")
    (scan_root / "kantoor").mkdir()
    (scan_root / "kantoor" / "Anne").symlink_to(elsewhere)
    assert list_subfolders(root_fd, ("kantoor",)) == []
    with refused("not_a_folder"):
        list_candidates(root_fd, ("kantoor", "Anne"))
    with refused("not_a_folder"):
        read_candidate(root_fd, ("kantoor", "Anne", "brief.pdf"), print_, max_bytes=MAX)
    with refused("not_a_folder"):
        move_to_failed(root_fd, ("kantoor", "Anne", "brief.pdf"), reason_text="x")
    assert (elsewhere / "brief.pdf").read_bytes() == b"%PDF-1.4 brief"


def test_a_hard_linked_file_is_refused(root_fd: int, scan_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.pdf"
    write(outside)
    (scan_root / "kantoor").mkdir()
    os.link(outside, scan_root / "kantoor" / "brief.pdf")
    assert list_candidates(root_fd, ("kantoor",)) == []
    with refused("linked"):
        read_candidate(root_fd, ("kantoor", "brief.pdf"), fingerprint_of(outside), max_bytes=MAX)
    assert outside.exists()


def test_a_fifo_is_no_candidate_and_is_not_read(root_fd: int, scan_root: Path) -> None:
    (scan_root / "kantoor").mkdir()
    fifo = scan_root / "kantoor" / "pijp.pdf"
    os.mkfifo(fifo)
    assert list_candidates(root_fd, ("kantoor",)) == []
    info = fifo.stat()
    expected = Fingerprint(
        device=info.st_dev, inode=info.st_ino, size=info.st_size, mtime_ns=info.st_mtime_ns
    )
    with refused("not_a_file"):
        read_candidate(root_fd, ("kantoor", "pijp.pdf"), expected, max_bytes=MAX)


def test_candidates_are_regular_files_with_a_known_extension(root_fd: int, scan_root: Path) -> None:
    folder = scan_root / "kantoor"
    first = write(folder / "brief.pdf")
    write(folder / "notitie.txt", b"x")
    write(folder / "scan.pdf.part")
    write(folder / ".verborgen.pdf")
    write(folder / "Anne" / "dieper.pdf")
    (folder / "map.pdf").mkdir()
    assert list_candidates(root_fd, ("kantoor",)) == [(("kantoor", "brief.pdf"), first)]


def test_subfolders_are_directories_only(root_fd: int, scan_root: Path) -> None:
    folder = scan_root / "kantoor"
    (folder / "Anne").mkdir(parents=True)
    (folder / "mislukt").mkdir()
    (folder / ".verborgen").mkdir()
    write(folder / "brief.pdf")
    assert list_subfolders(root_fd, ("kantoor",)) == ["Anne", "mislukt"]


def test_reading_gives_the_bytes(root_fd: int, scan_root: Path) -> None:
    print_ = write(scan_root / "kantoor" / "brief.pdf", b"%PDF-1.4 inhoud")
    data = read_candidate(root_fd, ("kantoor", "brief.pdf"), print_, max_bytes=MAX)
    assert data == b"%PDF-1.4 inhoud"


def test_a_file_that_changed_since_the_look_waits(root_fd: int, scan_root: Path) -> None:
    path = scan_root / "kantoor" / "brief.pdf"
    print_ = write(path)
    path.write_bytes(b"%PDF-1.4 langer geworden")
    with refused("changed"):
        read_candidate(root_fd, ("kantoor", "brief.pdf"), print_, max_bytes=MAX)


def test_a_file_over_the_limit_is_not_read(root_fd: int, scan_root: Path) -> None:
    print_ = write(scan_root / "kantoor" / "brief.pdf", b"x" * 100)
    with refused("too_large"):
        read_candidate(root_fd, ("kantoor", "brief.pdf"), print_, max_bytes=99)


# Removing an imported file


def row(
    path: str, print_: Fingerprint, *, state: str = "imported", document: bool = True
) -> ScanFile:
    return ScanFile(
        relative_path=path,
        device=print_.device,
        inode=print_.inode,
        size=print_.size,
        mtime_ns=print_.mtime_ns,
        state=state,
        document_id=uuid.uuid4() if document else None,
    )


def test_an_imported_file_is_removed(root_fd: int, scan_root: Path) -> None:
    path = scan_root / "kantoor" / "brief.pdf"
    print_ = write(path)
    remove_imported(root_fd, ImportedFile.from_row(row("kantoor/brief.pdf", print_)))
    assert not path.exists()
    assert (scan_root / "kantoor").is_dir()


def test_a_row_that_is_not_imported_removes_nothing(root_fd: int, scan_root: Path) -> None:
    print_ = write(scan_root / "kantoor" / "brief.pdf")
    with refused("not_imported"):
        ImportedFile.from_row(row("kantoor/brief.pdf", print_, state="seen"))
    with refused("not_imported"):
        ImportedFile.from_row(row("kantoor/brief.pdf", print_, document=False))
    assert (scan_root / "kantoor" / "brief.pdf").exists()


@pytest.mark.parametrize("change", ["size", "mtime", "replaced"])
def test_a_file_changed_after_import_is_not_removed(
    root_fd: int, scan_root: Path, change: str
) -> None:
    path = scan_root / "kantoor" / "brief.pdf"
    print_ = write(path)
    if change == "size":
        path.write_bytes(b"%PDF-1.4 een andere brief")
    elif change == "mtime":
        os.utime(path, ns=(print_.mtime_ns + 10**9, print_.mtime_ns + 10**9))
    else:
        path.unlink()
        write(path)
    with refused("changed"):
        remove_imported(root_fd, ImportedFile.from_row(row("kantoor/brief.pdf", print_)))
    assert path.exists()


def test_a_symlink_in_place_of_an_imported_file_is_not_removed(
    root_fd: int, scan_root: Path, tmp_path: Path
) -> None:
    path = scan_root / "kantoor" / "brief.pdf"
    print_ = write(path)
    path.unlink()
    outside = tmp_path / "outside.pdf"
    write(outside)
    path.symlink_to(outside)
    with refused("changed"):
        remove_imported(root_fd, ImportedFile.from_row(row("kantoor/brief.pdf", print_)))
    assert path.is_symlink()
    assert outside.exists()


# Moving a refused file to mislukt/


def test_a_refused_file_moves_with_its_reason(root_fd: int, scan_root: Path) -> None:
    write(scan_root / "kantoor" / "Anne" / "brief.pdf", b"%PDF-1.4 beveiligd")
    moved = move_to_failed(root_fd, ("kantoor", "Anne", "brief.pdf"), reason_text="Kapot.")
    assert moved == ("kantoor", "mislukt", "brief.pdf")
    failed = scan_root / "kantoor" / "mislukt"
    assert (failed / "brief.pdf").read_bytes() == b"%PDF-1.4 beveiligd"
    assert (failed / "brief.pdf.reden.txt").read_text(encoding="utf-8") == "Kapot.\n"
    assert not (scan_root / "kantoor" / "Anne" / "brief.pdf").exists()


def test_a_taken_name_gets_a_number_and_nothing_is_overwritten(
    root_fd: int, scan_root: Path
) -> None:
    failed = scan_root / "kantoor" / "mislukt"
    write(failed / "brief.pdf", b"de eerste")
    write(failed / "brief (2).pdf.reden.txt", b"een oude reden")
    write(scan_root / "kantoor" / "brief.pdf", b"de tweede")
    moved = move_to_failed(root_fd, ("kantoor", "brief.pdf"), reason_text="Kapot.")
    assert moved == ("kantoor", "mislukt", "brief (3).pdf")
    assert (failed / "brief.pdf").read_bytes() == b"de eerste"
    assert (failed / "brief (2).pdf.reden.txt").read_bytes() == b"een oude reden"
    assert not (failed / "brief (2).pdf").exists()
    assert (failed / "brief (3).pdf").read_bytes() == b"de tweede"


def test_a_file_in_mislukt_is_untouchable(root_fd: int, scan_root: Path) -> None:
    print_ = write(scan_root / "kantoor" / "mislukt" / "brief.pdf")
    with refused("failed_untouchable"):
        move_to_failed(root_fd, ("kantoor", "mislukt", "brief.pdf"), reason_text="x")
    imported = ImportedFile.from_row(row("kantoor/mislukt/brief.pdf", print_))
    with refused("failed_untouchable"):
        remove_imported(root_fd, imported)
    assert (scan_root / "kantoor" / "mislukt" / "brief.pdf").exists()


def test_list_failed_reads_mislukt(root_fd: int, scan_root: Path) -> None:
    assert list_failed(root_fd, ("kantoor",)) == set()
    write(scan_root / "kantoor" / "mislukt" / "brief.pdf")
    write(scan_root / "kantoor" / "mislukt" / "brief.pdf.reden.txt", b"x")
    assert list_failed(root_fd, ("kantoor",)) == {"brief.pdf", "brief.pdf.reden.txt"}


# Folders and the probe


def test_ensure_folder_makes_one_level_at_a_time(root_fd: int, scan_root: Path) -> None:
    ensure_folder(root_fd, ("kantoor", "mislukt"))
    ensure_folder(root_fd, ("kantoor", "mislukt"))
    assert (scan_root / "kantoor" / "mislukt").is_dir()


def test_probe_reports_ok_and_leaves_nothing(root_fd: int, scan_root: Path) -> None:
    (scan_root / "kantoor").mkdir()
    result = probe(root_fd, ("kantoor",))
    assert result.state == "ok"
    assert result.owner_uid == os.getuid()
    assert list((scan_root / "kantoor").iterdir()) == []


def test_probe_reports_a_missing_folder(root_fd: int) -> None:
    assert probe(root_fd, ("kantoor",)).state == "missing"


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes everywhere")
def test_probe_reports_a_folder_it_cannot_write(root_fd: int, scan_root: Path) -> None:
    folder = scan_root / "kantoor"
    folder.mkdir()
    folder.chmod(0o555)
    try:
        assert probe(root_fd, ("kantoor",)).state == "not_writable"
        assert list(folder.iterdir()) == []
    finally:
        folder.chmod(0o755)


def test_the_module_never_removes_a_directory_or_globs() -> None:
    source = Path(scan_files.__file__).read_text()
    for word in ("rmtree", "rmdir", "glob", "removedirs"):
        assert word not in source, word


# Review fixes (final review of the branch)


def test_a_long_name_moves_with_a_shorter_name(root_fd: int, scan_root: Path) -> None:
    name = "b" * 240 + ".pdf"
    write(scan_root / "kantoor" / name)
    moved = move_to_failed(root_fd, ("kantoor", name), reason_text="Kapot.")
    assert moved[:2] == ("kantoor", "mislukt")
    failed = scan_root / "kantoor" / "mislukt"
    assert (failed / moved[2]).exists()
    assert (failed / (moved[2] + ".reden.txt")).exists()
    assert moved[2].endswith(".pdf")
    assert not (scan_root / "kantoor" / name).exists()
    assert len(list(failed.iterdir())) == 2


def test_a_name_that_is_not_utf8_is_refused() -> None:
    assert not scan_files.valid_name("brief\udcff.pdf")
    assert scan_files.valid_name("brief.pdf")


def test_a_workspace_claims_its_folder(root_fd: int, scan_root: Path) -> None:
    (scan_root / "kantoor").mkdir()
    assert claim_folder(root_fd, ("kantoor",), "werkruimte-een")
    assert claim_folder(root_fd, ("kantoor",), "werkruimte-een")
    assert not claim_folder(root_fd, ("kantoor",), "werkruimte-twee")
    assert (scan_root / "kantoor" / ".brievie-werkruimte").read_text() == "werkruimte-een\n"


def test_a_linked_claim_is_refused(root_fd: int, scan_root: Path, tmp_path: Path) -> None:
    (scan_root / "kantoor").mkdir()
    outside = tmp_path / "claim"
    outside.write_text("werkruimte-een\n")
    (scan_root / "kantoor" / ".brievie-werkruimte").symlink_to(outside)
    assert not claim_folder(root_fd, ("kantoor",), "werkruimte-een")
