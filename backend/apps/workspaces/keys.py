from uuid import UUID

from django.db import IntegrityError, connection, transaction

from apps.common import crypto
from apps.workspaces.models import WorkspaceKey
from apps.workspaces.rls import SETTING_NAME


def workspace_key(workspace_id: UUID) -> bytes:
    """The workspace's data key, created on first use.

    It encrypts the workspace's document files and stored secrets, and is itself
    stored wrapped with the master key, bound to the workspace id. Must run inside
    the workspace's context.
    """
    _require_context(workspace_id)
    stored = WorkspaceKey.objects.filter(workspace_id=workspace_id).first()
    if stored is None:
        key = crypto.new_key()
        wrapped = crypto.seal(crypto.master_key(), key, aad=workspace_id.bytes)
        try:
            with transaction.atomic():
                WorkspaceKey.objects.create(workspace_id=workspace_id, wrapped_key=wrapped)
        except IntegrityError:
            # Another request created the key first; use that one.
            stored = WorkspaceKey.objects.get(workspace_id=workspace_id)
        else:
            return key
    return crypto.open_sealed(
        crypto.master_key(), bytes(stored.wrapped_key), aad=workspace_id.bytes
    )


def _require_context(workspace_id: UUID) -> None:
    with connection.cursor() as c:
        c.execute("SELECT COALESCE(current_setting(%s, true), '')", [SETTING_NAME])
        row = c.fetchone()
    if row is None or row[0] != str(workspace_id):
        raise RuntimeError("workspace_key() needs the workspace context of that workspace.")
