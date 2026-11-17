"""Every destructive function of the back-up app refuses an unsafe path by itself.

Each test arms a recorder first: the primitives that delete, move, create, overwrite
or chown are replaced by functions that fail the test when called. An unsafe path
therefore never reaches a real primitive, whatever the function does wrong. No
accepting case lives here (those run under `tmp_path` in the module's own tests).
"""

import os
import shutil
import tempfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pytest_django import Settings

from apps.backup import destination, restore, sweep
from apps.backup.errors import BackupError
from apps.backup.privatefile import write_private

HOME = Path.home()

_PRIMITIVES: list[tuple[object, str]] = [
    (os, "scandir"),
    (os, "unlink"),
    (os, "remove"),
    (os, "rmdir"),
    (os, "replace"),
    (os, "rename"),
    (os, "chown"),
    (os, "lchown"),
    (os, "chmod"),
    (os, "open"),
    (os, "mkdir"),
    (os, "makedirs"),
    (shutil, "rmtree"),
    (shutil, "move"),
    (tempfile, "mkstemp"),
    (Path, "unlink"),
    (Path, "rmdir"),
    (Path, "mkdir"),
    (Path, "chmod"),
    (Path, "write_text"),
    (Path, "write_bytes"),
    (Path, "rename"),
    (Path, "replace"),
]


class Recorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self.called: list[str] = []

    def _refuse(self, name: str) -> Callable[..., None]:
        def refuse(*_args: object, **_kwargs: object) -> None:
            self.called.append(name)
            raise AssertionError(f"{name} was called")

        return refuse

    def arm(self, *, allow: tuple[str, ...] = ()) -> None:
        tempfile.gettempdir()  # cached before os.open is replaced
        for owner, name in _PRIMITIVES:
            if name not in allow:
                self._monkeypatch.setattr(owner, name, self._refuse(name))

    def disarm(self) -> None:
        self._monkeypatch.undo()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Iterator[Recorder]:
    armed = Recorder(monkeypatch)
    yield armed
    armed.disarm()
    assert armed.called == []


@pytest.fixture
def configured(settings: Settings, tmp_path: Path) -> Path:
    """The three restored directories and the back-up mount, all under `tmp_path`."""
    settings.BRIEVIE_STORAGE_ROOT = str(tmp_path / "documents")
    settings.BRIEVIE_AUTHORITY_COPY_DIR = str(tmp_path / "authority")
    settings.BRIEVIE_BACKUP_CONFIG_DIR = str(tmp_path / "backup-config")
    settings.BRIEVIE_BACKUP_MOUNT = str(tmp_path / "backup")
    for name in ("documents", "authority", "backup-config", "backup"):
        (tmp_path / name).mkdir()
    return tmp_path


def refused(recorder: Recorder, call: Callable[[], object], *, allow: tuple[str, ...] = ()) -> None:
    recorder.arm(allow=allow)
    try:
        with pytest.raises(BackupError) as caught:
            call()
    finally:
        recorder.disarm()
    assert caught.value.code == "unsafe_path"
    assert recorder.called == []


SYSTEM_PATHS = ["/", "/Users", "/data", "/etc", str(HOME), str(HOME.parent)]


# --- restore._empty --------------------------------------------------------------------


@pytest.mark.parametrize("directory", [*SYSTEM_PATHS, "relative/documents", "/data/documents"])
def test_empty_refuses_a_system_home_relative_or_unconfigured_path(
    configured: Path, recorder: Recorder, directory: str
) -> None:
    refused(recorder, lambda: restore._empty(Path(directory)))


def test_empty_refuses_a_directory_that_is_not_configured(
    configured: Path, recorder: Recorder
) -> None:
    other = configured / "other"
    other.mkdir()
    (other / "keep").write_text("keep")

    refused(recorder, lambda: restore._empty(other))
    refused(recorder, lambda: restore._empty(configured))


def test_empty_refuses_a_configured_directory_that_is_a_link(
    configured: Path, recorder: Recorder, settings: Settings
) -> None:
    real = configured / "real-documents"
    real.mkdir()
    (real / "keep.page").write_text("keep")
    (configured / "linked").symlink_to(real, target_is_directory=True)
    settings.BRIEVIE_STORAGE_ROOT = str(configured / "linked")

    refused(recorder, lambda: restore._empty(configured / "linked"))


# --- restore._place_authority ----------------------------------------------------------


@pytest.fixture
def source(configured: Path) -> Path:
    authority = configured / "authority"
    for name in ("root.crt", "root.key"):
        (authority / name).write_text(name)
    return authority


@pytest.mark.parametrize(
    "target",
    [
        *SYSTEM_PATHS,
        "caddy/pki/authorities/local",
        "/caddy-data/caddy/pki/authorities/other",
        "/caddy-data/caddy/pki/authorities/local/..",
    ],
)
def test_place_authority_refuses_anything_but_caddys_authority_directory(
    source: Path, recorder: Recorder, target: str
) -> None:
    refused(recorder, lambda: restore._place_authority(source, Path(target)))


@pytest.mark.parametrize("linked", ["caddy/pki/authorities/local", "caddy", "caddy/certificates"])
def test_place_authority_refuses_a_target_or_leaf_reached_through_a_link(
    source: Path, recorder: Recorder, tmp_path: Path, linked: str
) -> None:
    caddy_data = tmp_path / "caddy-data"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = caddy_data / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(elsewhere, target_is_directory=True)
    target = caddy_data / "caddy" / "pki" / "authorities" / "local"

    refused(recorder, lambda: restore._place_authority(source, target))
    assert list(elsewhere.iterdir()) == []


# --- restore._write_out and write_private ----------------------------------------------


@pytest.mark.parametrize("out_dir", ["/"])
def test_write_out_refuses_the_root(recorder: Recorder, out_dir: str) -> None:
    refused(recorder, lambda: restore._write_out(Path(out_dir), {"env": "X=1"}, None))


@pytest.mark.parametrize("name", ["../env", "a/env", "/etc/env", "..", ""])
def test_write_out_refuses_a_name_that_leaves_the_folder(
    recorder: Recorder, tmp_path: Path, name: str
) -> None:
    out_dir = tmp_path / "out"
    refused(recorder, lambda: restore._write_out(out_dir, {"manifest.json": "{}", name: "x"}, None))
    assert not out_dir.exists()


def test_write_out_refuses_a_file_name_that_is_a_link_out_of_the_folder(
    recorder: Recorder, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    outside = tmp_path / "outside-env"
    outside.write_text("keep")
    (out_dir / "env").symlink_to(outside)

    refused(recorder, lambda: restore._write_out(out_dir, {"env": "X=1"}, None))
    assert outside.read_text() == "keep"


@pytest.mark.parametrize("name", ["../escape", "a/b", "/etc/passwd", "", ".", ".."])
def test_write_private_refuses_a_name_with_a_path_in_it(
    recorder: Recorder, tmp_path: Path, name: str
) -> None:
    refused(recorder, lambda: write_private(tmp_path, name, "text"))


# --- destination._chown_tree -----------------------------------------------------------


@pytest.mark.parametrize("root", [*SYSTEM_PATHS, "relative/restic"])
def test_chown_tree_refuses_a_path_outside_the_mount(
    configured: Path, recorder: Recorder, root: str
) -> None:
    refused(recorder, lambda: destination._chown_tree(Path(root), 10001))


def test_chown_tree_refuses_the_mount_itself_and_its_neighbours(
    configured: Path, recorder: Recorder
) -> None:
    refused(recorder, lambda: destination._chown_tree(configured / "backup", 10001))
    refused(recorder, lambda: destination._chown_tree(configured / "documents", 10001))


def test_chown_tree_refuses_a_link_out_of_the_mount_before_any_chown(
    configured: Path, recorder: Recorder
) -> None:
    repository = configured / "backup" / "restic"
    (repository / "data").mkdir(parents=True)
    (repository / "data" / "escape").symlink_to(configured / "documents")

    # Listing is allowed here (the walk must find the link); chowning is not.
    refused(recorder, lambda: destination._chown_tree(repository, 10001), allow=("scandir",))


# --- sweep.delete_orphans --------------------------------------------------------------


@pytest.mark.parametrize("root", [*SYSTEM_PATHS, "relative/documents", "/data/documents"])
def test_delete_orphans_refuses_a_root_other_than_the_storage_root(
    configured: Path, recorder: Recorder, root: str
) -> None:
    old = datetime.now(UTC) - timedelta(days=2)
    orphans = [sweep.Orphan("00000000-0000-0000-0000-000000000000/x/1.page", 1, old)]

    refused(recorder, lambda: sweep.delete_orphans(orphans, Path(root)))


def test_delete_orphans_refuses_an_unconfigured_root_under_tmp(
    configured: Path, recorder: Recorder
) -> None:
    old = datetime.now(UTC) - timedelta(days=2)
    orphans = [sweep.Orphan("00000000-0000-0000-0000-000000000000/x/1.page", 1, old)]

    refused(recorder, lambda: sweep.delete_orphans(orphans, configured / "authority"))
