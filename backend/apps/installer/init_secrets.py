"""The secrets of a compose install: made once, kept in a vault volume, placed per role.

Run as `python -m apps.installer.init_secrets` in the `init-secrets` service: root, no
network, no database, no Django settings. It makes what is missing, never overwrites a
secret that exists, and copies each role's files into that role's own volume, so the
keeper never sees the master key and the api never sees a superuser or owner password
(S 4.4). It prints counts and Dutch sentences, never a value.

Every file name is from a closed list and every write goes through a directory
descriptor without following a link; the only things it removes are files of that list
in a role volume that no longer needs them, and its own temporary files. The tests run
it only on directories under `tmp_path`.
"""

import base64
import contextlib
import os
import re
import secrets
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.backup import safepaths
from apps.backup.errors import BackupError
from apps.common import secrets as secret_files

VAULT_NAMES = (
    "postgres_password",
    "owner_password",
    "app_password",
    "backup_db_password",
    "secret_key",
    "installation_id",
    "backup_password",
    # Last: a vault without it is "not finished", so a crash between two files is repaired
    # by the next run and never mistaken for an old install.
    "master_key",
)
# Which files each role volume holds. The keeper has no master key; the api has neither
# the superuser nor the owner password; only `init` and `admin` read the vault itself.
ROLE_FILES: dict[str, tuple[str, ...]] = {
    "postgres": ("postgres_password",),
    # The api shows the recovery sheet, which carries a separate back-up password when
    # there is one; a derived one it can compute from the master key anyway.
    "api": ("secret_key", "app_password", "master_key", "backup_password"),
    "keeper": ("secret_key", "app_password", "backup_db_password", "backup_password"),
    # The scan folder seals with the master key; it never makes or opens a back-up.
    "intake": ("secret_key", "app_password", "master_key"),
    # `init` (roles, gate, emergency back-up, migrations) needs every password but never the
    # master key: no migration or back-up step decrypts a document.
    "init": (
        "secret_key",
        "postgres_password",
        "owner_password",
        "app_password",
        "backup_db_password",
        "backup_password",
    ),
}
# Filled only when mounted: a compose file from before the role (0.5.0) has no such volume.
OPTIONAL_ROLES = frozenset({"intake"})
INSTALLATION_ID_NAME = "installation-id"
FILE_MODE = 0o400
DIRECTORY_MODE = 0o700
MAX_FILE_BYTES = 4096
# What a bytes of a hex or text secret may look like (validated on import).
_HEX = re.compile(r"[0-9a-f]+")
_PASSWORD = re.compile(r"[0-9A-Za-z]{16,128}")
_TEMPORARY = re.compile(r"\.[a-z_]+\.tmp-[0-9a-f]{16}")

# deploy/.env key -> vault file, for `--import`.
IMPORT_KEYS: dict[str, str] = {
    "BRIEVIE_MASTER_KEY": "master_key",
    "SECRET_KEY": "secret_key",
    "POSTGRES_PASSWORD": "postgres_password",
    "BRIEVIE_OWNER_PASSWORD": "owner_password",
    "BRIEVIE_APP_PASSWORD": "app_password",
    "BRIEVIE_BACKUP_DB_PASSWORD": "backup_db_password",
    "BRIEVIE_INSTALLATION_ID": "installation_id",
    "BRIEVIE_BACKUP_PASSWORD": "backup_password",
}
IMPORT_REQUIRED = ("master_key",)

REASONS = {
    "data_without_keys": gettext_lazy(
        "Er zijn al gegevens, maar de sleutels ontbreken. Zet het volume 'secrets' terug, "
        "of zet de sleutels van je herstelblad terug met 'init_secrets --import'. "
        "Er is niets veranderd."
    ),
    "installation_differs": gettext_lazy(
        "Het installatienummer in de sleutelkluis hoort niet bij de gegevens van deze "
        "installatie. Er is niets veranderd."
    ),
    "vault_unsafe": gettext_lazy(
        "Een bestand in de sleutelkluis is een link, heeft de verkeerde rechten of is "
        "onleesbaar. Er is niets veranderd."
    ),
    "directory_missing": gettext_lazy(
        "Een map voor de sleutels bestaat niet of is een link. Er is niets veranderd."
    ),
    "import_unreadable": gettext_lazy(
        "Het bestand met de sleutels kan niet worden gelezen. Er is niets veranderd."
    ),
    "import_invalid": gettext_lazy(
        "Het bestand met de sleutels klopt niet: een sleutel mist of heeft een verkeerde vorm. "
        "Er is niets veranderd."
    ),
    "import_differs": gettext_lazy(
        "Een sleutel in het bestand is niet gelijk aan de sleutel in de kluis. "
        "Er is niets veranderd."
    ),
    "import_incomplete": gettext_lazy(
        "Er is al een database. Met alleen de hoofdsleutel kan dat niet: de database heeft ook "
        "zijn eigen wachtwoord nodig (POSTGRES_PASSWORD). Geef het hele bestand deploy/.env "
        "van de installatie waar deze gegevens vandaan komen. Er is niets veranderd."
    ),
    "data_unreadable": gettext_lazy(
        "De map met gegevens kan niet worden gelezen (controleer de rechten of de netwerkschijf). "
        "Zonder te weten of er gegevens zijn, maakt Brievie geen nieuwe sleutels. "
        "Er is niets veranderd."
    ),
    "backup_password_invalid": gettext_lazy(
        "Het eigen back-upwachtwoord kan niet worden gelezen. Er is niets veranderd."
    ),
}


class InitSecretsError(Exception):
    """A refusal by code; the sentence is in REASONS and holds no value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def sentence(self) -> str:
        return str(REASONS[self.code])


@dataclass(frozen=True)
class Report:
    created: tuple[str, ...] = ()
    placed: int = 0
    removed: int = 0
    imported: bool = False
    notes: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Layout:
    """The directories one run works in. In the container: /vault, /out/<role>, ..."""

    vault: Path
    out: Mapping[str, Path]
    backup_config: Path
    documents: Path
    pgdata: Path
    # (uid, gid) per "vault" and per role.
    owners: Mapping[str, tuple[int, int]]


# --- small file helpers (all relative to a directory descriptor) ---------------------


def _open_directory(path: Path) -> int:
    try:
        return safepaths.open_dir_nofollow(path)
    except (BackupError, OSError):
        raise InitSecretsError("directory_missing") from None


def _read(directory: int, name: str) -> str | None:
    try:
        raw = safepaths.read_regular_at(directory, name, limit=MAX_FILE_BYTES)
    except (BackupError, OSError):
        raise InitSecretsError("vault_unsafe") from None
    if raw is None:
        return None
    try:
        return raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise InitSecretsError("vault_unsafe") from None


def _mode_is_private(directory: int, name: str) -> bool:
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and (info.st_mode & 0o177) == 0


def _write(directory: int, name: str, text: str, owner: tuple[int, int]) -> None:
    """Replace `name` atomically: a temporary file, then a rename; never through a link."""
    temporary = f".{name}.tmp-{secrets.token_hex(8)}"
    handle = safepaths.write_new_at(directory, temporary, f"{text}\n".encode(), mode=FILE_MODE)
    try:
        os.fchown(handle, *owner)
    finally:
        os.close(handle)
    try:
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=directory)
        raise
    os.fsync(directory)


def _remove_leftovers(directory: int) -> None:
    """Remove temporary files an interrupted run left (only our own pattern, only files)."""
    for name in os.listdir(directory):
        if _TEMPORARY.fullmatch(name):
            with contextlib.suppress(OSError):
                os.unlink(name, dir_fd=directory)


def _remove_stale(directory: int, wanted: Sequence[str]) -> int:
    """Remove files of the closed list that this role no longer needs."""
    removed = 0
    for name in os.listdir(directory):
        if name in VAULT_NAMES and name not in wanted:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                os.unlink(name, dir_fd=directory)
                removed += 1
    return removed


def _secure_directory(directory: int, owner: tuple[int, int]) -> None:
    os.fchown(directory, *owner)
    os.fchmod(directory, DIRECTORY_MODE)


# --- is there data? ---------------------------------------------------------------


def _unreadable(error: OSError) -> None:
    raise InitSecretsError("data_unreadable") from None


def _has_pg_version(root: Path, depth: int = 4) -> bool:
    """A `PG_VERSION` file within `depth` levels (postgres:18 keeps it below `<major>/docker`)."""
    base = len(root.parts)
    for current, directories, files in os.walk(root, followlinks=False, onerror=_unreadable):
        if "PG_VERSION" in files:
            return True
        if len(Path(current).parts) - base >= depth:
            directories.clear()
    return False


def _has_files(root: Path) -> bool:
    return any(
        files
        for _current, _directories, files in os.walk(root, followlinks=False, onerror=_unreadable)
    )


def data_exists(layout: Layout, backup_config_id: str | None) -> bool:
    return (
        backup_config_id is not None
        or _has_pg_version(layout.pgdata)
        or _has_files(layout.documents)
    )


# --- values -----------------------------------------------------------------------


def _generate(name: str, values: Mapping[str, str], environ: Mapping[str, str]) -> str:
    if name in ("postgres_password", "owner_password", "app_password", "backup_db_password"):
        return secrets.token_hex(24)
    if name == "secret_key":
        return base64.b64encode(secrets.token_bytes(48)).decode("ascii")
    if name == "master_key":
        return secrets.token_hex(32)
    if name == "installation_id":
        return secrets.token_hex(16)
    if name == "backup_password":
        try:
            own = secret_files.read_secret("BRIEVIE_BACKUP_PASSWORD", environ)
        except secret_files.SecretFileError:
            raise InitSecretsError("backup_password_invalid") from None
        if own:
            return own
        from apps.common import crypto

        return crypto.derive_backup_password(bytes.fromhex(values["master_key"]))
    raise AssertionError(name)


def parse_import(text: str) -> dict[str, str]:
    """Vault values of a `deploy/.env`-shaped text; refuses a missing or malformed key."""
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _separator, value = line.partition("=")
        key = key.removeprefix("export ").strip().strip("\"'")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        name = IMPORT_KEYS.get(key)
        if name is not None and value:
            found[name] = value
    for name in IMPORT_REQUIRED:
        if name not in found:
            raise InitSecretsError("import_invalid")
    for name, value in found.items():
        if not _valid(name, value):
            raise InitSecretsError("import_invalid")
    return found


def _valid(name: str, value: str) -> bool:
    if name == "master_key":
        return len(value) == 64 and _HEX.fullmatch(value) is not None
    if name == "installation_id":
        return len(value) == 32 and _HEX.fullmatch(value) is not None
    if name == "secret_key":
        return 50 <= len(value) <= 200 and value.isprintable() and " " not in value
    if name == "backup_password":
        return 16 <= len(value) <= 256 and value.isprintable() and " " not in value
    return _PASSWORD.fullmatch(value) is not None


# --- the run ----------------------------------------------------------------------


def run(
    layout: Layout,
    environ: Mapping[str, str],
    *,
    import_values: Mapping[str, str] | None = None,
) -> Report:
    """Make what is missing, place what each role needs. Everything is judged first;
    a refusal writes nothing."""
    vault = _open_directory(layout.vault)
    config = _open_directory(layout.backup_config)
    out = {
        role: _open_directory(path)
        for role, path in layout.out.items()
        if role not in OPTIONAL_ROLES or os.path.lexists(path)
    }
    try:
        return _run(layout, environ, vault, config, out, import_values)
    finally:
        os.close(vault)
        os.close(config)
        for descriptor in out.values():
            os.close(descriptor)


def _run(
    layout: Layout,
    environ: Mapping[str, str],
    vault: int,
    config: int,
    out: Mapping[str, int],
    import_values: Mapping[str, str] | None,
) -> Report:
    present: dict[str, str] = {}
    for name in VAULT_NAMES:
        value = _read(vault, name)
        if value is None:
            continue
        if not value or not _mode_is_private(vault, name):
            raise InitSecretsError("vault_unsafe")
        present[name] = value
    config_id = _read_config_id(config)
    data = data_exists(layout, config_id)

    values = dict(present)
    created: list[str] = []
    if import_values is not None:
        # An existing database keeps the superuser password it was made with: minting a new
        # one here would never fit it, and the next import would then differ from the vault.
        if (
            _has_pg_version(layout.pgdata)
            and "postgres_password" not in import_values
            and "postgres_password" not in present
        ):
            raise InitSecretsError("import_incomplete")
        for name, value in import_values.items():
            if name in present and present[name] != value:
                raise InitSecretsError("import_differs")
        for name in VAULT_NAMES:
            if name in import_values and name not in present:
                values[name] = import_values[name]
                created.append(name)
    elif data:
        # Data without keys: only what can be derived or read from the data may be made
        # anew (the back-up password, and the installation id the data already has).
        derivable = {"backup_password", "installation_id" if config_id else ""}
        if any(n not in present and n not in derivable for n in VAULT_NAMES):
            raise InitSecretsError("data_without_keys")
    # The installation id of the data wins over a made one.
    if "installation_id" not in values and config_id is not None:
        values["installation_id"] = config_id
        created.append("installation_id")
    for name in sorted(VAULT_NAMES, key=lambda item: item != "master_key"):
        if name not in values:
            values[name] = _generate(name, values, environ)
            created.append(name)
    if config_id is not None and values["installation_id"] != config_id:
        raise InitSecretsError("installation_differs")

    _secure_directory(vault, layout.owners["vault"])
    for name in VAULT_NAMES:
        if name in created:
            _write(vault, name, values[name], layout.owners["vault"])
    if config_id is None:
        _write(config, INSTALLATION_ID_NAME, values["installation_id"], layout.owners["vault"])

    placed = removed = 0
    for role, descriptor in out.items():
        _secure_directory(descriptor, layout.owners[role])
        _remove_leftovers(descriptor)
        removed += _remove_stale(descriptor, ROLE_FILES[role])
        for name in ROLE_FILES[role]:
            if _read_quiet(descriptor, name) != values[name] or not _mode_is_private(
                descriptor, name
            ):
                _write(descriptor, name, values[name], layout.owners[role])
                placed += 1
    _remove_leftovers(vault)
    return Report(
        created=tuple(created),
        placed=placed,
        removed=removed,
        imported=import_values is not None,
    )


def adopt_installation_id(vault_dir: Path, backup_config: Path, owner: tuple[int, int]) -> str:
    """After a restore: the data's `installation-id` becomes this installation's (the id in
    the vault), so the next start does not find two installations in one volume. Replaces
    that one fixed file atomically; nothing else in the folder is touched."""
    vault = _open_directory(vault_dir)
    config = _open_directory(backup_config)
    try:
        value = _read(vault, "installation_id")
        if value is None or len(value) != 32 or _HEX.fullmatch(value) is None:
            raise InitSecretsError("vault_unsafe")
        _write(config, INSTALLATION_ID_NAME, value, owner)
        return value
    finally:
        os.close(vault)
        os.close(config)


def _read_config_id(config: int) -> str | None:
    value = _read(config, INSTALLATION_ID_NAME)
    if value is None:
        return None
    if len(value) != 32 or _HEX.fullmatch(value) is None:
        raise InitSecretsError("installation_differs")
    return value


def _read_quiet(directory: int, name: str) -> str | None:
    """The file's text, or None when it is absent or cannot be used (it is then replaced)."""
    try:
        return _read(directory, name)
    except InitSecretsError:
        with contextlib.suppress(OSError):
            os.unlink(name, dir_fd=directory)
        return None


# --- the command line -------------------------------------------------------------

CONTAINER_LAYOUT = Layout(
    vault=Path("/vault"),
    out={
        "postgres": Path("/out/postgres"),
        "api": Path("/out/api"),
        "keeper": Path("/out/keeper"),
        "init": Path("/out/init"),
        "intake": Path("/out/intake"),
    },
    backup_config=Path("/data/backup-config"),
    documents=Path("/data/documents"),
    pgdata=Path("/var/lib/postgresql"),
    owners={"vault": (10001, 10001), "postgres": (999, 999), "api": (10001, 10001),
            "keeper": (10001, 10001), "init": (10001, 10001), "intake": (10001, 10001)},
)  # fmt: skip


MAX_IMPORT_BYTES = 256 * 1024


def _read_import_file(path: str) -> str:
    """The text of the file to import (a deploy/.env is larger than a secret file; it is
    still bounded and never a link)."""
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMPORT_BYTES:
        raise OSError("not a usable import file")
    with open(path, "rb") as handle:
        return handle.read(MAX_IMPORT_BYTES).decode("utf-8")


def _bootstrap_translation() -> None:
    """Dutch texts without the project's settings (this runs without a database)."""
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            USE_I18N=True,
            LANGUAGE_CODE="nl",
            LOCALE_PATHS=[str(Path(__file__).resolve().parents[2] / "locale")],
            INSTALLED_APPS=[],
        )
        django.setup()


def main(argv: Sequence[str], environ: Mapping[str, str], layout: Layout = CONTAINER_LAYOUT) -> int:
    _bootstrap_translation()
    import_path = ""
    if argv and argv[0] == "--import":
        if len(argv) != 2:
            print(str(REASONS["import_invalid"]), file=sys.stderr)
            return 2
        import_path = argv[1]
    elif argv:
        print(_("Gebruik: init_secrets [--import <bestand>]"), file=sys.stderr)
        return 2
    try:
        import_values = None
        if import_path:
            try:
                import_values = parse_import(_read_import_file(import_path))
            except (OSError, UnicodeDecodeError):
                raise InitSecretsError("import_unreadable") from None
        report = run(layout, environ, import_values=import_values)
    except InitSecretsError as error:
        print(error.sentence(), file=sys.stderr)
        return 1
    print(
        _("De sleutels staan klaar: %(made)d nieuw in de kluis, %(placed)d bestanden geplaatst.")
        % {"made": len(report.created), "placed": report.placed}
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))
