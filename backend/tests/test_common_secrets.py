"""X versus X_FILE (pure; files only under tmp_path)."""

import os
from pathlib import Path

import pytest

from apps.common.secrets import (
    SecretFileError,
    SecretMissingError,
    check_secret_key,
    database_url,
    read_secret,
    require_secret,
    with_password,
)


def write(path: Path, content: bytes, mode: int = 0o400) -> str:
    path.write_bytes(content)
    path.chmod(mode)
    return str(path)


def test_value_wins_over_file(tmp_path: Path) -> None:
    file = write(tmp_path / "s", b"from-file\n")
    assert read_secret("X", {"X": "from-env", "X_FILE": file}) == "from-env"


def test_file_used_when_value_unset_or_empty(tmp_path: Path) -> None:
    file = write(tmp_path / "s", b"from-file\n")
    assert read_secret("X", {"X_FILE": file}) == "from-file"
    assert read_secret("X", {"X": "", "X_FILE": file}) == "from-file"


def test_only_the_first_line_stripped(tmp_path: Path) -> None:
    file = write(tmp_path / "s", b"  abc  \nsecond\n")
    assert read_secret("X", {"X_FILE": file}) == "abc"


def test_default_when_nothing_is_set() -> None:
    assert read_secret("X", {}) == ""
    assert read_secret("X", {}, default="d") == "d"


def test_missing_file_is_unreadable_and_names_no_content(tmp_path: Path) -> None:
    with pytest.raises(SecretFileError) as caught:
        read_secret("X", {"X_FILE": str(tmp_path / "nope")})
    assert caught.value.code == "unreadable"


def test_link_is_refused(tmp_path: Path) -> None:
    target = write(tmp_path / "real", b"topsecret-value")
    link = tmp_path / "link"
    os.symlink(target, link)
    with pytest.raises(SecretFileError) as caught:
        read_secret("X", {"X_FILE": str(link)})
    assert caught.value.code == "link"
    assert "topsecret-value" not in str(caught.value)


def test_large_file_is_refused(tmp_path: Path) -> None:
    file = write(tmp_path / "big", b"a" * 5 * 1024)
    with pytest.raises(SecretFileError) as caught:
        read_secret("X", {"X_FILE": file})
    assert caught.value.code == "too_large"


def test_binary_file_is_refused(tmp_path: Path) -> None:
    file = write(tmp_path / "bin", b"\xff\xfe\x00\x01")
    with pytest.raises(SecretFileError) as caught:
        read_secret("X", {"X_FILE": file})
    assert caught.value.code == "not_text"


def test_open_file_refused_only_when_strict(tmp_path: Path) -> None:
    file = write(tmp_path / "open", b"v\n", mode=0o644)
    assert read_secret("X", {"X_FILE": file}) == "v"
    with pytest.raises(SecretFileError) as caught:
        read_secret("X", {"X_FILE": file, "BRIEVIE_STRICT_SECRET_FILES": "true"})
    assert caught.value.code == "too_open"


def test_require_secret_refuses_missing() -> None:
    with pytest.raises(SecretMissingError):
        require_secret("X", {})


def test_with_password_quotes_and_keeps_existing() -> None:
    assert with_password("postgres://u@h:5432/d", "p@ss/word") == (
        "postgres://u:p%40ss%2Fword@h:5432/d"
    )
    assert with_password("postgres://u:old@h/d", "new") == "postgres://u:old@h/d"
    assert with_password("postgres://u@h/d", "") == "postgres://u@h/d"


def test_database_url_pairs_password_names(tmp_path: Path) -> None:
    file = write(tmp_path / "pw", b"abc123\n")
    env = {
        "DATABASE_URL": "postgres://brievie_app@postgres:5432/brievie",
        "DATABASE_PASSWORD_FILE": file,
        "DATABASE_OWNER_URL": "postgres://o:fixed@postgres:5432/brievie",
    }
    assert (
        database_url("DATABASE_URL", env) == "postgres://brievie_app:abc123@postgres:5432/brievie"
    )
    assert database_url("DATABASE_OWNER_URL", env) == env["DATABASE_OWNER_URL"]
    assert database_url("DATABASE_BACKUP_URL", env) == ""


def test_secret_key_from_file_keeps_the_placeholder_refusal(tmp_path: Path) -> None:
    placeholder = write(tmp_path / "k1", b"change-me-" + b"x" * 60)
    good = write(tmp_path / "k2", b"k" * 64)
    assert check_secret_key(
        read_secret("SECRET_KEY", {"SECRET_KEY_FILE": placeholder}), debug=False
    )
    assert (
        check_secret_key(read_secret("SECRET_KEY", {"SECRET_KEY_FILE": good}), debug=False) is None
    )
    assert check_secret_key("short", debug=False)
    assert check_secret_key("short", debug=True) is None
