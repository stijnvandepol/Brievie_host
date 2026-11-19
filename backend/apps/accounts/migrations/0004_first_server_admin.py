from django.apps.registry import Apps
from django.db import migrations


def mark_first_server_admin(apps: Apps, schema_editor: object) -> None:
    """On a server that has users but no server admin yet, make one.

    The admin whose membership is oldest in the oldest workspace (by created_at,
    then id); with no workspace, the oldest active user. Changes nothing when a
    server admin exists, so running it twice is safe.
    """
    user_model = apps.get_model("accounts", "User")
    if not user_model.objects.exists() or user_model.objects.filter(is_server_admin=True).exists():
        return
    workspace_model = apps.get_model("workspaces", "Workspace")
    membership_model = apps.get_model("workspaces", "Membership")

    chosen_id = None
    for workspace in workspace_model.objects.order_by("created_at", "id"):
        membership = (
            membership_model.objects.filter(workspace=workspace, role="admin", user__is_active=True)
            .order_by("created_at", "id")
            .first()
        )
        if membership is not None:
            chosen_id = membership.user_id
            break
    if chosen_id is None:
        oldest = user_model.objects.filter(is_active=True).order_by("created_at", "id").first()
        chosen_id = oldest.id if oldest is not None else None
    if chosen_id is not None:
        user_model.objects.filter(id=chosen_id).update(is_server_admin=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_user_is_server_admin"),
        ("workspaces", "0002_workspacekey"),
    ]

    operations = [
        migrations.RunPython(mark_first_server_admin, migrations.RunPython.noop),
    ]
