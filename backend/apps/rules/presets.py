"""The preset rules: one per preset label, made from its `recognise` data (design 9.6)."""

from uuid import UUID

from django.db import transaction
from django.db.models import Max

from apps.labels.models import Label
from apps.labels.presets import PRESETS, PresetLabel
from apps.rules import definition
from apps.rules.engine import Condition
from apps.rules.models import Rule
from apps.workspaces.models import Workspace


def preset_definition(preset: PresetLabel, label_id: UUID) -> dict[str, object]:
    """Match `any` of the preset's conditions, then add its label."""
    return definition.to_json(
        "any",
        [Condition(field=c.field, op=c.op, value=c.value) for c in preset.recognise],
        [("add_label", label_id, None)],
    )


def ensure_preset_rules(workspace: Workspace) -> None:
    """Create the rule of every preset label that has none (matched on `system_key`).

    Appended after the existing rules, in preset order, in mode `propose`. A deleted
    preset rule comes back here, when the user adds the start set again. Needs the
    workspace's RLS context.
    """
    with transaction.atomic():
        labels = {
            label.system_key: label
            for label in Label.objects.filter(workspace=workspace).exclude(system_key="")
        }
        taken = set(
            Rule.objects.filter(workspace=workspace)
            .exclude(system_key="")
            .values_list("system_key", flat=True)
        )
        last = Rule.objects.filter(workspace=workspace).aggregate(last=Max("position"))["last"]
        position = (last or 0) + 1
        for preset in PRESETS[workspace.kind]:
            label = labels.get(preset.key)
            if label is None or preset.key in taken:
                continue
            Rule.objects.create(
                workspace=workspace,
                position=position,
                name=f"Label {label.name}",
                mode=Rule.Mode.PROPOSE,
                definition=preset_definition(preset, label.id),
                system_key=preset.key,
            )
            position += 1
