"""The path guards of the back-up app, called as pure functions.

Nothing here deletes, moves or overwrites anything: the guards only judge a path.
Every accepting case lives under `tmp_path`; the system paths are only judged.
"""

from pathlib import Path

import pytest

from apps.backup import safepaths
from apps.backup.errors import BackupError
from apps.backup.safepaths import (
    leaf_dir,
    require_caddy_authority_dir,
    require_emptiable,
    require_inside,
    require_plain_name,
)

HOME = Path.home()


def refused(call: object) -> None:
    assert callable(call)
    with pytest.raises(BackupError) as caught:
        call()
    assert caught.value.code == "unsafe_path"
    assert caught.value.message == (
        "Brievie weigert deze map te wissen of te overschrijven, omdat dat niet veilig is."
    )


# --- require_emptiable -----------------------------------------------------------------


@pytest.mark.parametrize(
    "directory",
    [
        "/",
        "/Users",
        "/data",
        "/home",
        "/root",
        str(HOME),
        str(HOME.parent),
        str(HOME / "Documents" / "brievie-documents"),
        "relative/documents",
        "documents",
        "/data/documents/..",
    ],
)
def test_a_system_home_or_relative_path_is_never_emptiable(directory: str) -> None:
    # Even when the caller lists it as allowed: the guard does not trust its caller.
    refused(lambda: require_emptiable(Path(directory), allowed=[Path(directory)]))


def test_a_link_is_never_emptiable(tmp_path: Path) -> None:
    real = tmp_path / "real-documents"
    real.mkdir()
    link = tmp_path / "documents"
    link.symlink_to(real, target_is_directory=True)

    refused(lambda: require_emptiable(link, allowed=[link]))
    refused(lambda: require_emptiable(link, allowed=[real]))


def test_a_path_through_a_link_is_never_emptiable(tmp_path: Path) -> None:
    holder = tmp_path / "holder"
    holder.mkdir()
    (holder / "up").symlink_to("/", target_is_directory=True)
    sneaky = holder / "up" / "tmp"  # not itself a link: a link above leads to the root
    real = tmp_path / "real"
    (real / "documents").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    refused(lambda: require_emptiable(sneaky, allowed=[sneaky]))
    refused(lambda: require_emptiable(alias / "documents", allowed=[real / "documents"]))
    refused(lambda: require_emptiable(alias / "documents", allowed=[alias / "documents"]))


def test_a_directory_not_in_the_allowed_list_is_refused(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()

    refused(lambda: require_emptiable(documents, allowed=[tmp_path / "authority"]))
    refused(lambda: require_emptiable(documents, allowed=[]))
    refused(lambda: require_emptiable(tmp_path, allowed=[documents]))
    refused(lambda: require_emptiable(documents / "inner", allowed=[documents]))


def test_a_configured_directory_under_the_temporary_folder_is_emptiable(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    authority = tmp_path / "authority"  # need not exist yet

    assert require_emptiable(documents, allowed=[documents, authority]) == documents
    assert require_emptiable(authority, allowed=[str(documents), str(authority)]) == authority


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


def test_the_home_directory_and_its_ancestors_are_refused_wherever_home_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "someone"
    home.mkdir(parents=True)
    monkeypatch.setattr(safepaths, "_home", lambda: home.resolve())

    refused(lambda: require_emptiable(home, allowed=[home]))
    refused(lambda: require_emptiable(home.parent, allowed=[home.parent]))
    # Inside the home directory, only the temporary folder is acceptable (this one is).
    inside = home / "documents"
    assert require_emptiable(inside, allowed=[inside]) == inside


def test_inside_home_only_the_temporary_folder_is_emptiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(safepaths, "_home", lambda: home.resolve())
    monkeypatch.setattr(safepaths, "_temp_root", lambda: (tmp_path / "temp").resolve())
    folder = home / "brievie" / "documents"

    refused(lambda: require_emptiable(folder, allowed=[folder]))


def test_a_home_at_the_root_does_not_make_everything_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A service user without a home has HOME=/: the root rule already covers "/".
    monkeypatch.setattr(safepaths, "_home", lambda: Path("/"))
    documents = tmp_path / "documents"

    assert require_emptiable(documents, allowed=[documents]) == documents
    refused(lambda: require_emptiable(Path("/"), allowed=[Path("/")]))


# --- require_inside --------------------------------------------------------------------


def test_a_path_strictly_inside_the_root_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)

    assert require_inside(root / "a", root) == root / "a"
    assert require_inside(root / "a" / "b.txt", root) == root / "a" / "b.txt"
    assert require_inside(root / "not-yet", root) == root / "not-yet"


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


def test_nothing_is_inside_the_file_system_root() -> None:
    refused(lambda: require_inside(Path("/etc/passwd"), Path("/")))


# --- the Caddy authority ---------------------------------------------------------------


def test_a_caddy_authority_directory_is_accepted(tmp_path: Path) -> None:
    target = tmp_path / "caddy-data" / "caddy" / "pki" / "authorities" / "local"

    assert require_caddy_authority_dir(target) == target
    assert require_caddy_authority_dir(Path("/caddy-data/caddy/pki/authorities/local")) == (
        Path("/caddy-data/caddy/pki/authorities/local")
    )
    assert leaf_dir(target) == tmp_path / "caddy-data" / "caddy" / "certificates" / "local"


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
        str(HOME),
    ],
)
def test_anything_else_is_not_a_caddy_authority_directory(target: str) -> None:
    refused(lambda: require_caddy_authority_dir(Path(target)))
    refused(lambda: leaf_dir(Path(target)))


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


# --- plain file names ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["manifest.json", "env", ".env", "a.b-c_d"])
def test_a_plain_file_name_is_accepted(name: str) -> None:
    assert require_plain_name(name) == name


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "../env", "/etc/passwd", "a\0b"])
def test_a_name_with_a_path_in_it_is_refused(name: str) -> None:
    refused(lambda: require_plain_name(name))
