"""Restore onto an empty machine (D 4.6), in one-off containers.

Part 1, `fetch`: open the back-up with the master key (typed from the recovery sheet)
and, when the back-ups have their own password, that password, and write the restored
.env. Every refusal comes before anything is written: a wrong password, a wrong master
key, a back-up of a newer Brievie, or a run that does not exist leave the output
folder as it was.

Part 2, once the restored .env is in place: `restore_database` loads the dump as the
superuser and closes the runs it brought back as open, `restore_files` puts the
documents and Caddy's authority back. Both hold the back-up lock of the database they
restore (or restore into), so a keeper that still runs cannot start a back-up or a
sweep meanwhile.
"""

import contextlib
import logging
import os
import re
import subprocess
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from uuid import UUID

import psycopg
from django.conf import settings
from django.utils import timezone

from apps.backup import destination, restic, safepaths
from apps.backup.database import libpq_env, superuser_conninfo
from apps.backup.envcopy import ENV_COPY_NAME, restored_env
from apps.backup.errors import REASONS, BackupError
from apps.backup.locks import backup_lock, database_lock
from apps.backup.manifest import (
    AUTHORITY_FILE,
    MANIFEST_NAME,
    manifest_from_json,
    unknown_migrations,
)
from apps.backup.services import location_secrets
from apps.common import crypto

logger = logging.getLogger(__name__)

KEY_TABLE = "workspaces_workspacekey"
# Reads the custom-format archive from stdin (no file name given) and prints the
# key table's COPY block on stdout. Connects to no database, so it needs no secret.
PG_RESTORE_KEYS_COMMAND = ("pg_restore", "--data-only", f"--table={KEY_TABLE}", "--file=-")
# Loads the archive from stdin as the superuser: only a superuser loads rows into tables
# with forced row-level security. Owners and grants are kept (the roles exist from
# 01-roles.sh); one transaction, so a failure leaves the database as it was. Host, user
# and password come from the environment (libpq_env), never from argv.
PG_RESTORE_COMMAND = ("pg_restore", "--exit-on-error", "--single-transaction", "--no-password")

_COPY_HEADER = re.compile(
    r'^COPY (?:"?public"?\.)?"?' + KEY_TABLE + r'"? \((?P<columns>[^)]*)\) FROM stdin;$'
)
_COPY_END = "\\."
_COPY_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
_COPY_ESCAPE = re.compile(r"\\(.)")
# restic failures a restore reports as such; anything else is "the restore failed".
# Never `files_incomplete` (exit 3): its sentence says a back-up was made.
_RESTORE_CODES = frozenset({"wrong_password", "repository_missing", "destination_unreachable"})
_KEY_NOISE = re.compile(r"[\s-]+")
_KEY_HEX = re.compile(rf"[0-9a-f]{{{2 * crypto.KEY_BYTES}}}")
_CHUNK = 1 << 16
_STDERR_TAIL = 500
_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"
# What caddy-publish-root.sh copies of Caddy's authority (never its temporary files).
_AUTHORITY_FILES = (AUTHORITY_FILE, "root.key", "intermediate.crt", "intermediate.key")
# A certificate or key of Caddy's authority is a few KiB; anything larger is not one.
_AUTHORITY_LIMIT = 64 * 1024
# The uid and gid of the keeper: the restored files are its.
SERVICE_UID = 10001


@dataclass(frozen=True)
class RunSnapshots:
    run_id: UUID
    db: restic.Snapshot
    files: restic.Snapshot


def _secrets(repository: restic.Repository, password: str, extra: Sequence[str] = ()) -> list[str]:
    return [password, *extra, *destination.secrets(), *location_secrets(repository)]


def _failure(returncode: int, stderr: str) -> BackupError:
    """The error of a failed restic call on the restore side; its stderr goes to the log."""
    logger.warning("Restore: restic exited with %s: %s", returncode, stderr[-_STDERR_TAIL:])
    code = restic.classify(restic.ResticResult(returncode=returncode, stdout=b"", stderr=stderr))
    return BackupError(code if code in _RESTORE_CODES else "restore_failed")


def _restic_output(
    repository: restic.Repository,
    password: str,
    argv: Sequence[str],
    extra_secrets: Sequence[str] = (),
) -> bytes:
    env = restic.restic_env(repository, password)
    try:
        result = restic.run(argv, env, secrets=_secrets(repository, password, extra_secrets))
    except BackupError as error:
        raise BackupError("restore_failed") from error
    if result.returncode != 0:
        raise _failure(result.returncode, result.stderr)
    return result.stdout


def _newest_by_run(snapshots: list[restic.Snapshot]) -> dict[UUID, restic.Snapshot]:
    """The newest snapshot of each run; a resumed run can have more than one."""
    newest: dict[UUID, restic.Snapshot] = {}
    for snapshot in snapshots:
        for tag in snapshot.tags:
            if not tag.startswith("run:"):
                continue
            try:
                run_id = UUID(tag.removeprefix("run:"))
            except ValueError:
                continue
            current = newest.get(run_id)
            if current is None or snapshot.time > current.time:
                newest[run_id] = snapshot
    return newest


def _snapshots_of_kind(
    repository: restic.Repository, password: str, kind: str, extra_secrets: Sequence[str]
) -> dict[UUID, restic.Snapshot]:
    argv = restic.snapshots_argv(f"kind:{kind}", no_lock=True)
    stdout = _restic_output(repository, password, argv, extra_secrets)
    try:
        snapshots = restic.parse_snapshots(stdout)
    except BackupError as error:
        raise BackupError("restore_failed") from error
    return _newest_by_run(snapshots)


def find_run(
    repository: restic.Repository,
    password: str,
    run: str | None,
    *,
    extra_secrets: Sequence[str] = (),
) -> RunSnapshots:
    """The snapshots of run `run` (a run id), or of the newest run that has both kinds.

    A run whose files step ended incomplete (restic exit 3) has both and is restorable.
    `extra_secrets` are hidden from the log too (the typed master key).
    """
    wanted: UUID | None = None
    if run is not None:
        try:
            wanted = UUID(run)
        except ValueError:
            raise BackupError("run_not_found") from None
    db = _snapshots_of_kind(repository, password, "db", extra_secrets)
    files = _snapshots_of_kind(repository, password, "files", extra_secrets)
    complete = [run_id for run_id in db if run_id in files and wanted in (None, run_id)]
    if not complete:
        raise BackupError("run_not_found")
    chosen = max(complete, key=lambda run_id: db[run_id].time)
    return RunSnapshots(run_id=chosen, db=db[chosen], files=files[chosen])


def _pump(source: IO[bytes], sink: IO[bytes]) -> None:
    """Copy `source` into `sink`, then read `source` to its end.

    A consumer may stop reading once it has what it wants; restic must still be able
    to write the whole dump, so its exit code says whether the dump was readable.
    """
    sink_open = True
    while chunk := source.read(_CHUNK):
        if sink_open:
            try:
                sink.write(chunk)
            except OSError:  # BrokenPipeError: the consumer has stopped reading
                sink_open = False
    with contextlib.suppress(OSError):
        sink.close()


def _collect(stream: IO[bytes], into: list[bytes]) -> None:
    into.append(stream.read())


def _tool_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", _DEFAULT_PATH), **(extra or {})}


def _tool_messages(stderr: str, tool: str) -> str:
    """Only the tool's own message lines: DETAIL, CONTEXT and the failing command can
    quote rows of the dump (names, document text), and never reach the log."""
    prefix = f"{os.path.basename(tool)}:"
    return "\n".join(line for line in stderr.splitlines() if line.startswith(prefix))


def _dump_through(
    repository: restic.Repository,
    password: str,
    snapshot: restic.Snapshot,
    consumer: Sequence[str],
    extra_secrets: Sequence[str] = (),
    consumer_env: Mapping[str, str] | None = None,
) -> bytes:
    """`restic dump <snapshot> brievie.pgdump | <consumer>`; the consumer's stdout.

    Both run as child processes joined by a pipe through this process: the dump is
    never written to disk. Both exit codes count, so a dump that broke off is not
    passed off by a consumer that read a truncated archive without complaint. On any
    error neither process is left running. `consumer_env` is added to the consumer's
    environment only (libpq settings); its passwords are hidden from the log as well.
    """
    passwords = [value for name, value in (consumer_env or {}).items() if "PASSWORD" in name]
    secrets = _secrets(repository, password, [*extra_secrets, *passwords])
    processes: list[subprocess.Popen[bytes]] = []
    threads: list[threading.Thread] = []
    dump_err: list[bytes] = []
    consumer_err: list[bytes] = []
    try:
        try:
            dump_argv = restic.dump_argv(snapshot.id, restic.DB_FILENAME, no_lock=True)
            dump = subprocess.Popen(  # noqa: S603 - argv without secrets
                [settings.BRIEVIE_RESTIC_BINARY, *dump_argv],
                env=restic.restic_env(repository, password),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processes.append(dump)
            reader = subprocess.Popen(  # noqa: S603 - fixed argv
                list(consumer),
                env=_tool_env(consumer_env),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            processes.append(reader)
        except OSError as error:
            raise BackupError("restore_failed") from error
        dump_out, dump_stderr = dump.stdout, dump.stderr
        reader_in, reader_out, reader_stderr = reader.stdin, reader.stdout, reader.stderr
        if not (dump_out and dump_stderr and reader_in and reader_out and reader_stderr):
            raise BackupError("restore_failed")  # unreachable: every stream is a pipe
        threads = [
            threading.Thread(target=_pump, args=(dump_out, reader_in), daemon=True),
            threading.Thread(target=_collect, args=(dump_stderr, dump_err), daemon=True),
            threading.Thread(target=_collect, args=(reader_stderr, consumer_err), daemon=True),
        ]
        for thread in threads:
            thread.start()
        output = reader_out.read()
        reader_code = reader.wait()
        if reader_code != 0:
            # Nothing more is wanted from the dump; never wait for all of it.
            dump.kill()
        dump_code = dump.wait()
        for thread in threads:
            thread.join()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
        for thread in threads:
            thread.join(timeout=5)
        for process in processes:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()
    if dump_code > 0:
        dump_stderr_text = b"".join(dump_err).decode(errors="replace")
        raise _failure(dump_code, restic.redact(dump_stderr_text, secrets))
    if dump_code != 0 or reader_code != 0:
        stderr = _tool_messages(b"".join(consumer_err).decode(errors="replace"), consumer[0])
        stderr = restic.redact(stderr, secrets)
        logger.warning("Restore: %s exited with %s: %s", consumer[0], reader_code, stderr)
        raise BackupError("restore_failed")
    return output


def _copy_text(field: str) -> str:
    """One field of a COPY text row, unescaped (a NULL is refused)."""
    if field == "\\N":
        raise BackupError("restore_failed")
    return _COPY_ESCAPE.sub(lambda m: _COPY_ESCAPES.get(m[1], m[1]), field)


def _first_key_row(text: str) -> tuple[UUID, bytes] | None:
    """Workspace id and wrapped key of the first row of the key table's COPY block.

    None when the table has no rows. Columns are found by name in the block's header.
    """
    lines = text.splitlines()
    index, header = next(
        (
            (number, match)
            for number, line in enumerate(lines)
            if (match := _COPY_HEADER.match(line)) is not None
        ),
        (0, None),
    )
    if header is None:
        raise BackupError("restore_failed")
    columns = [column.strip().strip('"') for column in header["columns"].split(",")]
    if index + 1 >= len(lines) or "workspace_id" not in columns or "wrapped_key" not in columns:
        raise BackupError("restore_failed")
    row = lines[index + 1]
    if row == _COPY_END:
        return None
    fields = row.split("\t")
    if len(fields) != len(columns):
        raise BackupError("restore_failed")
    workspace = _copy_text(fields[columns.index("workspace_id")])
    wrapped = _copy_text(fields[columns.index("wrapped_key")])
    if not wrapped.startswith("\\x"):
        raise BackupError("restore_failed")
    try:
        return UUID(workspace), bytes.fromhex(wrapped[2:])
    except ValueError:
        raise BackupError("restore_failed") from None


def check_master_key_against_dump(
    *,
    repository: restic.Repository,
    password: str,
    db_snapshot: restic.Snapshot,
    master_key: bytes,
) -> None:
    """Refuse a master key that cannot unwrap the dump's first workspace key.

    The password alone does not prove the key: with a separate back-up password the
    repository opens with any master key. A back-up without workspace keys passes.
    """
    output = _dump_through(
        repository, password, db_snapshot, PG_RESTORE_KEYS_COMMAND, _key_spellings(master_key)
    )
    row = _first_key_row(output.decode("utf-8", errors="replace"))
    if row is None:
        return
    workspace_id, wrapped = row
    try:
        crypto.open_sealed(master_key, wrapped, aad=workspace_id.bytes)
    except crypto.DecryptionError:
        raise BackupError("wrong_master_key") from None


def parse_master_key(master_key_hex: str) -> bytes:
    """The key as typed from the recovery sheet: spaces, dashes and capitals allowed."""
    cleaned = _KEY_NOISE.sub("", master_key_hex).lower()
    if _KEY_HEX.fullmatch(cleaned) is None:
        raise BackupError("master_key_malformed")
    return bytes.fromhex(cleaned)


def _key_spellings(master_key: bytes, typed: str = "") -> list[str]:
    """The master key as it could appear in a message: hidden from every log line."""
    text = master_key.hex()
    return [value for value in (text, text.upper(), typed.strip()) if value]


def _text(data: bytes, code: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise BackupError(code) from None


def _configured_directories() -> list[Path]:
    """The documents, the authority copy and the config folder: absolute or refused."""
    return [
        safepaths.configured_path(settings.BRIEVIE_STORAGE_ROOT),
        safepaths.configured_path(settings.BRIEVIE_AUTHORITY_COPY_DIR),
        safepaths.configured_path(settings.BRIEVIE_BACKUP_CONFIG_DIR),
    ]


def _backup_mount() -> Path:
    return safepaths.configured_path(settings.BRIEVIE_BACKUP_MOUNT)


def _open_out_dir(out_dir: Path) -> tuple[int, int | None]:
    """A descriptor of `out_dir`, made (0700) when it does not exist, and of its parent
    when this call made it (for the roll-back); refused unless new or empty."""
    expected = safepaths.identity(out_dir)
    if expected is not None:
        descriptor = safepaths.open_dir_nofollow(out_dir, expect=expected)
        with os.scandir(descriptor) as entries:
            empty = next(iter(entries), None) is None
        if not empty:
            os.close(descriptor)
            raise BackupError("out_dir_not_empty")
        return descriptor, None
    parent = safepaths.open_dir_nofollow(out_dir.parent)
    try:
        os.mkdir(out_dir.name, 0o700, dir_fd=parent)
        descriptor = os.open(
            out_dir.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
    except BaseException:
        os.close(parent)
        raise
    return descriptor, parent


def _write_out(out_dir: Path, files: dict[str, str], owner: tuple[int, int] | None) -> None:
    """Write every file (0600) or none into a new or empty folder.

    Refuses (`unsafe_path`) a folder that is `/`, a home directory or above one, is
    reached through a link, or is, contains or lies inside a configured directory, a
    production directory or the back-up mount; and (`out_dir_not_empty`) a folder
    that holds anything. Files are created relative to the folder's descriptor and
    never replace anything, so the roll-back of a failure removes only files this call
    created (and the folder, when it made it). A folder this call creates (0700) is
    handed to `owner` as well, so the host user can open it.
    """
    avoid = [*_configured_directories(), *safepaths.PRODUCTION_VOLUMES, _backup_mount()]
    safepaths.require_out_dir(out_dir, avoid=avoid)
    names = [safepaths.require_plain_name(name) for name in files]
    try:
        descriptor, parent = _open_out_dir(out_dir)
    except OSError as error:
        raise BackupError("restore_failed") from error
    created: list[str] = []
    try:
        for name in names:
            handle = safepaths.write_new_at(descriptor, name, files[name].encode("utf-8"))
            created.append(name)
            try:
                if owner is not None:
                    os.fchown(handle, *owner)
            finally:
                os.close(handle)
        if parent is not None and owner is not None:
            os.fchown(descriptor, *owner)
    except BaseException as error:
        for name in created:
            with contextlib.suppress(OSError):
                os.unlink(name, dir_fd=descriptor)
        if parent is not None:
            with contextlib.suppress(OSError):
                os.rmdir(out_dir.name, dir_fd=parent)
        if isinstance(error, OSError):
            raise BackupError("restore_failed") from error
        raise
    finally:
        os.close(descriptor)
        if parent is not None:
            os.close(parent)


@dataclass(frozen=True)
class Fetched:
    snapshots: RunSnapshots
    dropped_keys: tuple[str, ...]  # host keys of the back-up's .env that were left out

    @property
    def run_id(self) -> UUID:
        return self.snapshots.run_id


def fetch(
    *,
    run: str | None,
    master_key_hex: str,
    separate_password: str | None,
    backup_dir_host: str | None,
    out_dir: Path,
    out_owner: tuple[int, int] | None,
) -> Fetched:
    """Open the back-up, find the run, refuse what cannot be restored, then write.

    Writes `out_dir/manifest.json` and `out_dir/env` (the restored deploy/.env), and
    nothing at all when any check fails. The password is the separate one when given,
    else the one derived from the master key. The output folder is judged first.
    """
    avoid = [*_configured_directories(), *safepaths.PRODUCTION_VOLUMES, _backup_mount()]
    safepaths.require_out_dir(out_dir, avoid=avoid)
    master_key = parse_master_key(master_key_hex)
    hidden = _key_spellings(master_key, master_key_hex)
    password = separate_password or crypto.derive_backup_password(master_key)
    repository = destination.configured_repository()
    found = find_run(repository, password, run, extra_secrets=hidden)

    config_dir = safepaths.configured_path(settings.BRIEVIE_BACKUP_CONFIG_DIR)
    manifest_argv = restic.dump_argv(found.files.id, str(config_dir / MANIFEST_NAME), no_lock=True)
    manifest_out = _restic_output(repository, password, manifest_argv, hidden)
    manifest_text = _text(manifest_out, "manifest_unreadable")
    manifest = manifest_from_json(manifest_text)
    if manifest.run_id != found.run_id:
        raise BackupError("manifest_unreadable")
    if unknown_migrations(manifest):
        raise BackupError("newer_backup")
    env_argv = restic.dump_argv(found.files.id, str(config_dir / ENV_COPY_NAME), no_lock=True)
    env_copy = _text(_restic_output(repository, password, env_argv, hidden), "restore_failed")
    check_master_key_against_dump(
        repository=repository, password=password, db_snapshot=found.db, master_key=master_key
    )
    env = restored_env(
        env_copy,
        master_key_hex=master_key.hex(),
        backup_password=password,
        backup_dir_host=backup_dir_host,
    )
    _write_out(out_dir, {MANIFEST_NAME: manifest_text, ENV_COPY_NAME: env.text}, out_owner)
    return Fetched(snapshots=found, dropped_keys=env.dropped)


# --- part 2: the database and the files ------------------------------------------------


def close_interrupted_runs(conninfo: str) -> int:
    """Close the runs the dump brought back as open; returns how many.

    Done straight after pg_restore, before any service starts: the keeper would
    otherwise resume a restored `running` run. Their jobs are left alone:
    `perform_run` does nothing for a closed run.
    """
    try:
        with psycopg.connect(conninfo) as conn:
            cursor = conn.execute(
                "UPDATE backup_backuprun"
                " SET status = 'interrupted', reason_code = 'interrupted_by_restore',"
                " reason = %s, finished_at = %s, updated_at = %s"
                " WHERE status IN ('queued', 'running')",
                [str(REASONS["interrupted_by_restore"]), timezone.now(), timezone.now()],
            )
            return cursor.rowcount
    except psycopg.Error:
        raise BackupError("restore_failed") from None


def restore_database(*, run: str | None, dbname: str = "brievie") -> int:
    """Load the run's dump into the empty database `dbname`; returns the runs closed.

    `restic dump | pg_restore` as the superuser, under the back-up lock of `dbname`
    (held on a connection of its own: the default connection's database is the one
    being replaced). The dump is never on disk.
    """
    repository = destination.configured_repository()
    password = destination.backup_password()
    found = find_run(repository, password, run)
    conninfo = superuser_conninfo(dbname=dbname)
    with database_lock(conninfo):
        _dump_through(
            repository,
            password,
            found.db,
            [*PG_RESTORE_COMMAND, f"--dbname={dbname}"],
            consumer_env=libpq_env(conninfo),
        )
        return close_interrupted_runs(conninfo)


def _judged_directories() -> list[Path]:
    """The three restored directories, each judged against the others and the mount:
    on the fixed list (or in a safe temporary folder), no glob, no overlap."""
    directories = _configured_directories()
    mount = _backup_mount()
    for directory in directories:
        others = [other for other in directories if other is not directory]
        safepaths.require_emptiable(directory, allowed=directories, avoid=[*others, mount])
    return directories


def _empty(directory: Path) -> None:
    """Remove what is in `directory` (a volume's mount point stays); links are not followed.

    Refuses anything but one of the three configured directories on the fixed list,
    whoever calls it. The directory is opened component by component without
    following a link, checked to be the one judged, and emptied through descriptors.
    """
    directories = _configured_directories()
    others = [other for other in directories if other != directory]
    safepaths.require_emptiable(directory, allowed=directories, avoid=[*others, _backup_mount()])
    expected = safepaths.identity(directory)
    if expected is None:
        return
    descriptor = safepaths.open_dir_nofollow(directory, expect=expected)
    try:
        safepaths.empty_dir(descriptor)
    finally:
        os.close(descriptor)


def _check_target(directory: Path) -> None:
    """Refuse (`restore_target_unsafe`) a target that holds anything but directories and
    regular files with one name; a missing target is fine (restic makes it)."""
    expected = safepaths.identity(directory)
    if expected is None:
        return
    descriptor = safepaths.open_dir_nofollow(directory, expect=expected)
    try:
        safepaths.check_tree(descriptor)
    finally:
        os.close(descriptor)


def _clean_restored(directory: Path, owner: int | None) -> int:
    """After restic: remove what is no regular file or directory, clear setuid bits and
    hand the tree to `owner`; returns how many entries were removed."""
    expected = safepaths.identity(directory)
    if expected is None:
        return 0
    descriptor = safepaths.open_dir_nofollow(directory, expect=expected)
    try:
        cleaned = safepaths.clean_tree(descriptor, owner=owner)
    finally:
        os.close(descriptor)
    return cleaned.removed


def _place_authority(source: Path, target: Path) -> bool:
    """Make `target` an exact copy of the restored authority, root-owned, 0700/0600.

    Returns False, and leaves Caddy's own pki alone, when the back-up has no authority
    (a public certificate). Caddy's leaf certificates are removed, so it issues new ones
    from the restored authority. Every refusal comes before anything is changed. The
    source files are read without following a link, must be regular and small; the
    target and the leaf directory are worked on through descriptors only, and nothing
    above `…/caddy/pki` is made (`caddy_not_ready` when Caddy's folder is missing).
    """
    target = safepaths.require_caddy_authority_dir(target)
    leaf = safepaths.leaf_dir(target)
    try:
        source_fd = safepaths.open_dir_nofollow(source)
    except FileNotFoundError:
        return False  # nothing restored there: the back-up has no authority
    try:
        contents = {
            name: data
            for name in _AUTHORITY_FILES
            if (data := safepaths.read_regular_at(source_fd, name, limit=_AUTHORITY_LIMIT))
            is not None
        }
    finally:
        os.close(source_fd)
    if AUTHORITY_FILE not in contents:
        return False
    # Caddy's own folder (`…/caddy`) must exist: only `pki/authorities/local` below it
    # is ever made here, never anything above it.
    base = target.parents[2]
    expected = safepaths.identity(base)
    if expected is None:
        raise BackupError("caddy_not_ready")
    base_fd = safepaths.open_dir_nofollow(base, expect=expected)
    try:
        target_fd = safepaths.open_subdirs(base_fd, target.parts[-3:], create=0o700)
    finally:
        os.close(base_fd)
    try:
        os.fchown(target_fd, 0, 0)
        os.fchmod(target_fd, 0o700)
        with os.scandir(target_fd) as entries:
            stale = [
                entry.name
                for entry in entries
                if entry.name not in contents and not entry.is_dir(follow_symlinks=False)
            ]
        for name in stale:
            os.unlink(name, dir_fd=target_fd)
        for name, data in contents.items():
            temporary = f".{name}.tmp"
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=target_fd)
            handle = safepaths.write_new_at(target_fd, temporary, data)
            try:
                os.fchown(handle, 0, 0)
            finally:
                os.close(handle)
            os.replace(temporary, name, src_dir_fd=target_fd, dst_dir_fd=target_fd)
    finally:
        os.close(target_fd)
    _remove_leaf(leaf)
    return True


def _remove_leaf(leaf: Path) -> None:
    """Remove Caddy's leaf directory `<caddy>/certificates/local` through descriptors."""
    certificates = leaf.parent
    try:
        parent = safepaths.open_dir_nofollow(certificates)
    except FileNotFoundError:
        return
    try:
        safepaths.remove_tree_at(parent, leaf.name)
    except FileNotFoundError:
        pass
    finally:
        os.close(parent)


@dataclass(frozen=True)
class FilesRestored:
    authority_placed: bool  # False: the back-up has none; Caddy keeps its own
    removed: int  # links and special files removed after the restore


def restore_files(
    *, run: str | None, authority_target: Path, clear_documents: bool
) -> FilesRestored:
    """Put the run's files back, then Caddy's authority (before Caddy starts).

    The three directories are judged first, before any restic call, with or without
    `clear_documents`: on the fixed list, no glob, no overlap with each other or the
    back-up mount. restic restores exactly those three at their own paths.
    `clear_documents` (a restore with --replace) first empties them, so no page,
    authority or manifest of the replaced installation stays behind or is taken for the
    back-up's. Afterwards the three trees are cleaned (`safepaths.clean_tree`): only
    regular files and directories stay, without setuid bits, owned by uid 10001 when
    this runs as root.
    """
    directories = _judged_directories()
    safepaths.require_caddy_authority_dir(authority_target)
    repository = destination.configured_repository()
    password = destination.backup_password()
    found = find_run(repository, password, run)
    authority_copy = directories[1]
    owner = SERVICE_UID if os.geteuid() == 0 else None
    with backup_lock():
        if clear_documents:
            try:
                for directory in directories:
                    _empty(directory)
            except OSError:
                raise BackupError("documents_incomplete") from None
        # Whatever restic does with what is already there: a link, a special file or a
        # hard link in a target is refused before restic writes anything.
        for directory in directories:
            _check_target(directory)
        argv = restic.restore_argv(
            found.files.id, "/", [str(path) for path in directories], no_lock=True
        )
        try:
            _restic_output(repository, password, argv)
        except BackupError as error:
            if clear_documents:
                raise BackupError("documents_incomplete") from error
            raise
        try:
            removed = sum(_clean_restored(directory, owner) for directory in directories)
            placed = _place_authority(authority_copy, authority_target)
        except OSError:
            raise BackupError("restore_failed") from None
    return FilesRestored(authority_placed=placed, removed=removed)
