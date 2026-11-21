"""The only code that adds or removes a document's addressees and labels.

Every change writes an event. A hand edit (`set_addressees`, `set_labels`) also updates
the document's suggestions: what the user removed is dismissed, what they chose accepted.

Callers hold the document locked (`select_for_update`) and the workspace's RLS context.
"""

from collections.abc import Collection, Sequence
from uuid import UUID

from django.db import transaction
from django.db.models import Case, Exists, OuterRef, Q, QuerySet, Value, When
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.documents import suggestions, visibility
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentSuggestion,
)
from apps.labels.models import Label
from apps.labels.services import label_not_found
from apps.people.models import Person
from apps.rules.models import Rule

ADDRESSEES_MAX = 10
LABELS_MAX = 30

# Changes the system made on its own (a rule, the autopilot): the user may undo them.
UNDOABLE_ORIGINS = (
    DocumentEvent.Origin.RULE,
    DocumentEvent.Origin.AI,
    DocumentEvent.Origin.NAME_MATCH,
    DocumentEvent.Origin.RULE_AUTOPILOT,
)
# Origins whose links are marked `automatic`: the autopilot put them there. Such a change
# is undoable only while its link is still `automatic` (a person's confirm takes it over).
# A rule's label proposal the autopilot applied has its own origin, `rule_autopilot`.
AUTOMATIC_ORIGINS = (
    DocumentEvent.Origin.AI,
    DocumentEvent.Origin.NAME_MATCH,
    DocumentEvent.Origin.RULE_AUTOPILOT,
)

_ADD_LABEL = DocumentSuggestion.Action.ADD_LABEL
_ADD_ADDRESSEE = DocumentSuggestion.Action.ADD_ADDRESSEE


def set_addressees(
    *,
    document: Document,
    person_ids: Sequence[UUID],
    actor: User | None,
    origin: str = DocumentEvent.Origin.USER,
    suggested_ids: Collection[UUID] = (),
) -> None:
    """Make the document's addressees exactly `person_ids` (repeats count once).

    A person who is already on the document stays, even when archived since; an
    archived person cannot be newly added. Persons in `suggested_ids` are added with
    origin `suggestion` (the user took a proposal).
    """
    _require_scope(document)
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
                suggestions.mark_dismissed(document, _ADD_ADDRESSEE, link.person_id)
        if any(link.automatic and link.person_id not in persons for link in links):
            sync_automatic([document.id])
        for person_id in wanted:
            add_addressee(
                document,
                persons[person_id],
                actor=actor,
                origin=_origin_for(person_id, origin, suggested_ids),
            )
            suggestions.mark_accepted(document, _ADD_ADDRESSEE, person_id)


def add_addressee(
    document: Document,
    person: Person,
    *,
    actor: User | None,
    origin: str,
    rule: Rule | None = None,
) -> bool:
    """Put `person` on the document; False when they were already there.

    `rule` is the rule that made the change (origin `rule`).
    """
    _require_scope(document)
    with transaction.atomic():
        if DocumentAddressee.objects.filter(document=document, person=person).exists():
            return False
        DocumentAddressee.objects.create(
            workspace_id=document.workspace_id,
            document=document,
            person=person,
            automatic=origin in AUTOMATIC_ORIGINS,
        )
        if origin in AUTOMATIC_ORIGINS:
            sync_automatic([document.id])
        _write_event(
            document, DocumentEvent.Kind.ADDRESSEE_ADDED, actor, origin, person=person, rule=rule
        )
        return True


def set_labels(
    *,
    document: Document,
    label_ids: Sequence[UUID],
    actor: User | None,
    origin: str = DocumentEvent.Origin.USER,
    suggested_ids: Collection[UUID] = (),
) -> None:
    """Make the document's labels exactly `label_ids` (repeats count once).

    Labels in `suggested_ids` are added with origin `suggestion`.
    """
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
        removed_automatic = False
        for link in links.order_by("created_at", "id"):
            if link.label_id not in labels:
                link.delete()
                removed_automatic = removed_automatic or link.automatic
                _write_event(
                    document, DocumentEvent.Kind.LABEL_REMOVED, actor, origin, label=link.label
                )
                suggestions.mark_dismissed(document, _ADD_LABEL, link.label_id)
        if removed_automatic:
            sync_automatic([document.id])
        for label_id in wanted:
            add_label(
                document,
                labels[label_id],
                actor=actor,
                origin=_origin_for(label_id, origin, suggested_ids),
            )
            suggestions.mark_accepted(document, _ADD_LABEL, label_id)


def add_label(
    document: Document,
    label: Label,
    *,
    actor: User | None,
    origin: str,
    rule: Rule | None = None,
) -> bool:
    """Put `label` on the document; False when it was already there.

    `rule` is the rule that made the change (origin `rule` or `rule_autopilot`).
    """
    with transaction.atomic():
        if DocumentLabel.objects.filter(document=document, label=label).exists():
            return False
        DocumentLabel.objects.create(
            workspace_id=document.workspace_id,
            document=document,
            label=label,
            automatic=origin in AUTOMATIC_ORIGINS,
        )
        if origin in AUTOMATIC_ORIGINS:
            sync_automatic([document.id])
        _write_event(
            document, DocumentEvent.Kind.LABEL_ADDED, actor, origin, label=label, rule=rule
        )
        return True


def record_label_deleted(label: Label, *, actor_id: UUID | None) -> None:
    """Before a label is deleted: a `label_removed` line, with the label's name, in the
    history of every document that has it; then its links go, and the automatic flag of
    the letters that had it as an automatic link follows.

    The documents are not locked: nothing else is changed on them, and a rule that runs
    holds the label's row, which the caller has locked. One INSERT for all of them; the
    rows are built from the label's own links in its workspace, so the same-workspace
    check of `save()` (which a bulk insert skips) holds by construction.
    """
    with transaction.atomic():
        DocumentEvent.objects.bulk_create(
            [
                DocumentEvent(
                    workspace_id=label.workspace_id,
                    document_id=document_id,
                    kind=DocumentEvent.Kind.LABEL_REMOVED,
                    origin=DocumentEvent.Origin.USER,
                    actor_id=actor_id,
                    label=label,
                    label_name=label.name,
                )
                for document_id in DocumentLabel.objects.filter(
                    label=label, workspace_id=label.workspace_id
                )
                .order_by("document_id")
                .values_list("document_id", flat=True)
            ]
        )
        links = DocumentLabel.objects.filter(label=label, workspace_id=label.workspace_id)
        automatic = list(links.filter(automatic=True).values_list("document_id", flat=True))
        links.delete()
        sync_automatic(automatic)


def record_task_event(
    document: Document,
    kind: str,
    actor: User | None,
    origin: str = DocumentEvent.Origin.USER,
    *,
    person_name: str = "",
) -> None:
    """History entry for a change of one of the letter's tasks: ticked off or reopened
    (`task_completed`, `task_reopened`; origin `merge` when a merge completed a twin
    task), given to a member or released (`task_assigned`, `task_unassigned`, with the
    member's name; origin `system` and no actor when the server released it), or dated
    (`task_deadline_set`)."""
    _write_event(document, kind, actor, origin, person_name=person_name)


_UNDOABLE = (DocumentEvent.Kind.LABEL_ADDED, DocumentEvent.Kind.ADDRESSEE_ADDED)


def undo_event(*, document: Document, event_id: UUID, actor: User) -> None:
    """Reverse a change a rule or the autopilot made (design 13), and remember the user's "no".

    Only a `label_added` or `addressee_added` event with origin `rule`, `ai` or
    `name_match` that was not undone yet, while its label or person is still on the
    document. An `ai` or `name_match` change also only while its link is still
    `automatic`: once a person confirmed the organisation, the link is theirs.
    """
    with transaction.atomic():
        event = (
            DocumentEvent.objects.select_for_update().filter(document=document, id=event_id).first()
        )
        if event is None:
            raise ApiError(404, "event_not_found", _("We kunnen deze gebeurtenis niet vinden."))
        if (
            event.origin not in UNDOABLE_ORIGINS
            or event.kind not in _UNDOABLE
            or DocumentEvent.objects.filter(undoes=event).exists()
            or _target_of(event) is None
            or _later_events_for_target(event).exists()
        ):
            raise _not_undoable()
        if event.kind == DocumentEvent.Kind.LABEL_ADDED:
            label_id = event.label_id
            label_link = (
                None
                if label_id is None
                else DocumentLabel.objects.filter(document=document, label_id=label_id)
                .select_related("label")
                .first()
            )
            if label_id is None or label_link is None or _taken_over(event, label_link.automatic):
                raise _not_undoable()
            label_link.delete()
            sync_automatic([document.id])
            _write_event(
                document,
                DocumentEvent.Kind.LABEL_REMOVED,
                actor,
                DocumentEvent.Origin.UNDO,
                label=label_link.label,
                undoes=event,
            )
            suggestions.mark_dismissed(document, _ADD_LABEL, label_id)
            return
        person_id = event.person_id
        person_link = (
            None
            if person_id is None
            else DocumentAddressee.objects.filter(document=document, person_id=person_id)
            .select_related("person")
            .first()
        )
        if person_id is None or person_link is None or _taken_over(event, person_link.automatic):
            raise _not_undoable()
        _require_scope(document)
        person_link.delete()
        sync_automatic([document.id])
        _write_event(
            document,
            DocumentEvent.Kind.ADDRESSEE_REMOVED,
            actor,
            DocumentEvent.Origin.UNDO,
            person=person_link.person,
            undoes=event,
        )
        suggestions.mark_dismissed(document, _ADD_ADDRESSEE, person_id)


def _require_scope(document: Document) -> None:
    """Addressees change only inside `visibility.addressee_scope`, whose end syncs
    `addressee_ids` and the readers (P3): no caller can forget it."""
    if not visibility.in_addressee_scope(document.id):
        raise RuntimeError("addressee change outside addressee_scope")


def _taken_over(event: DocumentEvent, automatic: bool) -> bool:
    """Whether a person confirmed the link an automatic change made: not undoable then."""
    return event.origin in AUTOMATIC_ORIGINS and not automatic


def confirm_automatic_links(document: Document) -> None:
    """A person confirmed the document's labels and addressees: none is automatic any more.

    Only for a person's confirm that sets the organisation; a hand edit leaves the flag.
    """
    now = timezone.now()
    DocumentLabel.objects.filter(document=document, automatic=True).update(
        automatic=False, updated_at=now
    )
    DocumentAddressee.objects.filter(document=document, automatic=True).update(
        automatic=False, updated_at=now
    )
    sync_automatic([document.id])


def sync_automatic(document_ids: Collection[UUID]) -> None:
    """`Document.has_automatic_link` := whether an automatic label or addressee link is on
    the letter. The only writer of the flag; every change of a link's `automatic` or of an
    automatic link's existence calls it, in the same transaction."""
    if not document_ids:
        return
    automatic = Q(
        Exists(DocumentLabel.objects.filter(document_id=OuterRef("pk"), automatic=True))
    ) | Q(Exists(DocumentAddressee.objects.filter(document_id=OuterRef("pk"), automatic=True)))
    Document.objects.filter(id__in=list(document_ids)).update(
        has_automatic_link=Case(When(automatic, then=Value(True)), default=Value(False))
    )


def _target_of(event: DocumentEvent) -> UUID | None:
    return event.label_id if event.kind == DocumentEvent.Kind.LABEL_ADDED else event.person_id


def _later_events_for_target(event: DocumentEvent) -> QuerySet[DocumentEvent]:
    """Events after `event` on its document about the same label or person.

    A rule's change can be undone only while nothing happened to its target since.
    """
    later = DocumentEvent.objects.filter(
        document_id=event.document_id, created_at__gt=event.created_at
    )
    if event.kind == DocumentEvent.Kind.LABEL_ADDED:
        return later.filter(label=event.label)
    return later.filter(person=event.person)


def _not_undoable() -> ApiError:
    return ApiError(409, "event_not_undoable", _("Deze wijziging kun je niet meer terugdraaien."))


def record_confirmed(
    document: Document, *, actor: User | None, origin: str = DocumentEvent.Origin.USER
) -> None:
    """The history line for a confirmed review (the AI's has origin `ai` and no actor)."""
    DocumentEvent.objects.create(
        workspace_id=document.workspace_id,
        document=document,
        kind=DocumentEvent.Kind.CONFIRMED,
        origin=origin,
        actor=actor,
    )


def record_reopened(document: Document, *, actor: User) -> None:
    """The history line for taking the AI's confirmation back."""
    DocumentEvent.objects.create(
        workspace_id=document.workspace_id,
        document=document,
        kind=DocumentEvent.Kind.REOPENED,
        origin=DocumentEvent.Origin.USER,
        actor=actor,
    )


def record_trashed(
    document: Document,
    *,
    actor: User | None,
    origin: str = DocumentEvent.Origin.USER,
    related_document: Document | None = None,
) -> None:
    """The history line for moving the letter to the trash: origin `user`, or `merge` with
    the kept letter as `related_document` when a merge discarded it."""
    _write_event(
        document, DocumentEvent.Kind.TRASHED, actor, origin, related_document=related_document
    )


def record_merged(document: Document, *, actor: User, related_document: Document) -> None:
    """On the kept letter: a person merged `related_document` (now in the trash) into it."""
    _write_event(
        document,
        DocumentEvent.Kind.MERGED,
        actor,
        DocumentEvent.Origin.USER,
        related_document=related_document,
    )


def record_duplicate_dismissed(
    document: Document, *, actor: User, related_document: Document
) -> None:
    """On both letters of a pair: a person said they are two letters."""
    _write_event(
        document,
        DocumentEvent.Kind.DUPLICATE_DISMISSED,
        actor,
        DocumentEvent.Origin.USER,
        related_document=related_document,
    )


def record_task_moved(document: Document, *, actor: User, related_document: Document) -> None:
    """On both letters of a merge: an open task moved from the discarded letter to the kept
    one (`related_document` is the other letter)."""
    _write_event(
        document,
        DocumentEvent.Kind.TASK_MOVED,
        actor,
        DocumentEvent.Origin.MERGE,
        related_document=related_document,
    )


def record_restored(document: Document, *, actor: User) -> None:
    """The history line for putting the letter back from the trash."""
    _write_event(document, DocumentEvent.Kind.RESTORED, actor, DocumentEvent.Origin.USER)


def _origin_for(target_id: UUID, origin: str, suggested_ids: Collection[UUID]) -> str:
    return DocumentEvent.Origin.SUGGESTION if target_id in suggested_ids else origin


def _write_event(
    document: Document,
    kind: str,
    actor: User | None,
    origin: str,
    *,
    person: Person | None = None,
    label: Label | None = None,
    rule: Rule | None = None,
    undoes: DocumentEvent | None = None,
    related_document: Document | None = None,
    person_name: str = "",
) -> None:
    DocumentEvent.objects.create(
        workspace_id=document.workspace_id,
        document=document,
        kind=kind,
        origin=origin,
        actor=actor,
        person=person,
        person_name=person.display_name if person is not None else person_name,
        label=label,
        label_name=label.name if label is not None else "",
        rule=rule,
        rule_name=rule.name if rule is not None else "",
        undoes=undoes,
        related_document=related_document,
    )
