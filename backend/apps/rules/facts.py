"""What the rules engine sees of a document, read from the database (design 9.4).

Callers hold the workspace's RLS context.
"""

from uuid import UUID

from django.db.models import QuerySet

from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentLabel,
    DocumentSuggestion,
    Extraction,
)
from apps.rules.definition import to_spec
from apps.rules.engine import DocumentFacts, RuleSpec
from apps.rules.models import Rule


def facts_for(document: Document) -> DocumentFacts:
    """The document's facts: its masked text, sender and type, labels and addressees.

    Before confirmation the sender and type are the AI's proposals (a sender only with a
    confidence above 0); once confirmed, the confirmed values. Open suggestions are what
    is hoped for.
    """
    if document.confirmed_at is not None:
        sender, document_type = document.sender, document.document_type
    else:
        sender = _proposed(document, Extraction.Field.SENDER, above_zero=True)
        document_type = _proposed(document, Extraction.Field.DOCUMENT_TYPE, above_zero=False)
    open_rows = DocumentSuggestion.objects.filter(
        document=document, state=DocumentSuggestion.State.OPEN
    )
    return DocumentFacts(
        text=document.ocr_text,
        sender=sender,
        document_type=document_type,
        source=document.source,
        label_ids=frozenset(
            DocumentLabel.objects.filter(document=document).values_list("label_id", flat=True)
        ),
        addressee_ids=frozenset(
            DocumentAddressee.objects.filter(document=document).values_list("person_id", flat=True)
        ),
        hoped_label_ids=_ids(open_rows.filter(label__isnull=False), "label_id"),
        hoped_addressee_ids=_ids(open_rows.filter(person__isnull=False), "person_id"),
    )


def active_specs(workspace_id: UUID) -> list[RuleSpec]:
    """The workspace's active rules in `position` order, as `evaluate` expects them.

    A rule whose definition cannot be read is left out.
    """
    rules = Rule.objects.filter(workspace_id=workspace_id, active=True).order_by("position", "id")
    return [spec for spec in (to_spec(rule) for rule in rules) if spec is not None]


def _proposed(document: Document, field: str, *, above_zero: bool) -> str:
    rows = Extraction.objects.filter(document=document, field=field, group=0)
    if above_zero:
        rows = rows.filter(confidence__gt=0)
    value = rows.order_by("-confidence", "created_at").values_list("value", flat=True).first()
    return value or ""


def _ids(rows: QuerySet[DocumentSuggestion], column: str) -> frozenset[UUID]:
    return frozenset(rows.values_list(column, flat=True))
