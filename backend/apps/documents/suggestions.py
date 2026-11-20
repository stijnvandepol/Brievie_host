"""Proposals to add a label or an addressee to a document, one row per possible change.

A `dismissed` row is the document's memory of "no" (design 8): nothing automatic
proposes that change on the document again. Adding it by hand is always possible.

Callers hold the workspace's RLS context; the document is locked by whoever changes it.
"""

from collections.abc import Collection
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.db.models import QuerySet

from apps.common.privacy import mask_bsns
from apps.documents.models import Document, DocumentAddressee, DocumentLabel, DocumentSuggestion
from apps.labels.models import Label
from apps.people.models import Person
from apps.rules.models import Rule

Action = DocumentSuggestion.Action
Origin = DocumentSuggestion.Origin
State = DocumentSuggestion.State

# Higher wins when two origins propose the same change.
_STRENGTH = {Origin.NONE: 0, Origin.AI: 1, Origin.NAME_MATCH: 2, Origin.RULE: 3}


def propose(
    document: Document,
    *,
    action: str,
    label: Label | None = None,
    person: Person | None = None,
    origin: str,
    quote: str = "",
    confidence: Decimal,
    rule_name: str = "",
    rule: Rule | None = None,
    preselected: bool = False,
) -> DocumentSuggestion | None:
    """Record a proposal; None when the change is already on the document or was dismissed.

    A second proposal of the same change strengthens the open row: the strongest origin
    (with its quote and rule name), the highest confidence, preselected when either was,
    and the highest `ai_confidence` of the AI's own picks.

    The caller holds the document's row lock (`select_for_update`, as the pipeline's
    `_lock_processing` does) for its whole transaction: two runs on the same document
    could otherwise both create the row.
    """
    target_id = _target_id(action, label, person)
    ai_confidence = confidence if origin == Origin.AI else Decimal("0")
    with transaction.atomic():
        if _is_on_document(document, action, target_id):
            return None
        row = _rows(document, action, target_id).select_for_update().first()
        if row is None:
            return DocumentSuggestion.objects.create(
                workspace_id=document.workspace_id,
                document=document,
                action=action,
                label=label,
                person=person,
                origin=origin,
                rule=rule,
                rule_name=rule_name,
                quote=mask_bsns(quote.strip()),
                confidence=confidence,
                preselected=preselected,
                ai_confidence=ai_confidence,
            )
        if row.state == State.DISMISSED:
            return None
        if row.state == State.ACCEPTED:
            # Accepted once, but the change is gone again: a fresh proposal, so
            # nothing of the old one carries over.
            row.state = State.OPEN
            row.origin = origin
            row.rule = rule
            row.rule_name = rule_name
            row.quote = mask_bsns(quote.strip())
            row.confidence = confidence
            row.preselected = preselected
            row.ai_confidence = ai_confidence
            row.save()
            return row
        if _STRENGTH[Origin(origin)] > _STRENGTH[Origin(row.origin)]:
            row.origin = origin
            row.rule = rule
            row.rule_name = rule_name
            row.quote = mask_bsns(quote.strip())
        row.confidence = max(row.confidence, confidence)
        row.preselected = row.preselected or preselected
        row.ai_confidence = max(row.ai_confidence, ai_confidence)
        row.save()
        return row


def open_row(document: Document, action: str, target_id: UUID) -> DocumentSuggestion | None:
    """The open proposal of this change on the document, if there is one."""
    return _rows(document, action, target_id).filter(state=State.OPEN).first()


def is_dismissed(document: Document, action: str, target_id: UUID) -> bool:
    return _rows(document, action, target_id).filter(state=State.DISMISSED).exists()


def mark_dismissed(document: Document, action: str, target_id: UUID) -> None:
    """Remember that the user does not want this change on this document."""
    with transaction.atomic():
        if _rows(document, action, target_id).update(state=State.DISMISSED):
            return
        DocumentSuggestion.objects.create(
            workspace_id=document.workspace_id,
            document=document,
            action=action,
            **_target_fields(action, target_id),
            state=State.DISMISSED,
        )


def mark_accepted(document: Document, action: str, target_id: UUID) -> None:
    """The change is on the document by the user's choice; only an existing row changes."""
    _rows(document, action, target_id).update(state=State.ACCEPTED)


def clear_open(document: Document, origins: Collection[str]) -> None:
    """Remove the open proposals of these origins (before they are made again).

    The caller holds the document's row lock, as for `propose`.
    """
    DocumentSuggestion.objects.filter(
        document=document, state=State.OPEN, origin__in=origins
    ).delete()


def target_id_of(suggestion: DocumentSuggestion) -> UUID:
    target = suggestion.label_id if suggestion.action == Action.ADD_LABEL else suggestion.person_id
    if target is None:
        raise ValueError("A suggestion always names its label or person.")
    return target


def _rows(document: Document, action: str, target_id: UUID) -> QuerySet[DocumentSuggestion]:
    return DocumentSuggestion.objects.filter(
        document=document, action=action, **_target_fields(action, target_id)
    )


def _target_fields(action: str, target_id: UUID) -> dict[str, UUID]:
    if action == Action.ADD_LABEL:
        return {"label_id": target_id}
    if action == Action.ADD_ADDRESSEE:
        return {"person_id": target_id}
    raise ValueError(f"Unknown suggestion action {action!r}.")


def _target_id(action: str, label: Label | None, person: Person | None) -> UUID:
    if action == Action.ADD_LABEL and label is not None and person is None:
        return label.id
    if action == Action.ADD_ADDRESSEE and person is not None and label is None:
        return person.id
    raise ValueError("A suggestion names exactly the label or the person its action needs.")


def _is_on_document(document: Document, action: str, target_id: UUID) -> bool:
    if action == Action.ADD_LABEL:
        return DocumentLabel.objects.filter(document=document, label_id=target_id).exists()
    return DocumentAddressee.objects.filter(document=document, person_id=target_id).exists()
