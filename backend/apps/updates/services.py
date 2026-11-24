"""What `./brievie update` asks the image: free space, the health after the update, and
the record of a finished update."""

import json
import os
import stat
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from apps.backup.destination import destination_kind
from apps.updates.models import InstalledVersion, ServerUpdate
from apps.updates.release import ReleaseError, Version, space_needed

HEALTH_URL = "http://127.0.0.1:8000/api/v1/health"
HEALTH_TRIES = 12
HEALTH_DELAY = 5.0
_HEALTH_TIMEOUT = 5.0


@transaction.atomic
def record_update(
    *, from_version: str, to_version: str, backup_run_id: UUID | None
) -> ServerUpdate:
    return ServerUpdate.objects.create(
        from_version=str(Version.parse(from_version)),
        to_version=str(Version.parse(to_version)),
        backup_run_id=backup_run_id,
        finished_at=timezone.now(),
    )


def last_update() -> ServerUpdate | None:
    return ServerUpdate.objects.order_by("-finished_at").first()


def installed_version() -> Version | None:
    """The version the database belongs to: the recorded row, else the newest finished
    update's target, else None (a fresh database, or one that was never recorded)."""
    row = InstalledVersion.objects.first()
    if row is not None:
        return Version.parse(row.version)
    update = last_update()
    return Version.parse(update.to_version) if update is not None else None


@transaction.atomic
def set_installed_version(version: Version) -> None:
    """Record `version` as the one the database belongs to. A lower version than the
    recorded one is refused (`ReleaseError("downgrade")`): the gate refuses it first, so
    this is the second lock. A restore needs no exception: the row is part of the back-up."""
    row = InstalledVersion.objects.select_for_update().first()
    if row is None:
        InstalledVersion.objects.create(version=str(version))
        return
    if version < Version.parse(row.version):
        raise ReleaseError("downgrade")
    if str(version) != row.version:
        row.version = str(version)
        row.save(update_fields=["version", "updated_at"])


@dataclass(frozen=True)
class Preflight:
    free_bytes: int
    needed_bytes: int
    fallback: bool


def _tree_bytes(root: Path) -> int:
    """The size of the regular files under `root`; no link is followed or counted."""
    total = 0
    for directory, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                info = os.lstat(os.path.join(directory, name))
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def _database_bytes() -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_database_size(current_database())")
        row = cursor.fetchone()
    return int(row[0]) if row else 0


def preflight() -> Preflight:
    """Free space on the disk of the documents (in the default Docker set-up the volumes
    and the images share it) against what the update needs: building the new image, and
    a copy of the data when the emergency back-up goes to the local fallback."""
    root = Path(settings.BRIEVIE_STORAGE_ROOT)
    fallback = destination_kind() == ""
    documents = _tree_bytes(root) if fallback else 0
    database = _database_bytes() if fallback else 0
    info = os.statvfs(root)
    return Preflight(
        free_bytes=info.f_bavail * info.f_frsize,
        needed_bytes=space_needed(
            documents_bytes=documents, database_bytes=database, fallback=fallback
        ),
        fallback=fallback,
    )


def health_verdict(status: int, body: bytes, expected: str) -> str:
    """`""` when the server answers 200 with `server_version == expected`; otherwise
    `unhealthy` (no answer, not 200, not JSON) or `wrong_version`."""
    if status != 200:
        return "unhealthy"
    try:
        data = json.loads(body)
    except ValueError:
        return "unhealthy"
    if not isinstance(data, dict):
        return "unhealthy"
    return "" if data.get("server_version") == expected else "wrong_version"


def _get_health() -> tuple[int, bytes]:
    """GET the health endpoint inside the api container; (0, b"") when nothing answers.
    The Host header is the first allowed host, as the compose healthcheck sends it."""
    hosts = os.environ.get("ALLOWED_HOSTS", "").split(",")
    host = hosts[0].strip().lstrip(".") if hosts else ""
    request = urllib.request.Request(
        HEALTH_URL, headers={"Host": "localhost" if host in ("", "*") else host}
    )
    try:
        with urllib.request.urlopen(request, timeout=_HEALTH_TIMEOUT) as response:  # noqa: S310
            return int(response.status), response.read()
    except urllib.error.HTTPError as error:
        return int(error.code), error.read()
    except (OSError, ValueError):
        return 0, b""


def wait_until_healthy(
    expected: str,
    *,
    tries: int = HEALTH_TRIES,
    delay: float = HEALTH_DELAY,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """The verdict of the first good answer, or of the last one after `tries` tries."""
    verdict = "unhealthy"
    for attempt in range(tries):
        if attempt:
            sleep(delay)
        status, body = _get_health()
        verdict = health_verdict(status, body, expected)
        if not verdict:
            return ""
    return verdict
