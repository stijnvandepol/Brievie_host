from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

# Frozen copy of apps.workspaces.rls.SETTING_NAME.
SETTING_NAME = "brievie.workspace_id"

# (field, op, value) conditions of a preset rule: the old form and its replacement.
# A frozen copy: the presets in apps.labels.presets may change again later.
Conditions = tuple[tuple[str, str, str], ...]


def _words(*words: str) -> Conditions:
    return tuple(("text", "contains_word", word) for word in words)


_REPLACEMENTS: dict[str, dict[Conditions, Conditions]] = {
    # "overeenkomst" and "algemene voorwaarden" are in almost every invoice.
    "contracten": {
        (
            ("document_type", "is", "contract"),
            *_words("overeenkomst", "algemene voorwaarden"),
        ): (("document_type", "is", "contract"),),
    },
    # "aanslag": a municipal tax bill says it too.
    "belastingdienst": {
        _words("belastingdienst", "toeslagen", "aanslag"): _words("belastingdienst", "toeslagen"),
        _words("belastingdienst", "aanslag", "naheffing", "aangifte"): _words(
            "belastingdienst", "naheffing", "aangifte"
        ),
    },
}


def _definition(conditions: Conditions, label_id: str) -> dict[str, object]:
    return {
        "version": 1,
        "match": "any",
        "conditions": [{"field": f, "op": o, "value": v} for f, o, v in conditions],
        "actions": [{"type": "add_label", "label_id": label_id, "person_id": None}],
    }


def _replacement(key: str, stored: object, label_id: str | None) -> dict[str, object] | None:
    """The new definition when `stored` is exactly an old preset's that adds `label_id`
    (the workspace's label with the rule's `system_key`); else None (edited)."""
    if not isinstance(stored, dict):
        return None
    actions = stored.get("actions")
    if not isinstance(actions, list) or len(actions) != 1 or not isinstance(actions[0], dict):
        return None
    if label_id is None or actions[0].get("label_id") != label_id:
        return None
    for old, new in _REPLACEMENTS[key].items():
        if stored == _definition(old, label_id):
            return _definition(new, label_id)
    return None


def update_old_preset_rules(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """Give an unedited old Contracten or Belastingdienst preset rule the current definition.

    A rule the user changed in any way is left alone, also one whose action points at
    another label than the workspace's label with the rule's `system_key`. The owner role is under forced RLS,
    so each workspace's context is entered before its rules are read or written.
    """
    workspace_model = apps.get_model("workspaces", "Workspace")
    rule_model = apps.get_model("rules", "Rule")
    label_model = apps.get_model("labels", "Label")
    workspace_ids = list(workspace_model._default_manager.values_list("id", flat=True))
    with schema_editor.connection.cursor() as cursor:
        for workspace_id in workspace_ids:
            cursor.execute("SELECT set_config(%s, %s, true)", [SETTING_NAME, str(workspace_id)])
            rules = rule_model._default_manager.filter(
                workspace_id=workspace_id, system_key__in=list(_REPLACEMENTS)
            )
            labels = {
                key: str(label_id)
                for key, label_id in label_model._default_manager.filter(
                    workspace_id=workspace_id, system_key__in=list(_REPLACEMENTS)
                ).values_list("system_key", "id")
            }
            for rule in rules:
                new = _replacement(rule.system_key, rule.definition, labels.get(rule.system_key))
                if new is not None:
                    rule.definition = new
                    rule.save(update_fields=["definition", "updated_at"])
        cursor.execute("SELECT set_config(%s, '', true)", [SETTING_NAME])


class Migration(migrations.Migration):
    dependencies = [
        ("labels", "0001_initial"),
        ("rules", "0002_rulerun"),
        ("workspaces", "0002_workspacekey"),
    ]

    operations = [
        migrations.RunPython(update_old_preset_rules, migrations.RunPython.noop),
    ]
