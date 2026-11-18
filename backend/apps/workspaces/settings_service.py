"""A workspace's own settings (D 6.3), changed by its admins.

Part B adds its settings as further optional fields: a field left out (None) leaves the
setting as it is, so an older client never wipes a newer setting. The AI setting has its
own service in `apps.pipeline.settings_service` because it needs consent.
"""

from django.db import transaction

from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.workspaces.models import Workspace


def update_settings(
    *, workspace: Workspace, actor: AuditActor, allow_member_export: bool | None = None
) -> Workspace:
    """Change the given settings and log each one that really changed, in the same
    transaction. The workspace row is locked, so two admins act one after the other."""
    with transaction.atomic():
        locked = Workspace.objects.select_for_update().get(id=workspace.id)
        if allow_member_export is not None and locked.allow_member_export != allow_member_export:
            locked.allow_member_export = allow_member_export
            locked.save(update_fields=["allow_member_export", "updated_at"])
            record(
                AuditCode.WORKSPACE_SETTING_CHANGED,
                actor=actor,
                target=AuditTarget(TargetKind.WORKSPACE, locked.id, locked.name),
                workspace_id=locked.id,
                values={"setting": "allow_member_export", "value": allow_member_export},
            )
        return locked
