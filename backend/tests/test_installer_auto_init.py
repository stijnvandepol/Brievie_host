"""BRIEVIE_BACKUP_AUTO_INIT: only an empty folder, only a folder, only when on."""

import os
from pathlib import Path

from apps.installer.autoinit import folder_is_empty, should_auto_init


def test_an_empty_folder_is_prepared_when_the_setting_is_on(tmp_path: Path) -> None:
    assert should_auto_init(enabled=True, kind="folder", mount=tmp_path)


def test_the_setting_off_never_prepares(tmp_path: Path) -> None:
    assert not should_auto_init(enabled=False, kind="folder", mount=tmp_path)


def test_a_remote_destination_is_never_prepared_here(tmp_path: Path) -> None:
    assert not should_auto_init(enabled=True, kind="s3", mount=tmp_path)
    assert not should_auto_init(enabled=True, kind="rest", mount=tmp_path)
    assert not should_auto_init(enabled=True, kind="", mount=tmp_path)


def test_a_folder_with_a_marker_or_any_file_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / "brievie-backup.json").write_text("{}")
    assert not should_auto_init(enabled=True, kind="folder", mount=tmp_path)
    assert not folder_is_empty(tmp_path)


def test_a_folder_with_a_hidden_file_is_not_empty(tmp_path: Path) -> None:
    (tmp_path / ".something").write_text("x")
    assert not folder_is_empty(tmp_path)


def test_a_missing_folder_and_a_link_are_not_prepared(tmp_path: Path) -> None:
    assert not folder_is_empty(tmp_path / "nope")
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    os.symlink(target, link)
    assert not folder_is_empty(link)
