"""Confirming a document: the user's review becomes its fields and its tasks."""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.documents import organise
from apps.documents.models import Document, DocumentSuggestion
from apps.documents.schemas import ConfirmIn, TaskIn
from apps.pipeline.validation import dates_in, quote_in_text
from apps.tasks.models import Task
from apps.workspaces.models import Workspace

_REVIEWABLE = {Document.Status.NEEDS_REVIEW, Document.Status.FAILED}
_CLIENT_SOURCES = {Task.DeadlineSource.IN_DOCUMENT, Task.DeadlineSource.MANUAL}


@dataclass(frozen=True)
class _StoredTask:
    """A task as confirm stores it; also what a repeated confirm is compared on."""

    type: str
    description: str
    deadline: date | None
    deadline_source: str
    deadline_quote: str


def confirm_document(
    *, workspace: Workspace, user: User, document_id: UUID, data: ConfirmIn
) -> Document:
    """Store the confirmed review and create its tasks; the document becomes `done`.

    Allowed while the document waits for review, or after processing failed (the
    user then fills everything in by hand). Repeating the same review on a `done`
    document (a retry after a lost answer) changes nothing and answers the same;
    another review is a conflict. With `sets_organisation` the review also makes the
    document's labels and addressees exactly the given lists (design 8). Needs the
    workspace's RLS context.
    """
    with transaction.atomic():
        document = (
            Document.objects.select_for_update().filter(workspace=workspace, id=document_id).first()
        )
        if document is None:
            raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
        if document.status == Document.Status.DONE and _is_repeat(data, document):
            return document
        if document.status not in _REVIEWABLE:
            raise ApiError(
                409,
                "document_not_reviewable",
                _("Dit document kun je nu niet bevestigen."),
            )
        _validate(data, document)

        document.sender = mask_bsns(data.sender.strip())
        document.document_type = data.document_type
        document.letter_date = data.letter_date
        document.summary = mask_bsns(data.summary.strip())
        document.summary_by_ai = data.summary_by_ai
        document.status = Document.Status.DONE
        document.failure_code = ""
        document.confirmed_by = user
        document.confirmed_at = timezone.now()
        document.confirmed_organisation = _organisation(data) if data.sets_organisation else None
        document.save()
        if data.sets_organisation:
            _set_organisation(document, data, user)
        organise.record_confirmed(document, actor=user)
        for task in _stored_tasks(data, document):
            Task.objects.create(
                workspace=workspace,
                document=document,
                type=task.type,
                description=task.description,
                deadline=task.deadline,
                deadline_source=task.deadline_source,
                deadline_quote=task.deadline_quote,
            )
    return document


def _organisation(data: ConfirmIn) -> dict[str, list[str]]:
    """The confirmed lists as stored, also what a repeated confirm is compared on."""
    return {
        "label_ids": sorted({str(i) for i in data.label_ids}),
        "addressee_ids": sorted({str(i) for i in data.addressee_ids}),
    }


def _set_organisation(document: Document, data: ConfirmIn, user: User) -> None:
    """Labels and addressees become the confirmed lists; the proposals not taken are
    dismissed. A change the user took from an open proposal gets origin `suggestion`."""
    open_rows = DocumentSuggestion.objects.filter(
        document=document, state=DocumentSuggestion.State.OPEN
    )
    proposed_labels = {s.label_id for s in open_rows if s.label_id is not None}
    proposed_persons = {s.person_id for s in open_rows if s.person_id is not None}
    organise.set_labels(
        document=document, label_ids=data.label_ids, actor=user, suggested_ids=proposed_labels
    )
    organise.set_addressees(
        document=document,
        person_ids=data.addressee_ids,
        actor=user,
        suggested_ids=proposed_persons,
    )
    open_rows.update(state=DocumentSuggestion.State.DISMISSED)


def _stored_tasks(data: ConfirmIn, document: Document) -> list[_StoredTask]:
    return [_stored_task(task, document) for task in data.tasks]


def _stored_task(task: TaskIn, document: Document) -> _StoredTask:
    source = task.deadline_source
    quote = task.deadline_quote.strip()
    # The quote shown with a deadline must say that date. When it does not (the user
    # changed the date, or the AI misread it), the date is the user's own: it is kept
    # as a manual deadline, like an unverified proposal, without the misleading quote.
    if source == Task.DeadlineSource.IN_DOCUMENT and task.deadline not in dates_in(quote):
        source = Task.DeadlineSource.MANUAL
    return _StoredTask(
        type=task.type,
        description=mask_bsns(task.description.strip()),
        deadline=task.deadline,
        deadline_source=source,
        # A deadline the user typed has no source in the document.
        deadline_quote=quote if source == Task.DeadlineSource.IN_DOCUMENT else "",
    )


def _is_repeat(data: ConfirmIn, document: Document) -> bool:
    """Whether `data` is the review this document was confirmed with.

    Compared on what was stored (trimmed, BSNs masked, tasks in any order), so the
    exact same request always matches. Task fields the user may change later (status)
    are not part of it. Labels and addressees are compared with the confirmed snapshot,
    not the current ones (a rule may have added to them since); a review with
    `sets_organisation` never repeats one without it, nor the reverse.
    """
    stored = Counter(
        _StoredTask(
            type=task.type,
            description=task.description,
            deadline=task.deadline,
            deadline_source=task.deadline_source,
            deadline_quote=task.deadline_quote,
        )
        for task in document.tasks.all()
    )
    return (
        document.sender == mask_bsns(data.sender.strip())
        and document.document_type == data.document_type
        and document.letter_date == data.letter_date
        and document.summary == mask_bsns(data.summary.strip())
        and document.summary_by_ai == data.summary_by_ai
        and stored == Counter(_stored_tasks(data, document))
        and document.confirmed_organisation
        == (_organisation(data) if data.sets_organisation else None)
    )


def _validate(data: ConfirmIn, document: Document) -> None:
    if data.document_type and data.document_type not in Document.Type.values:
        raise _invalid(_("Dit soort document kennen we niet."))
    for task in data.tasks:
        _validate_task(task, document)


def _validate_task(task: TaskIn, document: Document) -> None:
    if task.type not in Task.Type.values:
        raise _invalid(_("Dit soort taak kennen we niet."))
    if not task.description.strip():
        raise _invalid(_("Een taak heeft een omschrijving nodig."))
    if task.deadline_source not in _CLIENT_SOURCES:
        raise _invalid(_("De bron van de datum klopt niet."))
    if task.deadline_source == Task.DeadlineSource.IN_DOCUMENT and (
        task.deadline is None or not quote_in_text(task.deadline_quote, document.ocr_text)
    ):
        # The source shown with a deadline must really be in the document.
        raise _invalid(_("Bij deze datum hoort de zin uit het document waar hij staat."))


def _invalid(message: str) -> ApiError:
    return ApiError(422, "validation_error", message)
