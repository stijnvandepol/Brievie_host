"""Secrets from `X` or from the file named by `X_FILE` (compose installs keep secrets in files).

Pure: no Django import, no database. A value in the environment wins over a file, so an
install that sets `X` (the `./brievie` wrapper) behaves as it always did. No error text ever
holds the content of a file or a value; only the file's name and a closed code.
"""

import os
import stat
from collections.abc import Callable, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

MAX_SECRET_FILE_BYTES = 4096
STRICT_FLAG = "BRIEVIE_STRICT_SECRET_FILES"
CODES = ("unreadable", "link", "too_large", "not_text", "too_open")

Reader = Callable[[str, bool], bytes]


class SecretFileError(Exception):
    """A secret file that may not be used. `code` is one of `CODES`; the text has no content."""

    def __init__(self, code: str, name: str) -> None:
        super().__init__(f"secret file for {name}: {code}")
        self.code = code
        self.name = name


class SecretMissingError(Exception):
    """A required secret is set neither as `X` nor as `X_FILE`."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not set (neither {name} nor {name}_FILE)")
        self.name = name


def read_file(path: str, strict: bool) -> bytes:
    """Reads a small regular file that is not a link; `strict` also refuses group/other access."""
    try:
        info = os.lstat(path)
    except OSError as error:
        raise SecretFileError("unreadable", path) from error
    if stat.S_ISLNK(info.st_mode):
        raise SecretFileError("link", path)
    if not stat.S_ISREG(info.st_mode):
        raise SecretFileError("unreadable", path)
    if info.st_size > MAX_SECRET_FILE_BYTES:
        raise SecretFileError("too_large", path)
    if strict and info.st_mode & 0o077:
        raise SecretFileError("too_open", path)
    try:
        with open(path, "rb") as handle:
            return handle.read(MAX_SECRET_FILE_BYTES + 1)
    except OSError as error:
        raise SecretFileError("unreadable", path) from error


def read_secret(
    name: str,
    environ: Mapping[str, str],
    *,
    default: str = "",
    reader: Reader = read_file,
) -> str:
    """The value of `name`, else the first line of the file `name_FILE` names, else `default`."""
    value = environ.get(name, "")
    if value:
        return value
    path = environ.get(f"{name}_FILE", "")
    if not path:
        return default
    strict = environ.get(STRICT_FLAG, "").lower() == "true"
    raw = reader(path, strict)
    if len(raw) > MAX_SECRET_FILE_BYTES:
        raise SecretFileError("too_large", path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SecretFileError("not_text", path) from error
    lines = text.splitlines()
    return lines[0].strip() if lines else ""


def require_secret(name: str, environ: Mapping[str, str], *, reader: Reader = read_file) -> str:
    """Like `read_secret`, but a missing or empty secret is an error."""
    value = read_secret(name, environ, reader=reader)
    if not value:
        raise SecretMissingError(name)
    return value


def with_password(url: str, password: str) -> str:
    """The URL with `password` set, quoted for a URL; a URL that has a password stays as it is."""
    if not url or not password:
        return url
    parts = urlsplit(url)
    if parts.password is not None:
        return url
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    user = quote(parts.username or "", safe="")
    netloc = f"{user}:{quote(password, safe='')}@{host}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def database_url(name: str, environ: Mapping[str, str], *, reader: Reader = read_file) -> str:
    """`DATABASE_*_URL` with its password from `DATABASE_*_PASSWORD` or `_FILE`.

    `DATABASE_URL` pairs with `DATABASE_PASSWORD`, `DATABASE_OWNER_URL` with
    `DATABASE_OWNER_PASSWORD`, and so on. Empty when the URL itself is not set.
    """
    url = environ.get(name, "")
    if not url:
        return ""
    password = read_secret(name.removesuffix("_URL") + "_PASSWORD", environ, reader=reader)
    return with_password(url, password)


def check_secret_key(value: str, *, debug: bool) -> str | None:
    """None when `value` may be the Django secret key; else a sentence without the value."""
    if debug:
        return None
    if "change-me" in value or len(value) < 50:
        return (
            "SECRET_KEY is still the placeholder or shorter than 50 characters. "
            "Generate one: openssl rand -base64 48 (see 'Run the whole stack' in the README)."
        )
    return None
