"""Confirming a document: the user's review becomes its fields and its tasks."""

from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.documents.models import Document
from apps.documents.schemas import ConfirmIn, TaskIn
from apps.pipeline.validation import quote_in_text
from apps.tasks.models import Task
from apps.workspaces.models import Workspace

_REVIEWABLE = {Document.Status.NEEDS_REVIEW, Document.Status.FAILED}
_CLIENT_SOURCES = {Task.DeadlineSource.IN_DOCUMENT, Task.DeadlineSource.MANUAL}


def confirm_document(
    *, workspace: Workspace, user: User, document_id: UUID, data: ConfirmIn
) -> Document:
    """Store the confirmed review and create its tasks; the document becomes `done`.

    Allowed while the document waits for review, or after processing failed (the
    user then fills everything in by hand). Needs the workspace's RLS context.
    """
    with transaction.atomic():
        document = (
            Document.objects.select_for_update().filter(workspace=workspace, id=document_id).first()
        )
        if document is None:
            raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
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
        document.save()
        for task in data.tasks:
            in_document = task.deadline_source == Task.DeadlineSource.IN_DOCUMENT
            Task.objects.create(
                workspace=workspace,
                document=document,
                type=task.type,
                description=mask_bsns(task.description.strip()),
                deadline=task.deadline,
                deadline_source=task.deadline_source,
                # A deadline the user typed has no source in the document.
                deadline_quote=task.deadline_quote.strip() if in_document else "",
            )
    return document


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
