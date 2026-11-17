"""The descriptor helpers of safepaths, only ever under `tmp_path`.

They open every component with O_NOFOLLOW and work relative to directory
descriptors, so a directory swapped for a link after it was checked is never
followed. Every test keeps a file outside the tree and checks it survives.
"""

import os
import stat
from pathlib import Path

import pytest

from apps.backup import safepaths
from apps.backup.errors import BackupError


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A directory with a file that must survive every test."""
    directory = tmp_path / "outside"
    directory.mkdir()
    (directory / "keep").write_text("keep")
    return directory


def survived(outside: Path) -> bool:
    return (outside / "keep").read_text() == "keep"


def swap_for_link(directory: Path, target: Path) -> None:
    """Replace `directory` by a link to `target` (the attack between check and use)."""
    directory.rename(directory.with_name(directory.name + "-moved"))
    directory.symlink_to(target, target_is_directory=True)


# --- open_dir_nofollow ---------------------------------------------------------------------


def test_a_directory_is_opened_and_matches_what_was_checked(tmp_path: Path) -> None:
    directory = tmp_path / "a" / "b"
    directory.mkdir(parents=True)
    expected = safepaths.identity(directory)
    fd = safepaths.open_dir_nofollow(directory, expect=expected)
    try:
        info = os.fstat(fd)
        assert (info.st_dev, info.st_ino) == expected
    finally:
        os.close(fd)


def test_a_link_anywhere_below_the_top_is_refused(tmp_path: Path, outside: Path) -> None:
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(BackupError) as caught:
        safepaths.open_dir_nofollow(tmp_path / "link")
    assert caught.value.code == "unsafe_path"
    with pytest.raises(BackupError):
        safepaths.open_dir_nofollow(tmp_path / "link" / "deeper", create=0o700)
    assert sorted(p.name for p in outside.iterdir()) == ["keep"]


def test_a_directory_swapped_after_the_check_is_refused(tmp_path: Path, outside: Path) -> None:
    directory = tmp_path / "documents"
    directory.mkdir()
    expected = safepaths.identity(directory)
    swap_for_link(directory, outside)

    with pytest.raises(BackupError) as caught:
        safepaths.open_dir_nofollow(directory, expect=expected)
    assert caught.value.code == "unsafe_path"
    assert survived(outside)


def test_a_directory_renamed_in_place_after_the_check_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "documents"
    directory.mkdir()
    expected = safepaths.identity(directory)
    directory.rename(tmp_path / "old")
    directory.mkdir()  # another real directory under the same name

    with pytest.raises(BackupError):
        safepaths.open_dir_nofollow(directory, expect=expected)


def test_missing_components_are_created_private(tmp_path: Path) -> None:
    directory = tmp_path / "new" / "deeper"
    fd = safepaths.open_dir_nofollow(directory, create=0o700)
    os.close(fd)
    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_a_relative_path_is_never_opened() -> None:
    with pytest.raises(BackupError):
        safepaths.open_dir_nofollow(Path("relative/dir"))


# --- empty_dir and remove_tree_at -------------------------------------------------------------


def test_emptying_removes_everything_but_follows_no_link(tmp_path: Path, outside: Path) -> None:
    directory = tmp_path / "documents"
    (directory / "a" / "b").mkdir(parents=True)
    (directory / "a" / "b" / "1.page").write_text("x")
    (directory / ".tmp-x").write_text("x")
    (directory / "link-dir").symlink_to(outside, target_is_directory=True)
    (directory / "a" / "link-file").symlink_to(outside / "keep")

    fd = safepaths.open_dir_nofollow(directory)
    try:
        safepaths.empty_dir(fd)
    finally:
        os.close(fd)

    assert directory.is_dir()
    assert list(directory.iterdir()) == []
    assert survived(outside)


def test_a_subdirectory_swapped_for_a_link_during_removal_is_not_followed(
    tmp_path: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "documents"
    (directory / "sub").mkdir(parents=True)
    (directory / "sub" / "1.page").write_text("x")
    real_stat = os.stat
    swapped: list[bool] = []

    def stat_then_swap(path: object, *args: object, **kwargs: object) -> os.stat_result:
        result = real_stat(path, *args, **kwargs)  # type: ignore[arg-type]
        if path == "sub" and not swapped:
            swapped.append(True)
            swap_for_link(directory / "sub", outside)
        return result

    monkeypatch.setattr(os, "stat", stat_then_swap)
    fd = safepaths.open_dir_nofollow(directory)
    try:
        with pytest.raises((BackupError, OSError)):
            safepaths.empty_dir(fd)
    finally:
        os.close(fd)
        monkeypatch.undo()
    assert swapped
    assert survived(outside)


# --- clean_tree ------------------------------------------------------------------------------


def test_cleaning_removes_links_and_special_files_and_clears_setuid(
    tmp_path: Path, outside: Path
) -> None:
    directory = tmp_path / "documents"
    (directory / "ws").mkdir(parents=True)
    page = directory / "ws" / "1.page"
    page.write_text("page")
    page.chmod(0o4755)  # setuid
    sticky = directory / "sticky"
    sticky.mkdir()
    sticky.chmod(0o1777)
    (directory / "ws" / "link").symlink_to(outside / "keep")
    (directory / "dirlink").symlink_to(outside, target_is_directory=True)
    expected_removed = 2
    if hasattr(os, "mkfifo"):
        os.mkfifo(directory / "ws" / "fifo")
        expected_removed += 1

    fd = safepaths.open_dir_nofollow(directory)
    try:
        result = safepaths.clean_tree(fd, owner=None)
    finally:
        os.close(fd)

    assert result.removed == expected_removed
    assert sorted(p.name for p in (directory / "ws").iterdir()) == ["1.page"]
    assert not (directory / "dirlink").exists()
    assert stat.S_IMODE(page.stat().st_mode) == 0o755
    assert stat.S_IMODE(sticky.stat().st_mode) == 0o777
    assert survived(outside)


def test_cleaning_hands_everything_to_the_owner_but_skips_hard_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "documents"
    (directory / "ws").mkdir(parents=True)
    (directory / "ws" / "1.page").write_text("page")
    (directory / "twice").write_text("x")
    os.link(directory / "twice", tmp_path / "elsewhere")
    owned: list[int] = []
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: owned.append(os.fstat(fd).st_ino))

    fd = safepaths.open_dir_nofollow(directory)
    try:
        result = safepaths.clean_tree(fd, owner=10001)
    finally:
        os.close(fd)

    inodes = {p.stat().st_ino for p in (directory, directory / "ws", directory / "ws" / "1.page")}
    assert set(owned) == inodes
    assert (directory / "twice").stat().st_ino not in owned
    assert result.skipped == 1


# --- read_regular_at -------------------------------------------------------------------------


def test_reading_refuses_a_link_a_directory_and_a_big_file(tmp_path: Path, outside: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "root.crt").write_text("cert")
    (source / "root.key").symlink_to(outside / "keep")
    (source / "big").write_bytes(b"x" * 100)
    (source / "dir").mkdir()

    fd = safepaths.open_dir_nofollow(source)
    try:
        assert safepaths.read_regular_at(fd, "root.crt", limit=64) == b"cert"
        assert safepaths.read_regular_at(fd, "missing", limit=64) is None
        for name in ("root.key", "big", "dir"):
            with pytest.raises(BackupError):
                safepaths.read_regular_at(fd, name, limit=64)
    finally:
        os.close(fd)
