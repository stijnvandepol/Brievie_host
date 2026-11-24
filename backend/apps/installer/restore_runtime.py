"""The real steps of `restore_install`: the library calls the wrapper's restore commands make."""

import contextlib
from collections.abc import Mapping
from pathlib import Path

import psycopg
from django.conf import settings
from django.utils.translation import gettext as _
from psycopg import sql

from apps.backup import restore, verify
from apps.backup.errors import BackupError
from apps.backup.manifest import MANIFEST_NAME
from apps.common import crypto
from apps.installer import init_secrets, restore_install
from apps.installer.database import (
    Connect,
    DatabaseSetupError,
    Names,
    Passwords,
    ensure_roles_and_database,
)
from apps.installer.restore_install import StepError, Steps
from apps.installer.runtime import _passwords, _superuser_connect

AUTHORITY_TARGET = Path("/caddy-data/caddy/pki/authorities/local")
VAULT = Path("/run/brievie")
OWNER = (10001, 10001)


def _guard(error: BackupError) -> StepError:
    return StepError(error.message)


def _separate_password() -> str | None:
    """The configured back-up password when it is a real, separate one: the derived one
    (always in the vault) proves nothing extra and needs no second confirmation."""
    configured = str(settings.BRIEVIE_BACKUP_PASSWORD).strip()
    derived = crypto.derive_backup_password(crypto.master_key())
    return configured if configured and configured.lower() != derived else None


def build_steps(environ: Mapping[str, str]) -> Steps:
    connect: Connect = _superuser_connect()
    passwords: Passwords = _passwords(environ)
    names = Names()
    manifest = Path(settings.BRIEVIE_BACKUP_CONFIG_DIR) / MANIFEST_NAME

    def open_sessions() -> int:
        try:
            with connect("postgres") as server:
                row = server.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = %s"
                    " AND usename = ANY(%s) AND pid <> pg_backend_pid()",
                    [names.database, [names.app, names.backup, names.owner]],
                ).fetchone()
        except psycopg.Error:
            raise StepError(
                _("De database is niet bereikbaar. Start eerst: docker compose up -d postgres.")
            ) from None
        return int(row[0]) if row else 0

    def has_data() -> bool:
        with connect("postgres") as server:
            known = server.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", [names.database]
            ).fetchone()
        if known is None:
            return False
        with connect(names.database) as inside:
            present = inside.execute(
                "SELECT count(*) = 3 FROM pg_tables WHERE schemaname = 'public' AND tablename"
                " IN ('accounts_user', 'workspaces_workspace', 'documents_document')"
            ).fetchone()
            if not (present and present[0]):
                return False
            row = inside.execute(
                "SELECT (SELECT count(*) FROM accounts_user)"
                " + (SELECT count(*) FROM workspaces_workspace)"
                " + (SELECT count(*) FROM documents_document)"
            ).fetchone()
        return bool(row and row[0])

    def fetch(wanted: str | None) -> str:
        try:
            found = restore.fetch(
                run=wanted,
                master_key_hex=str(settings.BRIEVIE_MASTER_KEY),
                separate_password=_separate_password(),
                backup_dir_host=None,
                out_dir=restore_install.STAGING,
                out_owner=None,
            )
        except BackupError as error:
            raise _guard(error) from None
        return str(found.run_id)

    def check(found: str) -> None:
        try:
            restore.check_run(found)
        except BackupError as error:
            raise _guard(error) from None

    def reset_database() -> None:
        try:
            with connect("postgres") as server:
                server.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(names.database)
                    )
                )
            ensure_roles_and_database(connect, passwords, names)
        except (psycopg.Error, DatabaseSetupError):
            raise StepError(
                _("De oude database kon niet worden leeggemaakt. Er is niets teruggezet.")
            ) from None

    def load_database(found: str) -> None:
        try:
            restore.restore_database(run=found, dbname=names.database)
        except BackupError as error:
            raise _guard(error) from None

    def load_files(found: str) -> None:
        try:
            restore.restore_files(
                run=found, authority_target=AUTHORITY_TARGET, clear_documents=True
            )
        except BackupError as error:
            raise _guard(error) from None

    def adopt_id() -> None:
        try:
            init_secrets.adopt_installation_id(
                VAULT, Path(settings.BRIEVIE_BACKUP_CONFIG_DIR), OWNER
            )
        except init_secrets.InitSecretsError as error:
            raise StepError(error.sentence()) from None

    def verify_result() -> str:
        try:
            report = verify.verify_installation(
                dbname=names.database,
                storage_root=Path(settings.BRIEVIE_STORAGE_ROOT),
                manifest_path=manifest,
                authority_dir=AUTHORITY_TARGET,
            )
        except BackupError as error:
            raise _guard(error) from None
        if report.problems:
            raise StepError(
                _("Het herstel is niet compleet: %(count)d probleem gevonden. Lees de logs.")
                % {"count": len(report.problems)}
            )
        return _(
            "Het herstel is gecontroleerd: %(workspaces)d werkruimtes, %(pages)d pagina's."
        ) % {
            "workspaces": report.workspaces,
            "pages": report.pages_opened,
        }

    def clean_staging() -> None:
        # The folder is a tmpfs of the one-off container; it goes with the container.
        with contextlib.suppress(BackupError, OSError):
            restore_install.remove_staging(restore_install.STAGING)

    return Steps(
        open_sessions=open_sessions,
        has_data=has_data,
        fetch=fetch,
        check=check,
        reset_database=reset_database,
        load_database=load_database,
        load_files=load_files,
        adopt_id=adopt_id,
        verify=verify_result,
        clean_staging=clean_staging,
    )
