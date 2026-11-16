from apps.documents.models import Document
from apps.labels.models import COLORS, Label
from apps.labels.presets import CONDITION_OPERATORS, PRESETS, apply_preset
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace

# Length limits of a condition value per field (design 9.2).
_VALUE_LENGTH = {"text": (2, 100), "sender": (2, 200)}


def names(workspace: Workspace) -> list[str]:
    with workspace_context(workspace.id):
        return sorted(Label.objects.filter(workspace=workspace).values_list("name", flat=True))


def test_apply_preset_is_idempotent_and_restores_only_missing(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        Label.objects.get(workspace=ws_a, system_key="zorg").delete()
        auto = Label.objects.get(workspace=ws_a, system_key="auto")
        auto.name = "Onze auto"
        auto.save()

        labels = apply_preset(ws_a)
        again = apply_preset(ws_a)

        assert len(labels) == 10
        assert [label.id for label in again] == [label.id for label in labels]
        assert Label.objects.get(workspace=ws_a, system_key="zorg").name == "Zorg"
        assert Label.objects.get(workspace=ws_a, system_key="auto").name == "Onze auto"
    assert "Auto" not in names(ws_a)
    assert len(names(ws_a)) == 10


def test_apply_preset_skips_a_taken_name(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        Label.objects.get(workspace=ws_a, system_key="bank").delete()
        own = Label.objects.create(workspace=ws_a, name="bank", color="red")

        apply_preset(ws_a)

        assert not Label.objects.filter(workspace=ws_a, system_key="bank").exists()
        own.refresh_from_db()
        assert (own.name, own.color, own.system_key) == ("bank", "red", "")
    assert len(names(ws_a)) == 10


def test_every_preset_color_and_condition_is_valid() -> None:
    assert set(PRESETS) == set(Workspace.Kind.values)
    for kind, labels in PRESETS.items():
        keys = [label.key for label in labels]
        assert len(keys) == len(set(keys)), kind
        assert len({label.name.lower() for label in labels}) == len(labels), kind
        for label in labels:
            assert label.color in COLORS, label
            assert 1 <= len(label.name) <= 50, label
            assert label.recognise, label
            for condition in label.recognise:
                assert condition.op in CONDITION_OPERATORS[condition.field], condition
                if condition.field == "document_type":
                    assert condition.value in Document.Type.values, condition
                else:
                    low, high = _VALUE_LENGTH[condition.field]
                    assert low <= len(condition.value) <= high, condition
