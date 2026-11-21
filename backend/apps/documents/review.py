"""Confirming a document: the user's review becomes its fields and its tasks."""

import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.audit.services import AuditActor
from apps.common.errors import ApiError, is_try_again
from apps.common.privacy import mask_bsns
from apps.documents import duplicates, organise, reading, visibility
from apps.documents.models import LIVE, Document, DocumentEvent, DocumentSuggestion
from apps.documents.schemas import ConfirmIn, TaskIn
from apps.pipeline.autopilot import term_phrase
from apps.pipeline.validation import dates_in, quote_in_text
from apps.rules import triggers
from apps.tasks.models import Task
from apps.workspaces.context import system_actor
from apps.workspaces.locks import lock_own_membership
from apps.workspaces.models import Workspace

logger = logging.getLogger(__name__)

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
    amount: Decimal | None = None
    term_quote: str = ""


def confirm_document(
    *, workspace: Workspace, user: User, document_id: UUID, data: ConfirmIn
) -> Document:
    """Store the confirmed review and create its tasks; the document becomes `done`.

    Allowed while the document waits for review, or after processing failed (the
    user then fills everything in by hand). Repeating the same review on a `done`
    document (a retry after a lost answer) changes nothing and answers the same;
    another review is a conflict. With `sets_organisation` the review also makes the
    document's labels and addressees exactly the given lists (design 8). Then the rules
    run (trigger `confirmed`); a repeat does not run them, and a failure in them leaves
    the confirm standing. Needs the workspace's RLS context.

    The write order of S 4.9.3: the caller's own writes first, while the letter is still
    theirs to see; then, as the system, the addressee list, the readers and last the
    status. A confirm that hands the letter to someone else therefore never fails on the
    policies, and a repeat of it is recognised as the system (the caller may no longer
    see the letter) and answers the same.
    """
    with transaction.atomic():
        lock_own_membership(workspace.id, user.id)
        with system_actor():
            seen = (
                Document.objects.select_for_update(no_key=True)
                .filter(LIVE, workspace=workspace, id=document_id)
                .first()
            )
            # Only a person's review repeats: the AI's confirmation is taken back by
            # reopening. Only the member who confirmed it gets the repeat's answer, so
            # the lookup as the system tells nobody else anything.
            if (
                seen is not None
                and seen.status == Document.Status.DONE
                and not seen.confirmed_by_ai
                and seen.confirmed_by_id == user.id
                and _is_repeat(data, seen)
            ):
                return seen
        document = Document.objects.filter(LIVE, workspace=workspace, id=document_id).first()
        if document is None:
            raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
        visibility.require_editable(document)
        if document.status not in _REVIEWABLE:
            raise ApiError(
                409,
                "document_not_reviewable",
                _("Dit document wordt nog verwerkt. Probeer het zo opnieuw.")
                if document.status == Document.Status.PROCESSING
                else _("Dit document kun je nu niet bevestigen."),
            )
        _validate(data, document)
        _store_confirmation(document, data, user=user, by_ai=False)
    return document


def confirm_automatically(document: Document, data: ConfirmIn) -> None:
    """The autopilot's confirm: what a person's confirm writes, with no person.

    The caller holds the lock on a `processing` document and the workspace's RLS context,
    and runs this in a savepoint. `confirmed_by` stays empty, `confirmed_by_ai` is true,
    its tasks are `created_by_ai` and the `confirmed` event has origin `ai` and no actor.
    The same checks as a person's confirm apply; a failure raises `ApiError` (422) and the
    caller leaves the document for review.
    """
    _validate(data, document)
    _store_confirmation(document, data, user=None, by_ai=True)


def _store_confirmation(
    document: Document, data: ConfirmIn, *, user: User | None, by_ai: bool
) -> None:
    """The one place a confirmed review is written: fields, history, tasks, rules (as
    the caller), then the addressee list and readers (the scope's end) and the status (as
    the system)."""
    # A private letter the confirmer would lose by its addressees is a 409 (PB ruling c);
    # handing over a letter that is not private is allowed (`can_read` false).
    with visibility.keep_own_access(document), visibility.addressee_scope(document):
        _write_confirmation(document, data, user=user, by_ai=by_ai)
    with system_actor():
        document.status = Document.Status.DONE
        document.confirmed_by = user
        document.save(update_fields=["status", "confirmed_by", "confirmed_at", "updated_at"])
        # Only openings of the confirmed letter count (security review of PR #44, point 3):
        # one during review is no opening, one of an earlier confirmation still is.
        reading.recompute_addressee_opened(document)
        # Its facts are confirmed now: a pair whose guard fires is withdrawn. As the
        # system: the confirming member may not see the other letter.
        duplicates.recheck(document)


_CONFIRMED_FIELDS = [
    "sender",
    "document_type",
    "letter_date",
    "summary",
    "summary_by_ai",
    "failure_code",
    "confirmed_by_ai",
    "confirmed_organisation",
    "updated_at",
]


def _write_confirmation(
    document: Document, data: ConfirmIn, *, user: User | None, by_ai: bool
) -> None:
    """Step 1 of the write order, as the caller: everything but the status."""
    document.sender = mask_bsns(data.sender.strip())
    document.document_type = data.document_type
    document.letter_date = data.letter_date
    document.summary = mask_bsns(data.summary.strip())
    document.summary_by_ai = data.summary_by_ai
    document.failure_code = ""
    document.confirmed_by_ai = by_ai
    document.confirmed_organisation = _organisation(data) if data.sets_organisation else None
    document.save(update_fields=_CONFIRMED_FIELDS)
    # The rules at confirm read the confirmed values once `confirmed_at` is set
    # (`rules.facts`); it is stored with the status, as the system, in step 2.
    document.confirmed_at = timezone.now()
    if data.sets_organisation and user is not None:
        _set_organisation(document, data, user)
    organise.record_confirmed(
        document,
        actor=user,
        origin=DocumentEvent.Origin.AI if by_ai else DocumentEvent.Origin.USER,
    )
    # A task already on the document stays as it is (after a reopen a ticked-off task is
    # still there): the same one is not made twice. A task dated later through "Datum
    # invullen" still counts as the undated task it came from.
    present = {key for task in document.tasks.all() for key in task_keys(task)}
    for task in _stored_tasks(data, document):
        if (task.type, task.description, task.deadline, task.deadline_source) in present:
            continue
        Task.objects.create(
            workspace_id=document.workspace_id,
            document=document,
            type=task.type,
            description=task.description,
            deadline=task.deadline,
            deadline_source=task.deadline_source,
            deadline_quote=task.deadline_quote,
            amount=task.amount,
            term_quote=task.term_quote,
            created_by_ai=by_ai,
            created_by=user,
        )
    if user is not None:
        _set_visibility(document, data.visibility, user)
    # After the confirmed choices, now with the confirmed sender and type.
    _rules_guarded(document)


def _set_visibility(document: Document, wanted: str, user: User) -> None:
    """`ConfirmIn.visibility` (PB T18): "" leaves it (a Zorg label chosen above already made
    it private), "restricted" makes it private with the confirmer as reader, "workspace"
    makes a private letter visible again (403 `cannot_make_public` stores nothing)."""
    actor = AuditActor.for_user(user)
    if wanted == Document.Visibility.RESTRICTED:
        visibility.make_private(document, actor=actor, origin=DocumentEvent.Origin.USER)
    elif wanted == Document.Visibility.WORKSPACE:
        visibility.make_public(document, actor=actor, user_id=user.id)


def task_key(task: Task) -> tuple[str, str, date | None, str]:
    return (task.type, task.description, task.deadline, task.deadline_source)


def task_keys(task: Task) -> list[tuple[str, str, date | None, str]]:
    """The keys a confirm compares an existing task on: its own, and for a task whose date
    was filled in for a term (`setTaskDeadline`: manual, with a term quote) also the
    undated key it was confirmed with."""
    keys = [task_key(task)]
    if task.deadline_source == Task.DeadlineSource.MANUAL and task.term_quote:
        keys.append((task.type, task.description, None, Task.DeadlineSource.MANUAL))
    return keys


def _rules_guarded(document: Document) -> None:
    """The rules in a savepoint: when they fail, only their writes are rolled back and
    the user's review is still stored.

    A deadlock or serialization failure is not swallowed: the whole confirm rolls back
    and the user tries again (409 `try_again`), so this letter's rules still run.
    """
    try:
        with transaction.atomic():
            triggers.on_confirmed(document)
    except Exception as exc:  # a broken rule must not lose the review
        if is_try_again(exc):
            raise
        # The type only: an exception message may quote the document.
        logger.error(
            "Unexpected %s while running rules for document %s", type(exc).__name__, document.id
        )


def _organisation(data: ConfirmIn) -> dict[str, list[str]]:
    """The confirmed lists as stored, also what a repeated confirm is compared on."""
    return {
        "label_ids": sorted({str(i) for i in data.label_ids}),
        "addressee_ids": sorted({str(i) for i in data.addressee_ids}),
    }


def _set_organisation(document: Document, data: ConfirmIn, user: User) -> None:
    """Labels and addressees become the confirmed lists; the proposals not taken are
    dismissed. A change the user took from an open proposal gets origin `suggestion`.
    Every link that stays loses its `automatic` mark: the person confirmed it."""
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
    # The person confirmed what stays: it is no longer the autopilot's.
    organise.confirm_automatic_links(document)


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
        amount=parse_amount(task.amount),
        # Without a date, the letter's words that name a term ask for one ("Datum nodig").
        term_quote=term_phrase(document.ocr_text) if task.deadline is None else "",
    )


# Digits, then at most two decimals after a point or comma; below 10^10 (max_digits 12).
_AMOUNT = re.compile(r"[0-9]{1,10}(?:[.,][0-9]{1,2})?")


def parse_amount(value: str) -> Decimal | None:
    """`""` is no amount; otherwise a decimal of at least 0 and below 10^10 with at most two
    decimals, written "1234.56" or "1234,56" (no thousands separators), else 422."""
    text = value.strip()
    if not text:
        return None
    if not _AMOUNT.fullmatch(text):
        raise _bad_amount()
    return Decimal(text.replace(",", ".")).quantize(Decimal("0.01"))


def _bad_amount() -> ApiError:
    return _invalid(_("Het bedrag klopt niet. Schrijf het als 1234,56."))


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
            amount=task.amount,
            term_quote=task.term_quote,
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


# A typo (2062, 0001) would land in the lists and exports; an old letter may be scanned.
LETTER_DATE_FROM = date(1900, 1, 1)
LETTER_DATE_AHEAD = timedelta(days=366)


def _validate(data: ConfirmIn, document: Document) -> None:
    if data.visibility and data.visibility not in Document.Visibility.values:
        raise _invalid(_("Kies privé of zichtbaar voor iedereen."))
    if data.document_type and data.document_type not in Document.Type.values:
        raise _invalid(_("Dit soort document kennen we niet."))
    if data.letter_date is not None and not (
        LETTER_DATE_FROM <= data.letter_date <= timezone.localdate() + LETTER_DATE_AHEAD
    ):
        raise _invalid(_("Een briefdatum ligt tussen 1 januari 1900 en een jaar na vandaag."))
    for task in data.tasks:
        _validate_task(task, document)


def _validate_task(task: TaskIn, document: Document) -> None:
    parse_amount(task.amount)
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
