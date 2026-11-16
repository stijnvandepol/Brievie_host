"""The only code that adds or removes a document's addressees and labels.

Every change writes an event.

Callers hold the document locked (`select_for_update`) and the workspace's RLS context.
"""

from collections.abc import Sequence
from uuid import UUID

from django.db import transaction
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.documents.models import Document, DocumentAddressee, DocumentEvent, DocumentLabel
from apps.labels.models import Label
from apps.labels.services import label_not_found
from apps.people.models import Person

ADDRESSEES_MAX = 10
LABELS_MAX = 30


def set_addressees(
    *,
    document: Document,
    person_ids: Sequence[UUID],
    actor: User | None,
    origin: str = DocumentEvent.Origin.USER,
) -> None:
    """Make the document's addressees exactly `person_ids` (repeats count once).

    A person who is already on the document stays, even when archived since; an
    archived person cannot be newly added.
    """
    wanted = list(dict.fromkeys(person_ids))
    if len(wanted) > ADDRESSEES_MAX:
        raise ApiError(
            422,
            "validation_error",
            _("Een document staat op naam van hoogstens %(max)d personen.")
            % {"max": ADDRESSEES_MAX},
        )
    with transaction.atomic():
        persons = {
            p.id: p
            for p in Person.objects.filter(workspace_id=document.workspace_id, id__in=wanted)
        }
        if len(persons) != len(wanted):
            raise ApiError(404, "person_not_found", _("We kunnen deze persoon niet vinden."))
        links = list(
            DocumentAddressee.objects.filter(document=document)
            .select_related("person")
            .order_by("created_at", "id")
        )
        current = {link.person_id for link in links}
        if any(persons[i].archived_at is not None for i in wanted if i not in current):
            raise ApiError(
                422,
                "validation_error",
                _("Een gearchiveerde persoon kun je niet aan een document toevoegen."),
            )
        for link in links:
            if link.person_id not in persons:
                link.delete()
                _write_event(
                    document,
                    DocumentEvent.Kind.ADDRESSEE_REMOVED,
                    actor,
                    origin,
                    person=link.person,
                )
        for person_id in wanted:
            add_addressee(document, persons[person_id], actor=actor, origin=origin)


def add_addressee(document: Document, person: Person, *, actor: User | None, origin: str) -> bool:
    """Put `person` on the document; False when they were already there."""
    with transaction.atomic():
        if DocumentAddressee.objects.filter(document=document, person=person).exists():
            return False
        DocumentAddressee.objects.create(
            workspace_id=document.workspace_id, document=document, person=person
        )
        _write_event(document, DocumentEvent.Kind.ADDRESSEE_ADDED, actor, origin, person=person)
        return True


def set_labels(
    *,
    document: Document,
    label_ids: Sequence[UUID],
    actor: User | None,
    origin: str = DocumentEvent.Origin.USER,
) -> None:
    """Make the document's labels exactly `label_ids` (repeats count once)."""
    wanted = list(dict.fromkeys(label_ids))
    if len(wanted) > LABELS_MAX:
        raise ApiError(
            422,
            "validation_error",
            _("Een document heeft hoogstens %(max)d labels.") % {"max": LABELS_MAX},
        )
    with transaction.atomic():
        labels = {
            label.id: label
            for label in Label.objects.filter(workspace_id=document.workspace_id, id__in=wanted)
        }
        if len(labels) != len(wanted):
            raise label_not_found()
        links = DocumentLabel.objects.filter(document=document).select_related("label")
        for link in links.order_by("created_at", "id"):
            if link.label_id not in labels:
                link.delete()
                _write_event(
                    document, DocumentEvent.Kind.LABEL_REMOVED, actor, origin, label=link.label
                )
        for label_id in wanted:
            add_label(document, labels[label_id], actor=actor, origin=origin)


def add_label(document: Document, label: Label, *, actor: User | None, origin: str) -> bool:
    """Put `label` on the document; False when it was already there."""
    with transaction.atomic():
        if DocumentLabel.objects.filter(document=document, label=label).exists():
            return False
        DocumentLabel.objects.create(
            workspace_id=document.workspace_id, document=document, label=label
        )
        _write_event(document, DocumentEvent.Kind.LABEL_ADDED, actor, origin, label=label)
        return True


def record_confirmed(document: Document, *, actor: User) -> None:
    """The history line for a confirmed review."""
    DocumentEvent.objects.create(
        workspace_id=document.workspace_id,
        document=document,
        kind=DocumentEvent.Kind.CONFIRMED,
        origin=DocumentEvent.Origin.USER,
        actor=actor,
    )


def _write_event(
    document: Document,
    kind: str,
    actor: User | None,
    origin: str,
    *,
    person: Person | None = None,
    label: Label | None = None,
) -> None:
    DocumentEvent.objects.create(
        workspace_id=document.workspace_id,
        document=document,
        kind=kind,
        origin=origin,
        actor=actor,
        person=person,
        person_name=person.display_name if person is not None else "",
        label=label,
        label_name=label.name if label is not None else "",
    )
