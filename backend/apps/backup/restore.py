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
import shutil
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

from apps.backup import destination, restic
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
from apps.backup.privatefile import write_private
from apps.backup.services import file_paths, location_secrets
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
# Caddy issues its leaf certificates here, next to `pki/authorities/<name>`.
_LEAF_DIR = "certificates"
# What caddy-publish-root.sh copies of Caddy's authority (never its temporary files).
_AUTHORITY_FILES = (AUTHORITY_FILE, "root.key", "intermediate.crt", "intermediate.key")


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
    argv = restic.snapshots_argv(f"kind:{kind}")
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
            dump_argv = restic.dump_argv(snapshot.id, restic.DB_FILENAME)
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


def _write_out(out_dir: Path, files: dict[str, str], owner: tuple[int, int] | None) -> None:
    """Write every file (0600) or none: one that fails removes those already written.

    A folder this call creates (0700) is handed to `owner` as well, so the host user
    can open it.
    """
    created = not out_dir.exists()
    written: list[Path] = []
    try:
        for name, text in files.items():
            write_private(out_dir, name, text)
            written.append(out_dir / name)
            if owner is not None:
                os.chown(out_dir / name, *owner)
        if created and owner is not None:
            os.chown(out_dir, *owner)
    except BaseException as error:
        for path in written:
            path.unlink(missing_ok=True)
        if created:
            with contextlib.suppress(OSError):
                out_dir.rmdir()
        if isinstance(error, OSError):
            raise BackupError("restore_failed") from error
        raise


def fetch(
    *,
    run: str | None,
    master_key_hex: str,
    separate_password: str | None,
    backup_dir_host: str | None,
    out_dir: Path,
    out_owner: tuple[int, int] | None,
) -> RunSnapshots:
    """Open the back-up, find the run, refuse what cannot be restored, then write.

    Writes `out_dir/manifest.json` and `out_dir/env` (the restored deploy/.env), and
    nothing at all when any check fails. The password is the separate one when given,
    else the one derived from the master key.
    """
    master_key = parse_master_key(master_key_hex)
    hidden = _key_spellings(master_key, master_key_hex)
    password = separate_password or crypto.derive_backup_password(master_key)
    repository = destination.configured_repository()
    found = find_run(repository, password, run, extra_secrets=hidden)

    config_dir = Path(settings.BRIEVIE_BACKUP_CONFIG_DIR)
    manifest_argv = restic.dump_argv(found.files.id, str(config_dir / MANIFEST_NAME))
    manifest_out = _restic_output(repository, password, manifest_argv, hidden)
    manifest_text = _text(manifest_out, "manifest_unreadable")
    manifest = manifest_from_json(manifest_text)
    if manifest.run_id != found.run_id:
        raise BackupError("manifest_unreadable")
    if unknown_migrations(manifest):
        raise BackupError("newer_backup")
    env_argv = restic.dump_argv(found.files.id, str(config_dir / ENV_COPY_NAME))
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
    _write_out(out_dir, {MANIFEST_NAME: manifest_text, ENV_COPY_NAME: env}, out_owner)
    return found


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


def _emptiable(directory: Path) -> Path:
    """`directory` when it is safe to empty: absolute, not `/`, not itself a link."""
    if not directory.is_absolute() or directory == Path("/") or directory.is_symlink():
        raise BackupError("restore_failed")
    return directory


def _empty(directory: Path) -> None:
    """Remove what is in `directory` (a volume's mount point stays); links are not followed."""
    if not directory.exists():
        return
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)


def _leaf_dir(authority_target: Path) -> Path:
    """`<caddy>/certificates/<name>` for `<caddy>/pki/authorities/<name>`."""
    return authority_target.parents[2] / _LEAF_DIR / authority_target.name


def _place_authority(source: Path, target: Path) -> bool:
    """Make `target` an exact copy of the restored authority, root-owned, 0700/0600.

    Returns False, and leaves Caddy's own pki alone, when the back-up has no authority
    (a public certificate). Caddy's leaf certificates are removed, so it issues new ones
    from the restored authority.
    """
    if not (source / AUTHORITY_FILE).is_file():
        return False
    names = [name for name in _AUTHORITY_FILES if (source / name).is_file()]
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(target, 0, 0)
    target.chmod(0o700)
    for entry in os.scandir(target):
        if entry.name not in names and not entry.is_dir(follow_symlinks=False):
            os.unlink(entry.path)
    for name in names:
        data = (source / name).read_bytes()
        temporary = target / f".{name}.tmp"
        temporary.unlink(missing_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target / name)
        os.chown(target / name, 0, 0)
    shutil.rmtree(_leaf_dir(target), ignore_errors=True)
    return True


def restore_files(*, run: str | None, authority_target: Path, clear_documents: bool) -> bool:
    """Put the run's files back, then Caddy's authority (before Caddy starts).

    restic restores the documents, the authority copy and the config folder at their
    own paths (as root it restores their owner, uid 10001). `clear_documents` (a
    restore with --replace) first empties all three, so no page, authority or manifest
    of the replaced installation stays behind or is taken for the back-up's. Returns
    whether an authority was placed (False: the back-up has none; Caddy keeps its own).
    """
    repository = destination.configured_repository()
    password = destination.backup_password()
    found = find_run(repository, password, run)
    authority_copy = Path(settings.BRIEVIE_AUTHORITY_COPY_DIR)
    with backup_lock():
        if clear_documents:
            replaced = [
                _emptiable(Path(path))
                for path in (
                    settings.BRIEVIE_STORAGE_ROOT,
                    settings.BRIEVIE_AUTHORITY_COPY_DIR,
                    settings.BRIEVIE_BACKUP_CONFIG_DIR,
                )
            ]
            try:
                for directory in replaced:
                    _empty(directory)
            except OSError:
                raise BackupError("documents_incomplete") from None
        argv = restic.restore_argv(found.files.id, "/", file_paths())
        try:
            _restic_output(repository, password, argv)
        except BackupError as error:
            if clear_documents:
                raise BackupError("documents_incomplete") from error
            raise
        try:
            return _place_authority(authority_copy, authority_target)
        except OSError:
            raise BackupError("restore_failed") from None
