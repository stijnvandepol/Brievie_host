"""Restore onto an empty machine, part 1: open the back-up and write the restored .env.

Runs in a one-off container that has the master key (typed from the recovery sheet)
and, when the back-ups have their own password, that password. Every refusal comes
before anything is written: a wrong password, a wrong master key, a back-up of a
newer Brievie, or a run that does not exist leave the output folder as it was.
"""

import contextlib
import logging
import os
import re
import subprocess
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from uuid import UUID

from django.conf import settings

from apps.backup import destination, restic
from apps.backup.envcopy import ENV_COPY_NAME, restored_env
from apps.backup.errors import BackupError
from apps.backup.manifest import MANIFEST_NAME, manifest_from_json, unknown_migrations
from apps.backup.privatefile import write_private
from apps.backup.services import location_secrets
from apps.common import crypto

logger = logging.getLogger(__name__)

KEY_TABLE = "workspaces_workspacekey"
# Reads the custom-format archive from stdin (no file name given) and prints the
# key table's COPY block on stdout. Connects to no database, so it needs no secret.
PG_RESTORE_KEYS_COMMAND = ("pg_restore", "--data-only", f"--table={KEY_TABLE}", "--file=-")

_COPY_HEADER = re.compile(
    r'^COPY (?:"?public"?\.)?"?' + KEY_TABLE + r'"? \((?P<columns>[^)]*)\) FROM stdin;$'
)
_COPY_END = "\\."
_COPY_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
_COPY_ESCAPE = re.compile(r"\\(.)")
# restic failures a restore reports as such; anything else is "the restore failed".
# Never `files_incomplete` (exit 3): its sentence says a back-up was made.
_RESTORE_CODES = frozenset({"wrong_password", "repository_missing", "destination_unreachable"})
_CHUNK = 1 << 16
_STDERR_TAIL = 500
_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass(frozen=True)
class RunSnapshots:
    run_id: UUID
    db: restic.Snapshot
    files: restic.Snapshot


def _secrets(repository: restic.Repository, password: str) -> list[str]:
    return [password, *destination.secrets(), *location_secrets(repository)]


def _failure(returncode: int, stderr: str) -> BackupError:
    """The error of a failed restic call on the restore side; its stderr goes to the log."""
    logger.warning("Restore: restic exited with %s: %s", returncode, stderr[-_STDERR_TAIL:])
    code = restic.classify(restic.ResticResult(returncode=returncode, stdout=b"", stderr=stderr))
    return BackupError(code if code in _RESTORE_CODES else "restore_failed")


def _restic_output(repository: restic.Repository, password: str, argv: Sequence[str]) -> bytes:
    env = restic.restic_env(repository, password)
    try:
        result = restic.run(argv, env, secrets=_secrets(repository, password))
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
    repository: restic.Repository, password: str, kind: str
) -> dict[UUID, restic.Snapshot]:
    stdout = _restic_output(repository, password, restic.snapshots_argv(f"kind:{kind}"))
    try:
        snapshots = restic.parse_snapshots(stdout)
    except BackupError as error:
        raise BackupError("restore_failed") from error
    return _newest_by_run(snapshots)


def find_run(repository: restic.Repository, password: str, run: str | None) -> RunSnapshots:
    """The snapshots of run `run` (a run id), or of the newest run that has both kinds.

    A run whose files step ended incomplete (restic exit 3) has both and is restorable.
    """
    wanted: UUID | None = None
    if run is not None:
        try:
            wanted = UUID(run)
        except ValueError:
            raise BackupError("run_not_found") from None
    db = _snapshots_of_kind(repository, password, "db")
    files = _snapshots_of_kind(repository, password, "files")
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


def _tool_env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", _DEFAULT_PATH)}


def _dump_through(
    repository: restic.Repository,
    password: str,
    snapshot: restic.Snapshot,
    consumer: Sequence[str],
) -> bytes:
    """`restic dump <snapshot> brievie.pgdump | <consumer>`; the consumer's stdout.

    Both run as child processes joined by a pipe through this process: the dump is
    never written to disk. Both exit codes count, so a dump that broke off is not
    passed off by a consumer that read a truncated archive without complaint. On any
    error neither process is left running.
    """
    secrets = _secrets(repository, password)
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
                env=_tool_env(),
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
        stderr = restic.redact(b"".join(consumer_err).decode(errors="replace"), secrets)
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
    output = _dump_through(repository, password, db_snapshot, PG_RESTORE_KEYS_COMMAND)
    row = _first_key_row(output.decode("utf-8", errors="replace"))
    if row is None:
        return
    workspace_id, wrapped = row
    try:
        crypto.open_sealed(master_key, wrapped, aad=workspace_id.bytes)
    except crypto.DecryptionError:
        raise BackupError("wrong_master_key") from None


def _master_key(master_key_hex: str) -> bytes:
    try:
        key = bytes.fromhex(master_key_hex.strip())
    except ValueError:
        raise BackupError("wrong_master_key") from None
    if len(key) != crypto.KEY_BYTES:
        raise BackupError("wrong_master_key")
    return key


def _text(data: bytes, code: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise BackupError(code) from None


def _write_out(out_dir: Path, files: dict[str, str], owner: tuple[int, int] | None) -> None:
    """Write every file (0600) or none: one that fails removes those already written."""
    written: list[Path] = []
    try:
        for name, text in files.items():
            write_private(out_dir, name, text)
            written.append(out_dir / name)
            if owner is not None:
                os.chown(out_dir / name, *owner)
    except BaseException as error:
        for path in written:
            path.unlink(missing_ok=True)
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
    master_key = _master_key(master_key_hex)
    password = separate_password or crypto.derive_backup_password(master_key)
    repository = destination.configured_repository()
    found = find_run(repository, password, run)

    config_dir = Path(settings.BRIEVIE_BACKUP_CONFIG_DIR)
    manifest_argv = restic.dump_argv(found.files.id, str(config_dir / MANIFEST_NAME))
    manifest_out = _restic_output(repository, password, manifest_argv)
    manifest_text = _text(manifest_out, "manifest_unreadable")
    manifest = manifest_from_json(manifest_text)
    if manifest.run_id != found.run_id:
        raise BackupError("manifest_unreadable")
    if unknown_migrations(manifest):
        raise BackupError("newer_backup")
    env_argv = restic.dump_argv(found.files.id, str(config_dir / ENV_COPY_NAME))
    env_copy = _text(_restic_output(repository, password, env_argv), "restore_failed")
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
