"""The path guards of the back-up app, called as pure functions.

Nothing here deletes, moves or overwrites anything: the guards only judge a path
(they may stat it). Every accepting case lives under `tmp_path` or is a fixed
production path that is only judged, never touched. The home directory and the
test root (installed by conftest with the test's own tmp_path) are changed through the
environment and the hook, never on disk.
"""

import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from apps.backup import safepaths
from apps.backup.errors import BackupError
from apps.backup.safepaths import (
    CADDY_AUTHORITY,
    CADDY_LEAF,
    PRODUCTION_DIRECTORIES,
    configured_path,
    leaf_dir,
    require_caddy_authority_dir,
    require_emptiable,
    require_inside,
    require_out_dir,
    require_plain_name,
)

HOME = Path.home()
UNSAFE = "Brievie weigert deze map te wissen of te overschrijven, omdat dat niet veilig is."


def refused(call: Callable[[], object], code: str = "unsafe_path") -> None:
    with pytest.raises(BackupError) as caught:
        call()
    assert caught.value.code == code
    if code == "unsafe_path":
        assert caught.value.message == UNSAFE


def not_allowed(call: Callable[[], object]) -> None:
    refused(call, "directory_not_allowed")


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home directory under tmp_path, from both $HOME and the password database."""
    home = tmp_path / "homes" / "someone"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(safepaths, "_password_home", lambda: str(home))
    return home


# --- configured paths ------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "relative", "./data", "data/documents", "/data\0/x"])
def test_a_configured_path_must_be_absolute(value: str) -> None:
    refused(lambda: configured_path(value))


def test_an_absolute_configured_path_is_taken_as_it_is() -> None:
    assert configured_path("/data/documents") == Path("/data/documents")


# --- require_emptiable: structure -------------------------------------------------------


@pytest.mark.parametrize(
    "directory",
    [
        "/",
        "/Users",
        "/users",
        "/USERS/someone",
        "/data",
        "/home",
        "/home/someone",
        "/Home/Someone/documents",
        "/root",
        "/ROOT/documents",
        "/var/root/documents",
        str(HOME),
        str(HOME.parent),
        str(HOME).lower(),
        str(HOME).upper(),
        str(HOME / "Documents" / "brievie-documents"),
        "relative/documents",
        "documents",
        "/data/documents/..",
        "/data/docu*",
        "/data/documents?",
        "/data/[d]ocuments",
    ],
)
def test_a_system_home_relative_or_glob_path_is_never_emptiable(directory: str) -> None:
    # Even when the caller lists it as allowed: the guard does not trust its caller.
    refused(lambda: require_emptiable(Path(directory), allowed=[Path(directory)]))


def test_a_path_that_is_or_goes_through_a_link_is_never_emptiable(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "documents").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    holder = tmp_path / "holder"
    holder.mkdir()
    (holder / "up").symlink_to("/", target_is_directory=True)

    refused(lambda: require_emptiable(link, allowed=[link]))
    refused(lambda: require_emptiable(link, allowed=[real]))
    refused(lambda: require_emptiable(link / "documents", allowed=[real / "documents"]))
    refused(lambda: require_emptiable(holder / "up" / "tmp", allowed=[holder / "up" / "tmp"]))


# --- require_emptiable: the fixed list ---------------------------------------------------


@pytest.mark.parametrize("directory", ["/usr/local", "/opt/homebrew/var", "/data/other", "/etc/x"])
def test_a_configured_directory_off_the_fixed_list_is_not_allowed(directory: str) -> None:
    not_allowed(lambda: require_emptiable(Path(directory), allowed=[Path(directory)]))


@pytest.mark.parametrize("directory", PRODUCTION_DIRECTORIES)
def test_the_fixed_production_directories_are_emptiable_when_configured(
    directory: Path,
) -> None:
    # Only judged (stat and realpath of a path that need not exist), never touched.
    assert require_emptiable(directory, allowed=list(PRODUCTION_DIRECTORIES)) == directory


def test_a_fixed_directory_that_is_not_the_configured_one_is_refused() -> None:
    refused(lambda: require_emptiable(Path("/data/documents"), allowed=[Path("/data/authority")]))


def test_the_sweep_list_holds_only_the_documents() -> None:
    fixed = [Path("/data/documents")]
    refused(
        lambda: require_emptiable(Path("/data/authority"), allowed=[], fixed=fixed),
        "directory_not_allowed",
    )


def test_a_configured_directory_under_the_temporary_folder_is_emptiable(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    authority = tmp_path / "authority"  # need not exist yet

    assert require_emptiable(documents, allowed=[documents, authority]) == documents
    assert require_emptiable(authority, allowed=[documents, authority]) == authority


def test_a_directory_not_in_the_allowed_list_is_refused(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()

    refused(lambda: require_emptiable(documents, allowed=[tmp_path / "authority"]))
    refused(lambda: require_emptiable(documents, allowed=[]))
    refused(lambda: require_emptiable(documents / "inner", allowed=[documents]))
    refused(lambda: require_emptiable(documents, allowed=["relative/documents"]))


def test_the_comparison_uses_resolved_paths(tmp_path: Path) -> None:
    # tmp_path on macOS is reached through the system link /var -> /private/var: both
    # spellings are accepted, whichever one the settings use.
    documents = tmp_path / "documents"
    documents.mkdir()
    resolved = documents.resolve()
    spellings = {documents, resolved}
    if resolved.parts[1] == "private" and Path("/", *resolved.parts[2:]).resolve() == resolved:
        spellings.add(Path("/", *resolved.parts[2:]))

    for directory in spellings:
        for configured in spellings:
            assert require_emptiable(directory, allowed=[configured]) == directory


# --- require_emptiable: no overlap -------------------------------------------------------


def test_a_directory_overlapping_the_mount_or_another_directory_is_refused(
    tmp_path: Path,
) -> None:
    documents = tmp_path / "documents"
    mount = tmp_path / "backup"
    inner = documents / "backup"

    refused(lambda: require_emptiable(documents, allowed=[documents], avoid=[documents]))
    refused(lambda: require_emptiable(documents, allowed=[documents], avoid=[inner]))
    refused(lambda: require_emptiable(inner, allowed=[inner], avoid=[documents]))
    refused(lambda: require_emptiable(documents, allowed=[documents], avoid=[""]))
    refused(lambda: require_emptiable(documents, allowed=[documents], avoid=["relative"]))
    assert require_emptiable(documents, allowed=[documents], avoid=[mount]) == documents


# --- the home directory -------------------------------------------------------------------


def test_the_home_directory_is_found_by_inode_whatever_its_name(
    fake_home: Path, tmp_path: Path
) -> None:
    alias = tmp_path / "Homes"  # the same directory under another spelling, if any
    refused(lambda: require_emptiable(fake_home, allowed=[fake_home]))
    refused(lambda: require_emptiable(fake_home.parent, allowed=[fake_home.parent]))
    refused(lambda: require_emptiable(fake_home / "documents", allowed=[fake_home / "documents"]))
    if alias.exists():  # a case-insensitive file system
        refused(lambda: require_emptiable(alias / "someone", allowed=[alias / "someone"]))


def test_the_password_database_still_counts_when_home_is_empty(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for value in ("", "relative/home"):
        monkeypatch.setenv("HOME", value)
        refused(lambda: require_emptiable(fake_home, allowed=[fake_home]))


def test_home_from_the_environment_still_counts_without_a_password_entry(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safepaths, "_password_home", lambda: None)
    refused(lambda: require_emptiable(fake_home, allowed=[fake_home]))


def test_a_home_at_the_root_does_not_make_everything_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A service user without a home has HOME=/: the root rule already covers "/".
    monkeypatch.setenv("HOME", "/")
    monkeypatch.setattr(safepaths, "_password_home", lambda: "/")
    documents = tmp_path / "documents"

    assert require_emptiable(documents, allowed=[documents]) == documents
    assert require_emptiable(Path("/data/documents"), allowed=["/data/documents"]) == Path(
        "/data/documents"
    )
    refused(lambda: require_emptiable(Path("/"), allowed=[Path("/")]))


# --- the test root ---------------------------------------------------------------------------


@pytest.fixture
def no_test_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production as it runs: no test root installed (conftest installs tmp_path)."""
    monkeypatch.setattr(safepaths, "_test_root", None)


def test_without_the_hook_nothing_under_the_temporary_folder_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_test_root: None
) -> None:
    # Whatever TMPDIR says: only the fixed list counts in production.
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    documents = tmp_path / "documents"
    in_system_temp = Path(tempfile.gettempdir()) / "brievie-documents"
    not_allowed(lambda: require_emptiable(documents, allowed=[documents]))
    not_allowed(lambda: require_emptiable(in_system_temp, allowed=[in_system_temp]))
    with pytest.raises(BackupError):
        require_caddy_authority_dir(tmp_path / "caddy" / "pki" / "authorities" / "local")
    assert require_emptiable(Path("/data/documents"), allowed=["/data/documents"]) == Path(
        "/data/documents"
    )


@pytest.mark.parametrize(
    "root",
    ["/", "/data", "/usr", "relative/tmp", "", str(HOME), str(HOME.parent), "/tmp"],  # noqa: S108
)
def test_the_hook_refuses_an_unsafe_root(root: str, no_test_root: None) -> None:
    refused(lambda: safepaths.allow_test_root(Path(root)))
    assert safepaths.installed_test_root() is None


def test_the_hook_refuses_a_root_above_a_production_directory(no_test_root: None) -> None:
    refused(lambda: safepaths.allow_test_root(Path("/caddy-data/caddy")))
    refused(lambda: safepaths.allow_test_root(Path("/data/documents/x")))


def test_the_hook_refuses_a_root_in_or_at_a_home_directory(
    fake_home: Path, no_test_root: None
) -> None:
    refused(lambda: safepaths.allow_test_root(fake_home))
    refused(lambda: safepaths.allow_test_root(fake_home / "tmp"))
    refused(lambda: safepaths.allow_test_root(fake_home.parent))


def test_the_test_root_itself_is_never_emptiable(tmp_path: Path) -> None:
    # conftest installed tmp_path as the test root: only paths strictly inside it count.
    assert safepaths.installed_test_root() == tmp_path.resolve()
    not_allowed(lambda: require_emptiable(tmp_path, allowed=[tmp_path]))


# --- require_inside -----------------------------------------------------------------------


def test_a_path_strictly_inside_the_root_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)

    assert require_inside(root / "a", root) == root / "a"
    assert require_inside(root / "a" / "b.txt", root) == root / "a" / "b.txt"
    assert require_inside(root / "not-yet", root) == root / "not-yet"
    assert require_inside(Path("/backup/restic"), Path("/backup")) == Path("/backup/restic")


@pytest.mark.parametrize(
    "candidate",
    ["{root}", "{root}/..", "{root}/../other", "{tmp}/other", "/", "/etc/passwd"],
)
def test_a_path_outside_or_at_the_root_is_refused(tmp_path: Path, candidate: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = Path(candidate.format(root=root, tmp=tmp_path))

    refused(lambda: require_inside(path, root))


def test_a_link_inside_that_leads_outside_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    refused(lambda: require_inside(root / "escape", root))
    refused(lambda: require_inside(root / "escape" / "file", root))


@pytest.mark.parametrize(
    "root", ["/", "", ".", "relative", "/Users", "/home", "/root", str(HOME), str(HOME.parent)]
)
def test_a_relative_empty_or_home_level_root_is_refused(root: str) -> None:
    refused(lambda: require_inside(Path(root) / "x" / "y", Path(root)))


def test_a_home_root_is_refused_by_inode(fake_home: Path) -> None:
    refused(lambda: require_inside(fake_home / "file", fake_home))
    refused(lambda: require_inside(fake_home / "file", fake_home.parent))


# --- the Caddy authority ------------------------------------------------------------------


def test_a_caddy_authority_directory_is_accepted(tmp_path: Path) -> None:
    target = tmp_path / "caddy-data" / "caddy" / "pki" / "authorities" / "local"

    assert require_caddy_authority_dir(target) == target
    assert leaf_dir(target) == tmp_path / "caddy-data" / "caddy" / "certificates" / "local"
    assert require_caddy_authority_dir(CADDY_AUTHORITY) == CADDY_AUTHORITY
    assert leaf_dir(CADDY_AUTHORITY) == CADDY_LEAF


@pytest.mark.parametrize(
    "target",
    [
        "/",
        "/data",
        "caddy/pki/authorities/local",
        "/caddy-data/caddy/pki/authorities/other",
        "/caddy-data/caddy/pki/authorities",
        "/caddy-data/caddy/pki/authorities/local/..",
        "/caddy-data/caddy/pki/authorities/local/x",
        "/caddy-data/caddy/../caddy/pki/authorities/local",
        "/caddy-data/kaddy/pki/authorities/local",
        "/usr/caddy/pki/authorities/local",
        "/caddy/pki/authorities/local",
        str(HOME / "caddy" / "pki" / "authorities" / "local"),
        str(HOME),
    ],
)
def test_anything_else_is_not_a_caddy_authority_directory(target: str) -> None:
    with pytest.raises(BackupError) as caught:
        require_caddy_authority_dir(Path(target))
    assert caught.value.code in {"unsafe_path", "directory_not_allowed"}
    with pytest.raises(BackupError):
        leaf_dir(Path(target))


@pytest.mark.parametrize("linked", ["local", "authorities", "pki", "caddy"])
def test_a_caddy_authority_directory_through_a_link_is_refused(tmp_path: Path, linked: str) -> None:
    real = tmp_path / "elsewhere"
    real.mkdir()
    parts = ["caddy", "pki", "authorities", "local"]
    base = tmp_path / "caddy-data"
    above = parts[: parts.index(linked)]
    (base.joinpath(*above)).mkdir(parents=True, exist_ok=True)
    base.joinpath(*above, linked).symlink_to(real, target_is_directory=True)
    target = base.joinpath(*parts)

    refused(lambda: require_caddy_authority_dir(target))


@pytest.mark.parametrize("linked", ["certificates", "certificates/local"])
def test_a_leaf_directory_through_a_link_is_refused(tmp_path: Path, linked: str) -> None:
    caddy = tmp_path / "caddy-data" / "caddy"
    target = caddy / "pki" / "authorities" / "local"
    target.mkdir(parents=True)
    real = tmp_path / "elsewhere"
    real.mkdir()
    link = caddy / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real, target_is_directory=True)

    refused(lambda: leaf_dir(target))


# --- the restore output folder -------------------------------------------------------------


def test_a_new_output_folder_is_accepted(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    assert require_out_dir(tmp_path / "out", avoid=[documents]) == tmp_path / "out"
    assert require_out_dir(Path("/out"), avoid=list(PRODUCTION_DIRECTORIES)) == Path("/out")


@pytest.mark.parametrize(
    "out_dir",
    [
        "/",
        "",
        "relative/out",
        "/Users",
        "/home",
        str(HOME),
        str(HOME.parent),
        "/data/documents",
        "/data/backup-config",
        "/data/authority/x",
        "/data",
        "/backup",
        "/backup/restic",
    ],
)
def test_an_output_folder_on_a_home_or_a_data_directory_is_refused(out_dir: str) -> None:
    avoid = [*PRODUCTION_DIRECTORIES, Path("/backup")]
    refused(lambda: require_out_dir(Path(out_dir), avoid=avoid))


def test_an_output_folder_overlapping_a_configured_directory_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "backup-config"
    refused(lambda: require_out_dir(config, avoid=[config]))
    refused(lambda: require_out_dir(config / "out", avoid=[config]))
    refused(lambda: require_out_dir(tmp_path, avoid=[config]))


def test_an_output_folder_through_a_link_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    refused(lambda: require_out_dir(tmp_path / "link", avoid=[]))
    refused(lambda: require_out_dir(tmp_path / "link" / "out", avoid=[]))


# --- plain file names ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["manifest.json", "env", ".env", "a.b-c_d"])
def test_a_plain_file_name_is_accepted(name: str) -> None:
    assert require_plain_name(name) == name


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "../env", "/etc/passwd", "a\0b"])
def test_a_name_with_a_path_in_it_is_refused(name: str) -> None:
    refused(lambda: require_plain_name(name))
