import importlib
from types import SimpleNamespace

from django.apps import apps
from django.db import connection

from apps.labels.models import Label
from apps.rules.models import Rule
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace

migration = importlib.import_module("apps.rules.migrations.0003_update_old_preset_rules")


def _definition(label_id: object, conditions: list[tuple[str, str, str]]) -> dict[str, object]:
    return {
        "version": 1,
        "match": "any",
        "conditions": [{"field": f, "op": o, "value": v} for f, o, v in conditions],
        "actions": [{"type": "add_label", "label_id": str(label_id), "person_id": None}],
    }


def _words(*words: str) -> list[tuple[str, str, str]]:
    return [("text", "contains_word", w) for w in words]


OLD_CONTRACTEN = [
    ("document_type", "is", "contract"),
    *_words("overeenkomst", "algemene voorwaarden"),
]
NEW_CONTRACTEN = [("document_type", "is", "contract")]
OLD_TAX_HOME = _words("belastingdienst", "toeslagen", "aanslag")
NEW_TAX_HOME = _words("belastingdienst", "toeslagen")
OLD_TAX_BUSINESS = _words("belastingdienst", "aanslag", "naheffing", "aangifte")
NEW_TAX_BUSINESS = _words("belastingdienst", "naheffing", "aangifte")


def _set(workspace: Workspace, key: str, conditions: list[tuple[str, str, str]]) -> object:
    with workspace_context(workspace.id):
        label = Label.objects.get(workspace=workspace, system_key=key)
        Rule.objects.filter(workspace=workspace, system_key=key).update(
            definition=_definition(label.id, conditions)
        )
        return label.id


def _stored(workspace: Workspace, key: str) -> object:
    with workspace_context(workspace.id):
        return Rule.objects.get(workspace=workspace, system_key=key).definition


def _run() -> None:
    migration.update_old_preset_rules(apps, SimpleNamespace(connection=connection))


def test_unedited_old_presets_get_the_new_definition(ws_a: Workspace, ws_b: Workspace) -> None:
    home_tax = _set(ws_a, "belastingdienst", OLD_TAX_HOME)
    business_tax = _set(ws_b, "belastingdienst", OLD_TAX_BUSINESS)
    contracts = _set(ws_b, "contracten", OLD_CONTRACTEN)

    _run()

    assert _stored(ws_a, "belastingdienst") == _definition(home_tax, NEW_TAX_HOME)
    assert _stored(ws_b, "belastingdienst") == _definition(business_tax, NEW_TAX_BUSINESS)
    assert _stored(ws_b, "contracten") == _definition(contracts, NEW_CONTRACTEN)


def test_edited_preset_rules_are_left_alone(ws_a: Workspace, ws_b: Workspace) -> None:
    home_tax = _set(ws_a, "belastingdienst", [*OLD_TAX_HOME, ("text", "contains_word", "fiscus")])
    contracts = _set(ws_b, "contracten", OLD_CONTRACTEN[:2])
    business_tax = _set(ws_b, "belastingdienst", OLD_TAX_BUSINESS)
    edited = {**_definition(business_tax, OLD_TAX_BUSINESS), "match": "all"}
    with workspace_context(ws_b.id):
        Rule.objects.filter(workspace=ws_b, system_key="belastingdienst").update(definition=edited)

    _run()

    assert _stored(ws_a, "belastingdienst") == _definition(
        home_tax, [*OLD_TAX_HOME, ("text", "contains_word", "fiscus")]
    )
    assert _stored(ws_b, "contracten") == _definition(contracts, OLD_CONTRACTEN[:2])
    assert _stored(ws_b, "belastingdienst") == edited


def test_current_presets_stay_the_same(ws_a: Workspace, ws_b: Workspace) -> None:
    before = [_stored(ws_a, "belastingdienst"), _stored(ws_b, "contracten")]

    _run()

    assert [_stored(ws_a, "belastingdienst"), _stored(ws_b, "contracten")] == before


def test_old_preset_pointing_at_another_label_is_left_alone(ws_b: Workspace) -> None:
    _set(ws_b, "contracten", OLD_CONTRACTEN)
    with workspace_context(ws_b.id):
        other = Label.objects.get(workspace=ws_b, system_key="inkoop")
        moved = _definition(other.id, OLD_CONTRACTEN)
        Rule.objects.filter(workspace=ws_b, system_key="contracten").update(definition=moved)

    _run()

    assert _stored(ws_b, "contracten") == moved
