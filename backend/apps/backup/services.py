import os

from apps.backup import destination
from apps.backup.locks import backup_lock

# The uid the service runs as; the one-off container that initialises runs as root.
KEEPER_UID = 10001


def initialise_destination(*, allow_same_disk: bool) -> str:
    """Prepare the configured destination for the first time; returns its kind."""
    owner_uid = KEEPER_UID if os.geteuid() == 0 else None
    with backup_lock():
        destination.initialise(allow_same_disk=allow_same_disk, owner_uid=owner_uid)
    return destination.destination_kind()
