"""Every destructive function of the back-up app refuses an unsafe path by itself.

Each refusal test arms the shared Recorder (tests/backup_support.py) right before the
call: every primitive that deletes, moves, creates, overwrites, chowns or starts a
process fails the test when called. An unsafe path therefore never reaches a real
primitive, whatever the function does wrong. The accepting cases and the
swap-between-check-and-use cases live under `tmp_path` only.
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pytest_django import Settings

from apps.backup import destination, restore, safepaths, sweep
from apps.backup.errors import BackupError
from apps.backup.privatefile import write_private
from tests.backup_support import Recorder

HOME = Path.home()
SYSTEM_PATHS = ["/", "/Users", "/data", "/etc", str(HOME), str(HOME.parent)]


@pytest.fixture
def configured(settings: Settings, tmp_path: Path) -> Path:
    """The three restored directories and the back-up mount, all under `tmp_path`."""
    for name in ("documents", "authority", "backup-config", "backup"):
        (tmp_path / name).mkdir()
    return tmp_path


def refused(
    recorder: Recorder,
    call: Callable[[], object],
    *,
    codes: tuple[str, ...] = ("unsafe_path",),
    allow: tuple[str, ...] = (),
) -> None:
    recorder.arm(allow=allow)
    try:
        with pytest.raises(BackupError) as caught:
            call()
    finally:
        recorder.disarm()
    assert caught.value.code in codes
    assert recorder.called == []


REFUSED = ("unsafe_path", "directory_not_allowed")


# --- restore._empty --------------------------------------------------------------------


@pytest.mark.parametrize(
    "directory", [*SYSTEM_PATHS, "relative/documents", "/data/documents", "/usr/local"]
)
def test_empty_refuses_a_system_home_relative_or_unconfigured_path(
    configured: Path, recorder: Recorder, directory: str
) -> None:
    refused(recorder, lambda: restore._empty(Path(directory)), codes=REFUSED)


@pytest.mark.parametrize("setting", ["/usr/local", "/opt/homebrew/var", "/data/other"])
def test_empty_refuses_a_configured_directory_off_the_fixed_list(
    configured: Path, recorder: Recorder, settings: Settings, setting: str
) -> None:
    settings.BRIEVIE_AUTHORITY_COPY_DIR = setting
    refused(recorder, lambda: restore._empty(Path(setting)), codes=("directory_not_allowed",))


def test_empty_refuses_a_directory_that_is_not_configured(
    configured: Path, recorder: Recorder
) -> None:
    other = configured / "other"
    other.mkdir()
    (other / "keep").write_text("keep")

    refused(recorder, lambda: restore._empty(other))
    refused(recorder, lambda: restore._empty(configured), codes=REFUSED)  # the test root


def test_empty_refuses_a_configured_directory_that_is_a_link(
    configured: Path, recorder: Recorder, settings: Settings
) -> None:
    real = configured / "real-documents"
    real.mkdir()
    (real / "keep.page").write_text("keep")
    (configured / "linked").symlink_to(real, target_is_directory=True)
    settings.BRIEVIE_STORAGE_ROOT = str(configured / "linked")

    refused(recorder, lambda: restore._empty(configured / "linked"))


@pytest.mark.parametrize("overlap", ["mount", "inside-mount", "contains-mount", "other-dir"])
def test_empty_refuses_a_directory_that_overlaps_the_mount_or_another(
    configured: Path, recorder: Recorder, settings: Settings, overlap: str
) -> None:
    documents = configured / "documents"
    if overlap == "mount":
        settings.BRIEVIE_BACKUP_MOUNT = str(documents)
    elif overlap == "inside-mount":
        settings.BRIEVIE_BACKUP_MOUNT = str(configured)
    elif overlap == "contains-mount":
        settings.BRIEVIE_BACKUP_MOUNT = str(documents / "backup")
    else:
        settings.BRIEVIE_AUTHORITY_COPY_DIR = str(documents / "authority")
    refused(recorder, lambda: restore._empty(documents))


@pytest.mark.parametrize("mount", ["", "relative"])
def test_an_empty_or_relative_mount_is_never_the_current_directory(
    configured: Path, recorder: Recorder, settings: Settings, mount: str
) -> None:
    settings.BRIEVIE_BACKUP_MOUNT = mount
    refused(recorder, lambda: restore._empty(configured / "documents"))
    refused(recorder, lambda: destination._chown_tree(Path(mount) / "restic", 10001))


def test_empty_never_follows_a_directory_swapped_after_the_check(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents = configured / "documents"
    (documents / "old.page").write_text("old")
    outside = configured / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    real_guard = safepaths.require_emptiable

    def guard_then_swap(directory: Path, **kwargs: object) -> Path:
        judged = real_guard(directory, **kwargs)  # type: ignore[arg-type]
        documents.rename(configured / "documents-moved")
        documents.symlink_to(outside, target_is_directory=True)
        return judged

    monkeypatch.setattr(safepaths, "require_emptiable", guard_then_swap)
    with pytest.raises((BackupError, OSError)):
        restore._empty(documents)
    assert (outside / "keep").read_text() == "keep"
    assert (configured / "documents-moved" / "old.page").exists()


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
        "/usr/caddy/pki/authorities/local",
    ],
)
def test_place_authority_refuses_anything_but_caddys_authority_directory(
    source: Path, recorder: Recorder, target: str
) -> None:
    refused(recorder, lambda: restore._place_authority(source, Path(target)), codes=REFUSED)


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


@pytest.mark.parametrize("hostile", ["link", "directory", "big"])
def test_place_authority_never_reads_through_a_link_or_a_strange_file(
    source: Path, recorder: Recorder, tmp_path: Path, hostile: str
) -> None:
    # A hostile snapshot must not make it copy any other file into Caddy's pki.
    secret = tmp_path / "admin-secret"
    secret.write_text("never copied")
    (source / "root.key").unlink()
    if hostile == "link":
        (source / "root.key").symlink_to(secret)
    elif hostile == "directory":
        (source / "root.key").mkdir()
    else:
        (source / "root.key").write_bytes(b"x" * (64 * 1024 + 1))
    target = tmp_path / "caddy-data" / "caddy" / "pki" / "authorities" / "local"

    refused(recorder, lambda: restore._place_authority(source, target))
    assert not target.exists()


def test_place_authority_removes_the_leaf_without_following_a_swapped_link(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    caddy = tmp_path / "caddy-data" / "caddy"
    target = caddy / "pki" / "authorities" / "local"
    leaf = caddy / "certificates" / "local"
    leaf.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    monkeypatch.setattr(os, "fchown", lambda *_args: None)
    real_leaf = safepaths.leaf_dir

    def leaf_then_swap(path: Path) -> Path:
        judged = real_leaf(path)
        (caddy / "certificates").rename(caddy / "certificates-moved")
        (caddy / "certificates").symlink_to(outside, target_is_directory=True)
        (outside / "local").mkdir()
        (outside / "local" / "keep").write_text("keep")
        return judged

    monkeypatch.setattr(safepaths, "leaf_dir", leaf_then_swap)
    with pytest.raises((BackupError, OSError)):
        restore._place_authority(source, target)
    assert (outside / "keep").read_text() == "keep"
    assert (outside / "local" / "keep").read_text() == "keep"


# --- restore._write_out and write_private ----------------------------------------------


@pytest.mark.parametrize(
    "out_dir",
    [
        *SYSTEM_PATHS,
        "relative/out",
        "/data/documents",
        "/data/backup-config",
        "/data/authority/x",
        "/backup",
    ],
)
def test_write_out_refuses_a_system_home_or_data_folder(
    configured: Path, recorder: Recorder, out_dir: str
) -> None:
    refused(recorder, lambda: restore._write_out(Path(out_dir), {"env": "X=1"}, None))


@pytest.mark.parametrize("which", ["documents", "backup-config", "authority", "backup"])
def test_write_out_refuses_a_configured_folder_or_one_inside_it(
    configured: Path, recorder: Recorder, which: str
) -> None:
    folder = configured / which
    refused(recorder, lambda: restore._write_out(folder, {"env": "X=1"}, None))
    refused(recorder, lambda: restore._write_out(folder / "out", {"env": "X=1"}, None))


def test_write_out_refuses_a_folder_that_holds_anything(
    configured: Path, recorder: Recorder
) -> None:
    out_dir = configured / "out"
    out_dir.mkdir()
    (out_dir / "env").write_text("the user's own file")

    refused(
        recorder,
        lambda: restore._write_out(out_dir, {"env": "X=1"}, None),
        codes=("out_dir_not_empty",),
        allow=("scandir",),
    )
    assert (out_dir / "env").read_text() == "the user's own file"


@pytest.mark.parametrize("name", ["../env", "a/env", "/etc/env", "..", ""])
def test_write_out_refuses_a_name_that_leaves_the_folder(
    configured: Path, recorder: Recorder, name: str
) -> None:
    out_dir = configured / "out"
    refused(recorder, lambda: restore._write_out(out_dir, {"manifest.json": "{}", name: "x"}, None))
    assert not out_dir.exists()


def test_write_out_rolls_back_only_what_it_created(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = configured / "out"
    calls: list[str] = []

    def fail_second(fd: int, uid: int, gid: int) -> None:
        calls.append("fchown")
        if len(calls) == 2:
            raise PermissionError

    monkeypatch.setattr(os, "fchown", fail_second)
    with pytest.raises(BackupError):
        restore._write_out(out_dir, {"manifest.json": "{}", "env": "X=1"}, (501, 20))
    assert not out_dir.exists()


def test_write_out_never_follows_a_folder_swapped_after_the_check(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = configured / "out"
    out_dir.mkdir()
    outside = configured / "outside"
    outside.mkdir()
    real_guard = safepaths.require_out_dir

    def guard_then_swap(path: Path, **kwargs: object) -> Path:
        judged = real_guard(path, **kwargs)  # type: ignore[arg-type]
        if out_dir.is_dir() and not out_dir.is_symlink():
            out_dir.rename(configured / "out-moved")
            out_dir.symlink_to(outside, target_is_directory=True)
        return judged

    monkeypatch.setattr(safepaths, "require_out_dir", guard_then_swap)
    with pytest.raises((BackupError, OSError)):
        restore._write_out(out_dir, {"env": "X=1"}, None)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("name", ["../escape", "a/b", "/etc/passwd", "", ".", ".."])
def test_write_private_refuses_a_name_with_a_path_in_it(
    configured: Path, recorder: Recorder, name: str
) -> None:
    refused(recorder, lambda: write_private(configured / "backup-config", name, "text"))


@pytest.mark.parametrize("directory", [*SYSTEM_PATHS, "/data/documents", "relative"])
def test_write_private_writes_only_into_the_config_folder(
    configured: Path, recorder: Recorder, directory: str
) -> None:
    refused(recorder, lambda: write_private(Path(directory), "env", "text"), codes=REFUSED)


def test_write_private_never_writes_through_a_link(configured: Path, recorder: Recorder) -> None:
    outside = configured / "outside"
    outside.mkdir()
    (configured / "linked-config").symlink_to(outside, target_is_directory=True)
    refused(recorder, lambda: write_private(configured / "linked-config", "env", "text"))


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


def test_chown_tree_never_hands_over_a_link_out_of_the_mount(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = configured / "backup" / "restic"
    (repository / "data").mkdir(parents=True)
    (repository / "data" / "escape").symlink_to(configured / "documents")
    owned: list[int] = []
    monkeypatch.setattr(os, "fchown", lambda fd, _u, _g: owned.append(os.fstat(fd).st_ino))
    monkeypatch.setattr(os, "chown", lambda *_args, **_kwargs: owned.append(-1))

    destination._chown_tree(repository, 10001)

    assert (configured / "documents").stat().st_ino not in owned
    assert -1 not in owned
    assert sorted(owned) == sorted(p.stat().st_ino for p in (repository, repository / "data"))


# --- sweep.delete_orphans --------------------------------------------------------------


ORPHANS = [
    sweep.Orphan(
        "00000000-0000-0000-0000-000000000000/x/1.page", 1, datetime.now(UTC) - timedelta(days=2)
    )
]


@pytest.mark.parametrize("root", [*SYSTEM_PATHS, "relative/documents", "/data/documents"])
def test_delete_orphans_refuses_a_root_other_than_the_storage_root(
    configured: Path, recorder: Recorder, root: str
) -> None:
    refused(recorder, lambda: sweep.delete_orphans(ORPHANS, Path(root)), codes=REFUSED)


def test_delete_orphans_refuses_an_unconfigured_root_under_tmp(
    configured: Path, recorder: Recorder
) -> None:
    refused(recorder, lambda: sweep.delete_orphans(ORPHANS, configured / "authority"))


@pytest.mark.parametrize("setting", ["/usr/local/documents", "/srv/brievie/documents"])
def test_delete_orphans_refuses_a_custom_storage_root_off_the_fixed_list(
    configured: Path, recorder: Recorder, settings: Settings, setting: str
) -> None:
    settings.BRIEVIE_STORAGE_ROOT = setting
    refused(
        recorder,
        lambda: sweep.delete_orphans(ORPHANS, Path(setting)),
        codes=("directory_not_allowed",),
    )


def test_delete_orphans_never_follows_a_root_swapped_after_the_check(
    configured: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents = configured / "documents"
    outside = configured / "outside"
    relative = "00000000-0000-0000-0000-000000000000/11111111-1111-1111-1111-111111111111/1.page"
    victim = outside / relative
    victim.parent.mkdir(parents=True)
    victim.write_text("not ours")
    old = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(victim, (old, old))
    real_guard = safepaths.require_emptiable

    def guard_then_swap(directory: Path, **kwargs: object) -> Path:
        judged = real_guard(directory, **kwargs)  # type: ignore[arg-type]
        documents.rename(configured / "documents-moved")
        documents.symlink_to(outside, target_is_directory=True)
        return judged

    monkeypatch.setattr(safepaths, "require_emptiable", guard_then_swap)
    orphans = [sweep.Orphan(relative, 1, datetime.now(UTC) - timedelta(days=2))]
    with pytest.raises((BackupError, OSError)):
        sweep.delete_orphans(orphans, documents)
    assert victim.read_text() == "not ours"


def test_place_authority_makes_nothing_above_caddys_own_folder(
    source: Path, recorder: Recorder, tmp_path: Path
) -> None:
    caddy_data = tmp_path / "caddy-data"
    caddy_data.mkdir()
    target = caddy_data / "caddy" / "pki" / "authorities" / "local"

    refused(recorder, lambda: restore._place_authority(source, target), codes=("caddy_not_ready",))
    assert list(caddy_data.iterdir()) == []
