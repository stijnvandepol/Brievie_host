from pathlib import Path

import pytest
from pytest_django import Settings

from apps.documents.storage import FileSystemStorage, default_storage


def test_write_read_delete_round_trip(tmp_path: Path) -> None:
    storage = FileSystemStorage(tmp_path)

    storage.write("ws/doc/1.page", b"versleuteld")

    assert storage.read("ws/doc/1.page") == b"versleuteld"
    assert (tmp_path / "ws" / "doc" / "1.page").read_bytes() == b"versleuteld"
    storage.delete("ws/doc/1.page")
    assert not (tmp_path / "ws" / "doc" / "1.page").exists()


def test_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    FileSystemStorage(tmp_path).write("ws/doc/1.page", b"x")

    assert [p.name for p in (tmp_path / "ws" / "doc").iterdir()] == ["1.page"]


def test_delete_of_a_missing_file_is_fine(tmp_path: Path) -> None:
    FileSystemStorage(tmp_path).delete("ws/doc/1.page")


@pytest.mark.parametrize("path", ["../x", "ws/../../x", "/etc/passwd", "", "ws/./../../x"])
def test_paths_outside_the_root_are_refused(tmp_path: Path, path: str) -> None:
    storage = FileSystemStorage(tmp_path / "root")
    with pytest.raises(ValueError, match="path"):
        storage.read(path)
    with pytest.raises(ValueError, match="path"):
        storage.write(path, b"x")
    with pytest.raises(ValueError, match="path"):
        storage.delete(path)


def test_default_storage_uses_the_configured_root(settings: Settings, tmp_path: Path) -> None:
    settings.BRIEVIE_STORAGE_ROOT = str(tmp_path / "docs")

    default_storage().write("a/b/1.page", b"x")

    assert (tmp_path / "docs" / "a" / "b" / "1.page").is_file()
