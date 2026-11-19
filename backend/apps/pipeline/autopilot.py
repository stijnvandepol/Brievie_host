"""The autopilot's part of organising: apply the AI's strong picks (design (a)).

Only proposals the pipeline itself made are applied: an AI pick from `AUTO_APPLY_FROM`,
a name match from `PRESELECT_FROM`. A rule's proposal is never applied here, whatever
else proposed the same change: a rule row means the letter's own text can have steered it.

The caller holds the document's row lock and the workspace's RLS context.
"""

from decimal import Decimal

from django.db.models import Case, IntegerField, Value, When

from apps.documents import organise, suggestions
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentSuggestion,
)
from apps.documents.organise import ADDRESSEES_MAX, LABELS_MAX
from apps.people.matching import PRESELECT_FROM

AUTO_APPLY_FROM = Decimal("0.800")

_ADD_LABEL = DocumentSuggestion.Action.ADD_LABEL
_ADD_ADDRESSEE = DocumentSuggestion.Action.ADD_ADDRESSEE


def apply_open_proposals(document: Document) -> int:
    """Apply the open proposals that qualify; how many were applied.

    Addressees first, then labels, each by confidence (highest first) and id.
    A target that is gone or an archived person is skipped, and so is anything that
    would take the document over its limit.
    """
    room: dict[str, int] = {
        _ADD_ADDRESSEE: ADDRESSEES_MAX
        - DocumentAddressee.objects.filter(document=document).count(),
        _ADD_LABEL: LABELS_MAX - DocumentLabel.objects.filter(document=document).count(),
    }
    rows = (
        DocumentSuggestion.objects.filter(document=document, state=DocumentSuggestion.State.OPEN)
        .select_related("label", "person")
        .annotate(
            order=Case(
                When(action=_ADD_ADDRESSEE, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by("order", "-confidence", "id")
    )
    applied = 0
    for row in rows:
        event_origin = _event_origin(row)
        if event_origin is None or room[row.action] <= 0:
            continue
        if row.action == _ADD_LABEL:
            if row.label is None:
                continue
            added = organise.add_label(document, row.label, actor=None, origin=event_origin)
        else:
            if row.person is None or row.person.archived_at is not None:
                continue
            added = organise.add_addressee(document, row.person, actor=None, origin=event_origin)
        if added:
            suggestions.mark_accepted(document, row.action, suggestions.target_id_of(row))
            room[row.action] -= 1
            applied += 1
    return applied


def _event_origin(row: DocumentSuggestion) -> str | None:
    """The history origin when this proposal may be applied on its own, else None."""
    if row.origin == DocumentSuggestion.Origin.AI and row.confidence >= AUTO_APPLY_FROM:
        return DocumentEvent.Origin.AI
    if (
        row.origin == DocumentSuggestion.Origin.NAME_MATCH
        and row.action == _ADD_ADDRESSEE
        and row.confidence >= PRESELECT_FROM
    ):
        return DocumentEvent.Origin.NAME_MATCH
    return None
