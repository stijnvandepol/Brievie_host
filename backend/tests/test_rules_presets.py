import uuid
from pathlib import Path

import pytest
from django.test import Client

from apps.labels.models import Label
from apps.labels.presets import PRESETS, apply_preset
from apps.rules.definition import to_spec
from apps.rules.engine import Condition, DocumentFacts, RuleSpec, evaluate
from apps.rules.models import Rule
from apps.rules.presets import preset_definition
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, make_person
from tests.rules_support import add_addressee, add_label, make_rule, text_word

LETTERS = Path(__file__).parent / "fixtures" / "sample_letters"


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def rules(workspace: Workspace) -> list[Rule]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return list(Rule.objects.filter(workspace=workspace).order_by("position"))


def preset_label(workspace: Workspace, key: str) -> Label:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Label.objects.get(workspace=workspace, system_key=key)


def test_apply_preset_creates_one_rule_per_preset_label_once(ws_a: Workspace) -> None:
    created = rules(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        apply_preset(ws_a)
    again = rules(ws_a)

    keys = [preset.key for preset in PRESETS[ws_a.kind]]
    assert [rule.system_key for rule in created] == keys
    assert [rule.id for rule in again] == [rule.id for rule in created]
    assert [rule.position for rule in created] == list(range(1, len(keys) + 1))
    first = created[0]
    assert (first.name, first.mode, first.active, first.stop) == (
        "Label Belastingdienst",
        "propose",
        True,
        False,
    )
    label = preset_label(ws_a, "belastingdienst")
    assert first.definition == {
        "version": 1,
        "match": "any",
        "conditions": [
            {"field": "text", "op": "contains_word", "value": word}
            for word in ("belastingdienst", "toeslagen")
        ],
        "actions": [{"type": "add_label", "label_id": str(label.id), "person_id": None}],
    }


def test_deleted_preset_rule_comes_back_with_the_start_set(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        Rule.objects.filter(system_key="zorg").delete()
        own = make_rule(ws_a, text_word("APK"), actions=[add_label(preset_label(ws_a, "auto"))])
        renamed = Label.objects.get(system_key="auto")
        renamed.name = "Onze auto"
        renamed.save()

        apply_preset(ws_a)

        back = Rule.objects.get(system_key="zorg")
    assert back.position == own.position + 1
    assert back.name == "Label Zorg"
    assert len(rules(ws_a)) == len(PRESETS[ws_a.kind]) + 1


def test_existing_workspace_gets_rules_only_for_its_preset_labels(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        Rule.objects.all().delete()
        Label.objects.get(system_key="bank").delete()
        Label.objects.create(workspace=ws_a, name="Bank")

        apply_preset(ws_a)

    keys = {rule.system_key for rule in rules(ws_a)}
    assert "bank" not in keys
    assert len(keys) == len(PRESETS[ws_a.kind]) - 1


def test_deleting_a_preset_label_deletes_its_rule(client_a: Client, ws_a: Workspace) -> None:
    zorg = preset_label(ws_a, "zorg")

    r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/labels/{zorg.id}")

    assert r.status_code == 204
    assert "zorg" not in {rule.system_key for rule in rules(ws_a)}


def test_label_named_by_another_rule_is_409_naming_the_rule(
    client_a: Client, ws_a: Workspace
) -> None:
    zorg = preset_label(ws_a, "zorg")
    make_rule(ws_a, text_word("huisarts"), actions=[add_label(zorg)], name="Huisarts")

    r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/labels/{zorg.id}")

    assert r.status_code == 409
    assert r.json() == {
        "code": "label_in_use",
        "message": "De regel 'Huisarts' gebruikt dit label. Pas die regel eerst aan.",
    }
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Label.objects.filter(id=zorg.id).exists()
        assert Rule.objects.filter(system_key="zorg").exists()


def test_label_in_a_condition_is_409(client_a: Client, ws_a: Workspace) -> None:
    bank = preset_label(ws_a, "bank")
    emma = make_person(ws_a, "Emma")
    make_rule(ws_a, Condition("label", "has", str(bank.id)), actions=[add_addressee(emma.id)])

    r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/labels/{bank.id}")

    assert r.status_code == 409
    assert r.json()["code"] == "label_in_use"


def test_edited_preset_rule_with_more_actions_keeps_its_label(
    client_a: Client, ws_a: Workspace
) -> None:
    zorg = preset_label(ws_a, "zorg")
    emma = make_person(ws_a, "Emma")
    with workspace_context(ws_a.id, actor=SYSTEM):
        rule = Rule.objects.get(system_key="zorg")
        rule.definition = {
            **rule.definition,
            "actions": [
                *rule.definition["actions"],
                {"type": "add_addressee", "label_id": None, "person_id": str(emma.id)},
            ],
        }
        rule.save()

    r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/labels/{zorg.id}")

    assert r.status_code == 409
    assert "Label Zorg" in r.json()["message"]


def test_person_named_by_a_rule_is_409(client_a: Client, ws_a: Workspace) -> None:
    emma = make_person(ws_a, "Emma")
    make_rule(ws_a, text_word("Emma"), actions=[add_addressee(emma.id)], name="Op naam van Emma")

    r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/persons/{emma.id}")

    assert r.status_code == 409
    assert r.json() == {
        "code": "person_in_use",
        "message": "De regel 'Op naam van Emma' gebruikt deze persoon. Pas die regel eerst aan.",
    }


# Per sample letter: the sender and type the review would hold, and the label keys the
# preset rules must propose in Thuis and in Zakelijk (None: not asserted).
SAMPLES: list[tuple[str, str, str, set[str], set[str] | None]] = [
    ("webshop_invoice", "Huisraad Online", "invoice", set(), {"inkoop"}),
    (
        "belastingdienst_assessment",
        "Belastingdienst",
        "assessment",
        {"belastingdienst"},
        {"belastingdienst"},
    ),
    ("health_insurer", "ZorgSamen Zorgverzekeraar", "information", {"zorg"}, None),
    ("apk_reminder", "Garage Van Dijk", "reminder", {"auto"}, None),
    ("bank_letter", "Rabobank", "information", {"bank"}, {"bank"}),
    ("municipal_tax_bill", "Gemeente Utrecht", "assessment", {"gemeente"}, {"kvk"}),
    ("payslip", "Bakkerij De Korenaar", "payslip", {"inkomen"}, {"personeel"}),
    ("energy_bill", "Vonk Energie", "invoice", {"energie"}, {"inkoop", "huisvesting"}),
    # Terms in the footer of an invoice make it no contract.
    ("supplier_invoice", "Groothandel Kantoorplus", "invoice", set(), {"inkoop"}),
    ("supplier_invoice", "Groothandel Kantoorplus", "contract", set(), {"contracten"}),
]


def preset_specs(kind: str) -> tuple[list[RuleSpec], dict[uuid.UUID, str]]:
    """The preset rules of a workspace kind as the engine gets them, and label id -> key."""
    specs: list[RuleSpec] = []
    keys: dict[uuid.UUID, str] = {}
    for position, preset in enumerate(PRESETS[kind], start=1):
        label_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{kind}/{preset.key}")
        keys[label_id] = preset.key
        rule = Rule(
            position=position,
            name=f"Label {preset.name}",
            definition=preset_definition(preset, label_id),
        )
        spec = to_spec(rule)
        assert spec is not None, preset.key
        specs.append(spec)
    return specs, keys


@pytest.mark.parametrize(("fixture", "sender", "document_type", "home", "business"), SAMPLES)
def test_presets_on_sample_letters(
    fixture: str, sender: str, document_type: str, home: set[str], business: set[str] | None
) -> None:
    # Review Focus 5
    facts = DocumentFacts(
        text=(LETTERS / f"{fixture}.txt").read_text(encoding="utf-8"),
        sender=sender,
        document_type=document_type,
        source="scan",
        label_ids=frozenset(),
        addressee_ids=frozenset(),
    )
    for kind, expected in ((Workspace.Kind.HOME, home), (Workspace.Kind.BUSINESS, business)):
        if expected is None:
            continue
        specs, keys = preset_specs(kind)
        planned = evaluate(facts, specs).planned
        assert {keys[p.target_id] for p in planned if p.target_id is not None} == expected, kind
        assert all(p.target_id is not None for p in planned)
        assert all(p.mode == "propose" for p in planned)


def test_every_sample_letter_has_a_letterhead() -> None:
    letters = sorted(LETTERS.glob("*.txt"))
    assert len(letters) >= 9
    for letter in letters:
        text = letter.read_text(encoding="utf-8")
        for part in ("Telefoon", "www.", "KvK", "BTW", "IBAN"):
            assert part.lower() in text.lower(), (letter.name, part)
