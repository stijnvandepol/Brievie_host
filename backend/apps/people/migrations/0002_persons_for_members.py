from itertools import groupby

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from apps.common.privacy import mask_bsns
from apps.workspaces.rls import SETTING_NAME


def _free_name(name: str, taken: set[str]) -> str:
    """`name`, or `name (2)`, `name (3)`, ... when taken (ignoring case); at most 150 chars."""
    candidate = name[:150]
    number = 2
    while candidate.lower() in taken:
        suffix = f" ({number})"
        candidate = f"{name[: 150 - len(suffix)]}{suffix}"
        number += 1
    return candidate


def create_persons_for_members(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Give every member a linked person, as people.services.ensure_person_for_member does.

    A frozen copy of that logic: the migration may not use the live models. The owner
    role is under forced RLS too, so each workspace's context is entered before its
    persons are read or written, and cleared at the end.
    """
    membership_model = apps.get_model("workspaces", "Membership")
    person_model = apps.get_model("people", "Person")
    memberships = (
        membership_model._default_manager.order_by("workspace_id", "created_at", "id")
        .values_list("workspace_id", "user_id", "user__name")
        .iterator()
    )
    with schema_editor.connection.cursor() as cursor:
        for workspace_id, group in groupby(memberships, key=lambda row: row[0]):
            cursor.execute("SELECT set_config(%s, %s, true)", [SETTING_NAME, str(workspace_id)])
            persons = person_model._default_manager.filter(workspace_id=workspace_id)
            linked = set(persons.exclude(user=None).values_list("user_id", flat=True))
            taken = {name.lower() for name in persons.values_list("display_name", flat=True)}
            for _, user_id, user_name in group:
                if user_id in linked:
                    continue
                name = mask_bsns(user_name).strip()
                display_name = _free_name(name, taken)
                taken.add(display_name.lower())
                person_model._default_manager.create(
                    workspace_id=workspace_id,
                    display_name=display_name,
                    name_variants=[name] if 2 <= len(name) <= 100 else [],
                    user_id=user_id,
                )
        cursor.execute("SELECT set_config(%s, '', true)", [SETTING_NAME])


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0001_initial"),
        ("workspaces", "0002_workspacekey"),
    ]

    operations = [
        migrations.RunPython(create_persons_for_members, migrations.RunPython.noop),
    ]
