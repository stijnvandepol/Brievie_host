"""The guarded removal of a letter's sealed page files (`apps.documents.page_files`).

Deleting code: every file this module makes or removes lies under the test's own
`tmp_path`. The autouse fixture in conftest points `BRIEVIE_STORAGE_ROOT` at
`tmp_path / "documents"` and installs `tmp_path` as the test root; `storage_root()`
checks that again before a test makes anything. The refusal tests come first and call
the judges only; no absolute path outside `tmp_path` appears here except as a string
given to a judge.
"""

import os
import re
import uuid
from pathlib import Path, PurePosixPath

import pytest
from pytest_django import Settings

from apps.backup import safepaths
from apps.documents.page_files import (
    PageFileRefused,
    judge_page_path,
    judge_storage_root,
    remove_document_files,
)

_UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-", re.IGNORECASE)

WS = uuid.UUID("0b7f3c1e-1111-4a2b-9c3d-000000000001")
DOC = uuid.UUID("0b7f3c1e-2222-4a2b-9c3d-000000000002")
OTHER = uuid.UUID("0b7f3c1e-3333-4a2b-9c3d-000000000003")


def storage_root(tmp_path: Path) -> Path:
    """The configured storage root, after proving it lies inside this test's tmp_path."""
    from django.conf import settings

    root = Path(settings.BRIEVIE_STORAGE_ROOT)
    assert root == tmp_path / "documents"
    assert tmp_path.resolve() in root.resolve().parents
    assert safepaths.installed_test_root() == tmp_path.resolve()
    return root


def letter_dir(tmp_path: Path, workspace_id: uuid.UUID = WS, document_id: uuid.UUID = DOC) -> Path:
    folder = storage_root(tmp_path) / str(workspace_id) / str(document_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def page_paths(
    *indexes: int, workspace_id: uuid.UUID = WS, document_id: uuid.UUID = DOC
) -> list[str]:
    return [f"{workspace_id}/{document_id}/{n}.page" for n in indexes]


# --- refusals: the judges only -----------------------------------------------------------


@pytest.mark.parametrize(
    "stored",
    [
        "/etc/passwd",
        "../x/1.page",
        "{ws}/{doc}/../../1.page",
        "{ws}/{other}/1.page",
        "{other}/{doc}/1.page",
        "{ws}/{doc}/1.page\0",
        "{ws}\\{doc}\\1.page",
        "{ws}/{doc}/",
        "{ws}/{doc}/notes.txt",
        "{ws}/{doc}/1.page/x",
        "{WS_UPPER}/{doc}/1.page",
        # Added: an absolute form of the own page, a non-ASCII digit, a temp file, empty.
        "/{ws}/{doc}/1.page",
        "{ws}/{doc}/{arabic_one}.page",
        "{ws}/{doc}/.tmp-abc",
        "",
        "{ws}/{doc}/1.page\n",
        "./{ws}/{doc}/1.page",
        "{ws}//{doc}/1.page",
    ],
)
def test_judge_page_path_refuses(stored: str) -> None:
    text = stored.format(
        ws=WS, doc=DOC, other=OTHER, WS_UPPER=str(WS).upper(), arabic_one=chr(0x0661)
    )

    with pytest.raises(PageFileRefused) as caught:
        judge_page_path(text, workspace_id=WS, document_id=DOC)

    assert _UUID_TEXT.search(str(caught.value)) is None
    assert "/" not in str(caught.value)


def test_judge_page_path_accepts_own_page() -> None:
    judged = judge_page_path(f"{WS}/{DOC}/3.page", workspace_id=WS, document_id=DOC)

    assert judged == PurePosixPath(f"{WS}/{DOC}/3.page")
    assert judged.name == "3.page"


@pytest.mark.parametrize(
    "root", ["/", "/Users/someone", "/home/someone", "/data", "relative/documents"]
)
def test_judge_storage_root_refuses(root: str) -> None:
    with pytest.raises(PageFileRefused):
        judge_storage_root(Path(root))


def test_judge_storage_root_refuses_production_path_not_configured() -> None:
    # /data/documents is on the fixed list but is not the configured root in tests.
    with pytest.raises(PageFileRefused):
        judge_storage_root(Path("/data/documents"))


def test_judge_storage_root_refuses_another_folder_in_tmp(tmp_path: Path) -> None:
    storage_root(tmp_path)
    with pytest.raises(PageFileRefused):
        judge_storage_root(tmp_path / "elsewhere")


def test_judge_storage_root_accepts_the_configured_root(tmp_path: Path) -> None:
    root = storage_root(tmp_path)

    assert judge_storage_root(root) == root


def test_judge_storage_root_refuses_backup_mount(settings: Settings, tmp_path: Path) -> None:
    root = storage_root(tmp_path)
    settings.BRIEVIE_BACKUP_MOUNT = str(root)

    with pytest.raises(PageFileRefused):
        judge_storage_root(root)


def test_judge_storage_root_refuses_root_inside_backup_mount(
    settings: Settings, tmp_path: Path
) -> None:
    root = storage_root(tmp_path)
    settings.BRIEVIE_BACKUP_MOUNT = str(tmp_path)

    with pytest.raises(PageFileRefused):
        judge_storage_root(root)


def test_judge_storage_root_refuses_symlinked_root(settings: Settings, tmp_path: Path) -> None:
    real = storage_root(tmp_path)
    real.mkdir()
    link = tmp_path / "documents-link"
    link.symlink_to(real, target_is_directory=True)
    settings.BRIEVIE_STORAGE_ROOT = str(link)

    with pytest.raises(PageFileRefused):
        judge_storage_root(link)


# --- removal: only under tmp_path, after the judges above ---------------------------------


def test_remove_refuses_whole_call_on_one_bad_path(tmp_path: Path) -> None:
    folder = letter_dir(tmp_path)
    good = folder / "1.page"
    good.write_bytes(b"sealed")

    with pytest.raises(PageFileRefused):
        remove_document_files(WS, DOC, [*page_paths(1), f"{WS}/{OTHER}/1.page"])

    assert good.read_bytes() == b"sealed"


def test_remove_refuses_with_a_root_the_judge_refuses(settings: Settings, tmp_path: Path) -> None:
    folder = letter_dir(tmp_path)
    page = folder / "1.page"
    page.write_bytes(b"sealed")
    with pytest.raises(PageFileRefused):  # the guard first, proven on the same setting
        judge_storage_root(storage_root(tmp_path).parent / "backup")
    settings.BRIEVIE_BACKUP_MOUNT = str(storage_root(tmp_path))

    with pytest.raises(PageFileRefused):
        remove_document_files(WS, DOC, page_paths(1))

    assert page.exists()


def test_remove_refuses_a_symlinked_root(settings: Settings, tmp_path: Path) -> None:
    folder = letter_dir(tmp_path)
    page = folder / "1.page"
    page.write_bytes(b"sealed")
    link = tmp_path / "documents-link"
    link.symlink_to(storage_root(tmp_path), target_is_directory=True)
    settings.BRIEVIE_STORAGE_ROOT = str(link)
    with pytest.raises(PageFileRefused):
        judge_storage_root(link)

    with pytest.raises(PageFileRefused):
        remove_document_files(WS, DOC, page_paths(1))

    assert page.exists()


def test_remove_deletes_pages_tmp_and_empty_dir(tmp_path: Path) -> None:
    folder = letter_dir(tmp_path)
    for name in ("1.page", "2.page", ".tmp-abc"):
        (folder / name).write_bytes(b"sealed")

    assert remove_document_files(WS, DOC, page_paths(1, 2)) == 3

    assert not folder.exists()
    assert folder.parent.is_dir()  # the workspace directory stays
    assert storage_root(tmp_path).is_dir()


def test_remove_keeps_dir_with_foreign_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    folder = letter_dir(tmp_path)
    (folder / "1.page").write_bytes(b"sealed")
    (folder / "keep.txt").write_bytes(b"not ours")
    (folder / "7.page").write_bytes(b"not named by the call")

    assert remove_document_files(WS, DOC, page_paths(1)) == 1

    assert (folder / "keep.txt").exists()
    assert (folder / "7.page").exists()
    assert not (folder / "1.page").exists()
    assert str(tmp_path) not in caplog.text


def test_remove_never_follows_linked_document_dir(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "1.page").write_bytes(b"someone else's")
    workspace_dir = storage_root(tmp_path) / str(WS)
    workspace_dir.mkdir(parents=True)
    (workspace_dir / str(DOC)).symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(PageFileRefused):
        remove_document_files(WS, DOC, page_paths(1))

    assert (elsewhere / "1.page").read_bytes() == b"someone else's"
    assert (workspace_dir / str(DOC)).is_symlink()


def test_remove_never_follows_linked_workspace_dir(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / str(DOC)).mkdir(parents=True)
    (elsewhere / str(DOC) / "1.page").write_bytes(b"someone else's")
    storage_root(tmp_path).mkdir()
    (storage_root(tmp_path) / str(WS)).symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(PageFileRefused):
        remove_document_files(WS, DOC, page_paths(1))

    assert (elsewhere / str(DOC) / "1.page").exists()


def test_remove_never_unlinks_a_symlinked_page(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"someone else's")
    folder = letter_dir(tmp_path)
    (folder / "1.page").symlink_to(target)
    (folder / "2.page").write_bytes(b"sealed")

    assert remove_document_files(WS, DOC, page_paths(1, 2)) == 1

    assert target.read_bytes() == b"someone else's"
    assert (folder / "1.page").is_symlink()


def test_remove_never_removes_a_directory_named_as_a_page(tmp_path: Path) -> None:
    folder = letter_dir(tmp_path)
    (folder / "1.page").mkdir()
    (folder / "1.page" / "inside").write_bytes(b"kept")
    (folder / ".tmp-dir").mkdir()

    assert remove_document_files(WS, DOC, page_paths(1)) == 0

    assert (folder / "1.page" / "inside").exists()
    assert (folder / ".tmp-dir").is_dir()


def test_remove_refuses_pages_without_the_storage_root(tmp_path: Path) -> None:
    # An unmounted share: the letter's files may still be there once it is mounted.
    root = storage_root(tmp_path)
    assert not root.exists()

    with pytest.raises(PageFileRefused):
        remove_document_files(WS, DOC, page_paths(1))

    assert not root.exists()


def test_remove_refuses_pages_without_the_workspace_dir(tmp_path: Path) -> None:
    # An empty mount point: the root is there, the workspace folder is not.
    root = storage_root(tmp_path)
    root.mkdir()

    with pytest.raises(PageFileRefused):
        remove_document_files(WS, DOC, page_paths(1))

    assert os.listdir(root) == []


def test_remove_without_pages_needs_no_root_or_workspace_dir(tmp_path: Path) -> None:
    root = storage_root(tmp_path)
    assert not root.exists()

    assert remove_document_files(WS, DOC, []) == 0
    root.mkdir()
    assert remove_document_files(WS, DOC, []) == 0
    assert os.listdir(root) == []


def test_remove_of_missing_files_is_fine(tmp_path: Path) -> None:
    # A crash after the files went and before the rows: the letter folder is gone.
    (storage_root(tmp_path) / str(WS)).mkdir(parents=True)

    assert remove_document_files(WS, DOC, page_paths(1, 2)) == 0
    letter_dir(tmp_path)
    assert remove_document_files(WS, DOC, page_paths(1, 2)) == 0
    assert not (storage_root(tmp_path) / str(WS) / str(DOC)).exists()


def test_remove_with_no_paths_takes_only_leftovers(tmp_path: Path) -> None:
    folder = letter_dir(tmp_path)
    (folder / ".tmp-x").write_bytes(b"half")

    assert remove_document_files(WS, DOC, []) == 1
    assert not folder.exists()


def test_remove_leaves_other_letters_alone(tmp_path: Path) -> None:
    mine = letter_dir(tmp_path)
    sibling = letter_dir(tmp_path, WS, OTHER)
    other_ws = letter_dir(tmp_path, OTHER, DOC)
    for folder in (mine, sibling, other_ws):
        (folder / "1.page").write_bytes(b"sealed")
        (folder / ".tmp-x").write_bytes(b"half")

    assert remove_document_files(WS, DOC, page_paths(1)) == 2

    for folder in (sibling, other_ws):
        assert sorted(os.listdir(folder)) == [".tmp-x", "1.page"]
