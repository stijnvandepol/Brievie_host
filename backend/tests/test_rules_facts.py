from decimal import Decimal

from django.utils import timezone
from pytest_django import DjangoAssertNumQueries

from apps.documents import organise, suggestions
from apps.documents.models import Document, Extraction
from apps.labels.models import Label
from apps.rules import services
from apps.rules.facts import facts_for, facts_for_many
from apps.rules.schemas import RuleIn
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import make_person
from tests.rules_support import make_document


def _extraction(document: Document, field: str, value: str, confidence: str) -> None:
    Extraction.objects.create(
        workspace_id=document.workspace_id,
        document=document,
        field=field,
        value=value,
        confidence=Decimal(confidence),
    )


def _documents(workspace: Workspace) -> list[Document]:
    """Documents with every kind of fact: proposals, a confirmed one, labels, addressees,
    open and dismissed suggestions."""
    anna = make_person(workspace, "Anna")
    bram = make_person(workspace, "Bram")
    plain = make_document(workspace, "Een gewone brief.")
    proposed = make_document(workspace, "Een brief van de bank.")
    confirmed = make_document(workspace, "Een bevestigde brief.", status="done")
    organised = make_document(workspace, "Uw APK verloopt.")
    with workspace_context(workspace.id):
        zorg, bank, auto = (
            Label.objects.get(workspace=workspace, system_key=key)
            for key in ("zorg", "bank", "auto")
        )
        _extraction(proposed, "sender", "Zonder vertrouwen", "0")
        _extraction(proposed, "sender", "ING Bank", "0.8")
        _extraction(proposed, "sender", "Rabobank", "0.6")
        _extraction(proposed, "document_type", "invoice", "0")
        _extraction(confirmed, "sender", "Voorstel", "0.9")
        Document.objects.filter(id=confirmed.id).update(
            sender="CAK", document_type="assessment", confirmed_at=timezone.now()
        )
        _extraction(organised, "document_type", "letter", "0.4")
        _extraction(organised, "document_type", "notice", "0.7")
        locked = Document.objects.select_for_update().get(id=organised.id)
        organise.add_label(locked, zorg, actor=None, origin="user")
        organise.add_addressee(locked, anna, actor=None, origin="user")
        for action, label, person in (
            ("add_label", bank, None),
            ("add_label", auto, None),
            ("add_addressee", None, bram),
        ):
            suggestions.propose(
                locked,
                action=action,
                label=label,
                person=person,
                origin="ai",
                confidence=Decimal("0.5"),
            )
        suggestions.mark_dismissed(locked, "add_label", auto.id)
        return list(
            Document.objects.filter(id__in=[plain.id, proposed.id, confirmed.id, organised.id])
        )


def test_bulk_facts_equal_the_single_document_facts(ws_a: Workspace) -> None:
    documents = _documents(ws_a)

    with workspace_context(ws_a.id):
        single = {document.id: facts_for(document) for document in documents}
        bulk = facts_for_many(documents)

    assert bulk == single
    by_text = {facts.text: facts for facts in bulk.values()}
    assert by_text["Een brief van de bank."].sender == "ING Bank"
    assert by_text["Een bevestigde brief."].sender == "CAK"
    assert by_text["Uw APK verloopt."].document_type == "notice"
    assert by_text["Uw APK verloopt."].dismissed_label_ids


def test_testing_a_rule_over_50_documents_takes_a_fixed_number_of_queries(
    ws_a: Workspace, django_assert_max_num_queries: DjangoAssertNumQueries
) -> None:
    for _ in range(50):
        make_document(ws_a, "Uw APK verloopt.")
    with workspace_context(ws_a.id):
        auto = Label.objects.get(workspace=ws_a, system_key="auto")
    data = RuleIn.model_validate(
        {
            "name": "APK",
            "conditions": [{"field": "text", "op": "contains_word", "value": "APK"}],
            "actions": [{"type": "add_label", "label_id": str(auto.id)}],
        }
    )

    # Validation 2, documents 2, facts 4, the shown documents' links 3, and their headline
    # proposals (proposed sender, type, date) 1: without that prefetch each row reads its own.
    with workspace_context(ws_a.id), django_assert_max_num_queries(12):
        out = services.test_rule(workspace=ws_a, data=data)

    assert (out.checked_count, out.matched_count) == (50, 50)
