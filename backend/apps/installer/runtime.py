"""The real `Deps` of `prepare_server`: Django, the settings and the container's files."""

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from uuid import UUID

import psycopg
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.utils.translation import gettext as _

from apps.backup import services as backup_services
from apps.backup.destination import destination_kind
from apps.backup.envcopy import store_env_copy
from apps.backup.errors import BackupError
from apps.backup.models import BackupRun
from apps.common import secrets as secret_files
from apps.common.version import server_version
from apps.installer.autoinit import should_auto_init
from apps.installer.database import Connect, Connection, Names, Passwords
from apps.installer.prepare import BackupResult, Deps, MigrateError
from apps.updates import gate, services
from apps.updates.release import ReleaseError, Version, parse_release

# The settings (not secrets) that a restore on a new machine needs from the old one; the
# back-up refuses without this copy (`env_copy_missing`).
SETTINGS_COPY_KEYS = (
    "BRIEVIE_HOSTNAME",
    "BRIEVIE_HTTP_PORT",
    "BRIEVIE_HTTPS_PORT",
    "BRIEVIE_VERSION",
    "BRIEVIE_HSTS_SECONDS",
    "BRIEVIE_MAX_BODY_BYTES",
    "BRIEVIE_MAX_UPLOAD_BYTES",
    "BRIEVIE_AUDIT_RETENTION_DAYS",
    "BRIEVIE_SESSION_IDLE_DAYS",
    "BRIEVIE_SESSION_MAX_DAYS",
    "BRIEVIE_AI_AUTOPILOT_ALLOWED",
)


class ConfigurationError(Exception):
    """The container is not set up as `init` needs; the text is a Dutch sentence."""


def settings_copy_text(environ: Mapping[str, str]) -> str:
    lines = ["# Written by Brievie at start (compose install): the settings without secrets."]
    lines += [f"{key}={environ[key]}" for key in SETTINGS_COPY_KEYS if environ.get(key)]
    return "\n".join(lines) + "\n"


def release_file() -> Path:
    base = Path(settings.BASE_DIR)
    for candidate in (base / "release.json", base.parent / "deploy" / "release.json"):
        if candidate.is_file():
            return candidate
    raise ConfigurationError(_("Het bestand release.json ontbreekt in dit image."))


def _superuser_connect() -> Connect:
    account = settings.DATABASE_SUPERUSER
    if account is None:
        raise ConfigurationError(_("DATABASE_SUPERUSER_URL ontbreekt: dit is geen compose-start."))

    def connect(dbname: str) -> Connection:
        return psycopg.connect(
            dbname=dbname,
            user=str(account["USER"]),
            password=str(account["PASSWORD"]),
            host=str(account["HOST"]),
            port=str(account["PORT"]),
            autocommit=True,
            # The freeze does not apply to this session (apps.installer.freeze).
            options="-c default_transaction_read_only=off",
        )

    return connect


def _passwords(environ: Mapping[str, str]) -> Passwords:
    try:
        found = {
            name: secret_files.read_secret(name, environ)
            for name in (
                "DATABASE_OWNER_PASSWORD",
                "DATABASE_PASSWORD",
                "DATABASE_BACKUP_PASSWORD",
            )
        }
    except secret_files.SecretFileError as error:
        raise ConfigurationError(
            _("Een wachtwoordbestand kan niet worden gebruikt (%(code)s).") % {"code": error.code}
        ) from None
    if not all(found.values()):
        raise ConfigurationError(_("De wachtwoorden van de database ontbreken."))
    return Passwords(
        owner=found["DATABASE_OWNER_PASSWORD"],
        app=found["DATABASE_PASSWORD"],
        backup=found["DATABASE_BACKUP_PASSWORD"],
    )


def _migrate() -> None:
    def pending() -> int:
        executor = MigrationExecutor(connection)
        return len(executor.migration_plan(executor.loader.graph.leaf_nodes()))

    before = pending()
    try:
        call_command("migrate", interactive=False, verbosity=0)
        call_command("createcachetable", verbosity=0)
    except Exception:  # noqa: BLE001 - a failing migration may raise anything; only the fact counts
        connection.close()
        raise MigrateError(applied_any=pending() < before) from None


def _backup(label: str) -> BackupResult:
    try:
        if destination_kind() == "":
            # No destination of the user's: the volume update-backup, prepared now.
            settings.BRIEVIE_BACKUP_DIR_SET = True
            try:
                backup_services.initialise_destination(allow_same_disk=True, fallback=True)
            except BackupError as error:
                if error.code != "already_initialised":
                    return BackupResult(ok=False)
            run = backup_services.run_update_fallback(label=label)
        else:
            run = backup_services.run_update_safety(label=label)
    except BackupError:
        return BackupResult(ok=False)
    return BackupResult(ok=run.status == BackupRun.Status.SUCCEEDED, run_id=run.id)


def _auto_init() -> str:
    """The back-up folder, prepared once when it is safe (apps.installer.autoinit)."""
    if not settings.BRIEVIE_BACKUP_AUTO_INIT:
        return ""
    try:
        kind = destination_kind()
    except BackupError:
        return ""
    mount = Path(settings.BRIEVIE_BACKUP_MOUNT)
    if not should_auto_init(enabled=True, kind=kind, mount=mount):
        return ""
    try:
        backup_services.initialise_destination(allow_same_disk=False)
    except BackupError as error:
        if error.code == "already_initialised":
            return ""
        return _("De back-upmap is niet vanzelf klaargezet: %(reason)s") % {"reason": error.message}
    return _("De back-upmap is klaargezet voor de back-ups.")


def _earlier_backup(label: str) -> UUID | None:
    run = (
        BackupRun.objects.filter(
            label=label,
            requested_by=backup_services.UPDATE_SAFETY,
            status=BackupRun.Status.SUCCEEDED,
        )
        .order_by("-created_at")
        .first()
    )
    return run.id if run else None


def _record(installed: Version | None, image: Version, run_id: UUID | None) -> None:
    services.set_installed_version(image)
    if installed is not None and installed != image:
        services.record_update(
            from_version=str(installed), to_version=str(image), backup_run_id=run_id
        )


def _preflight() -> tuple[int, int]:
    result = services.preflight()
    return result.free_bytes, result.needed_bytes


def build_deps(
    environ: Mapping[str, str], say: Callable[[str], None], complain: Callable[[str], None]
) -> Deps:
    try:
        release = parse_release(release_file().read_text("utf-8"))
        adopt_text = environ.get("BRIEVIE_ADOPT_VERSION", "").strip()
        adopt = Version.parse(adopt_text) if adopt_text else None
    except (ReleaseError, OSError):
        raise ConfigurationError(_("De versiegegevens van dit image kloppen niet.")) from None
    revision_text = environ.get("BRIEVIE_COMPOSE_REVISION", "")
    revision = int(revision_text) if revision_text.isdigit() else 0
    loader = MigrationLoader(None, ignore_no_migrations=True)
    own = frozenset(
        config.label for config in apps.get_app_configs() if config.name.startswith("apps.")
    )
    return Deps(
        connect=_superuser_connect(),
        names=Names(),
        passwords=_passwords(environ),
        image=Version.parse(server_version()),
        release=release,
        compose_revision=revision,
        image_compose_revision=gate.COMPOSE_REVISION,
        adopt_version=adopt,
        hostname=environ.get("BRIEVIE_HOSTNAME", ""),
        known_migrations=frozenset(loader.disk_migrations),
        own_apps=own,
        auto_init=_auto_init,
        migrate=_migrate,
        backup=_backup,
        earlier_backup=_earlier_backup,
        record=_record,
        store_settings_copy=lambda: store_env_copy(settings_copy_text(environ)),
        preflight=_preflight,
        say=say,
        complain=complain,
    )


def own_environ() -> Mapping[str, str]:
    return os.environ
