from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.functions import Coalesce, ExtractYear
from django.utils import timezone
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from apps.accounts.models import User
from apps.audit.services import AuditActor
from apps.common import crypto
from apps.common.errors import ApiError
from apps.documents import (
    duplicates,
    lingering,
    new_persons,
    organise,
    reading,
    suggestions,
    visibility,
)
from apps.documents.intake import page_aad
from apps.documents.models import (
    LETTERS_ONLY,
    LIVE,
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentPage,
    DocumentSuggestion,
    Extraction,
    Folder,
)
from apps.documents.schemas import (
    DocumentOut,
    DocumentSummaryOut,
    DocumentVisibilityOut,
    LingeringLetterOut,
    LingeringOut,
    NewPersonSuggestionOut,
    PageOut,
    ProposalOut,
    SuggestionOut,
    TaskOut,
)
from apps.documents.storage import default_storage
from apps.people.models import Person
from apps.pipeline.jobs import defer_processing
from apps.tasks import letter_sync
from apps.tasks.facts import member_facts
from apps.tasks.letter_state import LetterStatus, status_of
from apps.tasks.models import Task, amount_text
from apps.tasks.services import delete_tasks
from apps.workspaces.context import current_actor
from apps.workspaces.keys import workspace_key
from apps.workspaces.locks import lock_own_membership
from apps.workspaces.models import Membership, Workspace

if TYPE_CHECKING:
    from apps.tasks.assignment import TaskFlags

_PATH_UNIQUE = "documents_folder_workspace_path_unique"


def list_folders(workspace: Workspace) -> list[Folder]:
    """Folders of the workspace whose RLS context the caller has entered."""
    return list(Folder.objects.filter(workspace=workspace).order_by("path"))


def create_folder(*, workspace: Workspace, name: str, parent_id: UUID | None) -> Folder:
    """Create a folder; needs the workspace's RLS context (see workspaces.deps).

    A parent is looked up under RLS, so one from another workspace is not found.
    """
    with transaction.atomic():
        parent = None
        if parent_id is not None:
            parent = Folder.objects.filter(workspace=workspace, id=parent_id).first()
            if parent is None:
                raise ApiError(404, "folder_not_found", _("We kunnen deze map niet vinden."))
        folder = Folder(workspace=workspace, parent=parent, name=name)
        try:
            with transaction.atomic():
                folder.save()
        except ValidationError as exc:
            raise ApiError(422, "validation_error", exc.messages[0]) from exc
        except IntegrityError as exc:
            cause = exc.__cause__
            if (
                isinstance(cause, pg_errors.UniqueViolation)
                and cause.diag.constraint_name == _PATH_UNIQUE
            ):
                raise ApiError(
                    409, "folder_exists", _("Er is al een map met deze naam op deze plek.")
                ) from exc
            raise
    return folder


class InboxState:
    NEW = "new"
    REVIEW = "review"
    UNASSIGNED = "unassigned"
    SORTED = ""


INBOX_STATES = (InboxState.NEW, InboxState.REVIEW, InboxState.UNASSIGNED)
_REVIEW_STATUSES = (Document.Status.NEEDS_REVIEW, Document.Status.FAILED)


def inbox_state(document: Document) -> str:
    """Where the document stands in the inbox; derived, not stored (design 11). An own
    document is never in the Inbox (archive spec S 8): always sorted."""
    if document.kind == Document.Kind.DOCUMENT:
        return InboxState.SORTED
    if document.status == Document.Status.PROCESSING:
        return InboxState.NEW
    if document.status in _REVIEW_STATUSES:
        return InboxState.REVIEW
    if not _addressee_ids(document):
        return InboxState.UNASSIGNED
    return InboxState.SORTED


def _addressee_ids(document: Document) -> list[UUID]:
    # `.all()` so a prefetch (list_documents) is used.
    return [link.person_id for link in document.addressee_links.all()]


def _label_ids(document: Document) -> list[UUID]:
    # `.all()` so a prefetch (list_documents) is used.
    return [link.label_id for link in document.label_links.all()]


def _auto_addressee_ids(document: Document) -> list[UUID]:
    return [link.person_id for link in document.addressee_links.all() if link.automatic]


def _auto_label_ids(document: Document) -> list[UUID]:
    return [link.label_id for link in document.label_links.all() if link.automatic]


def _auto_rule_label_ids(document: Document) -> list[UUID]:
    """The automatic labels whose accepted proposal came from a rule (the autopilot applied
    a rule's label). Read from the suggestions, so a list's prefetch is used."""
    by_rule = {
        s.label_id
        for s in document.suggestions.all()
        if s.label_id is not None
        and s.state == DocumentSuggestion.State.ACCEPTED
        and s.origin == DocumentSuggestion.Origin.RULE
    }
    return [label_id for label_id in _auto_label_ids(document) if label_id in by_rule]


# The prefetch of a document's suggestions wherever several documents are shown: with
# their persons, so `_open_suggestions` can leave out archived ones without a query each.
SUGGESTIONS = models.Prefetch(
    "suggestions", queryset=DocumentSuggestion.objects.select_related("person")
)


_HEADLINE_FIELDS = (
    Extraction.Field.SENDER,
    Extraction.Field.DOCUMENT_TYPE,
    Extraction.Field.LETTER_DATE,
)
# The proposals a list row shows for a letter not confirmed yet; one query for the list.
HEADLINE_PROPOSALS = models.Prefetch(
    "extractions",
    queryset=Extraction.objects.filter(group=0, field__in=_HEADLINE_FIELDS).order_by(
        "-confidence", "created_at", "id"
    ),
    to_attr="headline_proposals",
)


def _headline_proposals(document: Document) -> dict[str, str]:
    """Per field, the strongest proposal for what a person has not confirmed yet.

    A value found nowhere in the letter (confidence 0) is no proposal to show; a type or
    date that does not parse is left out. Uses `HEADLINE_PROPOSALS` when prefetched.
    """
    if document.confirmed_at is not None:
        return {}
    rows: list[Extraction] | None = getattr(document, "headline_proposals", None)
    if rows is None:
        models.prefetch_related_objects([document], HEADLINE_PROPOSALS)
        rows = getattr(document, "headline_proposals", [])
    best: dict[str, str] = {}
    for row in rows:
        if row.field in best or not row.value:
            continue
        if row.field != Extraction.Field.DOCUMENT_TYPE and not row.confidence > 0:
            continue
        if row.field == Extraction.Field.DOCUMENT_TYPE and row.value not in Document.Type.values:
            continue
        if row.field == Extraction.Field.LETTER_DATE and _iso_date(row.value) is None:
            continue
        best[row.field] = row.value
    return best


def _iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _open_suggestions(document: Document) -> list[DocumentSuggestion]:
    """Preselected first, then by confidence. `.all()` so a prefetch is used.

    A proposal of a person archived since is left out: it can no longer be accepted
    (archiving removes it as well; this covers a row left from before).
    """
    rows = [
        s
        for s in document.suggestions.all()
        if s.state == DocumentSuggestion.State.OPEN
        and (s.person is None or s.person.archived_at is None)
    ]
    return sorted(rows, key=lambda s: (not s.preselected, -s.confidence, s.created_at, s.id))


def _is_automatic() -> models.Q:
    """The AI confirmed the document or put a label or addressee on it itself (the stored
    `has_automatic_link`, kept by `organise.sync_automatic`; DECISIONS 2026-11-20)."""
    return models.Q(confirmed_by_ai=True) | models.Q(has_automatic_link=True)


def _in_state(state: str) -> models.Q:
    """Letters only: an own document is never in the Inbox (archive spec S 8)."""
    if state == InboxState.NEW:
        return LETTERS_ONLY & models.Q(status=Document.Status.PROCESSING)
    if state == InboxState.REVIEW:
        return LETTERS_ONLY & models.Q(status__in=_REVIEW_STATUSES)
    # `addressee_ids` mirrors the links (one writer, checked nightly); this form uses the
    # partial index `documents_document_unassigned` (its condition names `kind` too).
    return LETTERS_ONLY & models.Q(status=Document.Status.DONE, addressee_ids=[])


LETTER_STATUSES = tuple(str(each) for each in LetterStatus)
_NOT_REVIEWED = (
    Document.Status.PROCESSING,
    Document.Status.NEEDS_REVIEW,
    Document.Status.FAILED,
)


def letter_status_q(status: str, today: date) -> models.Q:
    """The letters in a status today (spec S 4.2), from the three cached task facts and the
    review; the partial index `documents_document_open_tasks` serves open, waiting and
    late. 422 for a status that does not exist. Letters only: an own document has no
    status (archive spec AR7)."""
    done = Document.Status.DONE
    if status == LetterStatus.NEW:
        return LETTERS_ONLY & models.Q(status__in=_NOT_REVIEWED)
    if status == LetterStatus.HANDLED:
        return LETTERS_ONLY & models.Q(status=done, task_open_count=0)
    active = LETTERS_ONLY & models.Q(status=done, task_open_count__gt=0)
    if status == LetterStatus.WAITING:
        return active & models.Q(task_wait_until__gt=today)
    if status == LetterStatus.OPEN:
        return active & (
            models.Q(task_wait_until__isnull=True) | models.Q(task_wait_until__lte=today)
        )
    raise ApiError(422, "validation_error", _("Deze status kennen we niet."))


def overdue_q(today: date) -> models.Q:
    """The marker "Te laat": a reviewed letter, and an open task is active with a deadline
    behind. Never an own document (its reminder is late in "Mijn taken" only)."""
    return LETTERS_ONLY & models.Q(
        status=Document.Status.DONE, task_open_count__gt=0, task_overdue_from__lte=today
    )


def _today() -> date:
    # Imported here: `tasks.services` reads letters through this module.
    from apps.tasks.services import task_today

    return task_today()


@dataclass(frozen=True)
class LetterStatusCounts:
    new: int
    open: int
    waiting: int
    handled: int
    overdue: int


def status_counts(membership: Membership, *, person_id: UUID | None = None) -> LetterStatusCounts:
    """The letters the member sees per status (and the late ones), in one pass over
    `visible_documents`; with `person_id` only that person's post. An unknown or archived
    person gives zeros, not a 404 (a person list is a directory)."""
    today = _today()
    documents = visibility.visible_documents(membership)
    if person_id is not None:
        # An archived person (or an unknown one) is the counts of nothing, as the API says.
        if not Person.objects.filter(
            workspace_id=membership.workspace_id, id=person_id, archived_at__isnull=True
        ).exists():
            return LetterStatusCounts(new=0, open=0, waiting=0, handled=0, overdue=0)
        documents = documents.filter(addressee_ids__contains=[person_id])
    counts = documents.aggregate(
        **{
            name: models.Count("id", filter=letter_status_q(name, today))
            for name in LETTER_STATUSES
        },
        overdue=models.Count("id", filter=overdue_q(today)),
    )
    return LetterStatusCounts(**{name: int(counts[name]) for name in (*LETTER_STATUSES, "overdue")})


def my_post_filter(membership: Membership, *, include_departments: bool = False) -> models.Q:
    """ "Mijn post" (navigation spec B2): letters on the member's own persons (their own and
    those they manage), with `include_departments` also on their departments ("Ook mijn
    afdeling"). Matches nothing for a member without any. The GIN index on
    `addressee_ids`; only ever applied on top of `visible_documents`. Letters only: Mijn
    post is the personal Inbox of post; an own document is in Archief (spec S 8)."""
    facts = member_facts(membership)
    ids = set(facts.person_ids)
    if include_departments:
        ids |= facts.department_ids
    if not ids:
        return models.Q(pk__in=[])
    return LETTERS_ONLY & models.Q(addressee_ids__overlap=sorted(ids))


def list_documents(
    membership: Membership,
    *,
    inbox_state: str = "",
    person_id: UUID | None = None,
    label_id: UUID | None = None,
    automatic: bool = False,
    addressed_to_me: bool = False,
    include_departments: bool = False,
    unread_only: bool = False,
    letter_status: str = "",
    overdue: bool = False,
    archive: "ArchiveFilters | None" = None,
) -> list[Document]:
    """The newest documents the member sees (at most 200; paging comes later), filtered.

    `automatic` keeps the documents the autopilot confirmed or organised;
    `addressed_to_me` the member's own post ("Mijn post", with `include_departments` also
    their departments'); `unread_only` the letters they have not read (navigation S 5);
    `letter_status` (`new`, `open`, `waiting`, `handled`) and `overdue` the letter status;
    `archive` the archive's filters (`archive_q`). Every filter narrows.
    """
    if inbox_state and inbox_state not in INBOX_STATES:
        raise ApiError(422, "validation_error", _("Deze stand van de inbox kennen we niet."))
    documents = visibility.visible_documents(membership)
    if archive is not None:
        documents = documents.filter(archive_q(archive, _today()))
    if letter_status or overdue:
        today = _today()
        if letter_status:
            documents = documents.filter(letter_status_q(letter_status, today))
        if overdue:
            documents = documents.filter(overdue_q(today))
    if addressed_to_me:
        documents = documents.filter(
            my_post_filter(membership, include_departments=include_departments)
        )
    if unread_only:
        documents = documents.filter(reading.unread_q(membership))
    if inbox_state:
        documents = documents.filter(_in_state(inbox_state))
    if person_id is not None:
        # The GIN index on `addressee_ids`.
        documents = documents.filter(addressee_ids__contains=[person_id])
    if label_id is not None:
        documents = documents.filter(label_links__label_id=label_id)
    if automatic:
        documents = documents.filter(_is_automatic())
    return list(
        documents.prefetch_related(
            "addressee_links", "label_links", SUGGESTIONS, HEADLINE_PROPOSALS
        ).order_by("-created_at", "-id")[:200]
    )


@dataclass(frozen=True)
class ArchiveFilters:
    """The filters of "Archief" (archive spec S 5); each empty value filters nothing."""

    # `letter` or `document`.
    kind: str = ""
    document_type: str = ""
    # The document's own date's year, else its upload's.
    year: int = 0
    # Exactly this sender (chosen from the facets, never typed into a URL).
    sender: str = ""
    # Reviewed only (`status = done`): the archive.
    archived: bool = False
    # Own documents whose end date is within `EXPIRING_DAYS` or past ("Loopt af").
    expiring: bool = False


# "Loopt af": an end date within 90 days, or past.
EXPIRING_DAYS = 90
YEAR_FROM = 1900
YEAR_TO = 2200


def validate_archive(filters: ArchiveFilters) -> None:
    """422 for a kind, type or year that does not exist."""
    if filters.kind and filters.kind not in Document.Kind.values:
        raise ApiError(422, "validation_error", _("Kies brieven of documenten."))
    if filters.document_type and filters.document_type not in Document.Type.values:
        raise ApiError(422, "validation_error", _("Deze soort brief kennen we niet."))
    if filters.year and not YEAR_FROM <= filters.year <= YEAR_TO:
        raise ApiError(422, "validation_error", _("Dit jaar kennen we niet."))


def archive_q(filters: ArchiveFilters, today: date) -> models.Q:
    """The archive's filters as one condition, only ever applied on top of
    `visible_documents`: it narrows, it never widens. 422 for an unknown kind, type or
    year (`validate_archive`)."""
    validate_archive(filters)
    condition = models.Q()
    if filters.archived:
        condition &= models.Q(status=Document.Status.DONE)
    if filters.kind:
        condition &= models.Q(kind=filters.kind)
    if filters.document_type:
        condition &= models.Q(document_type=filters.document_type)
    if filters.sender:
        condition &= models.Q(sender=filters.sender)
    if filters.year:
        start, end = date(filters.year, 1, 1), date(filters.year + 1, 1, 1)
        condition &= models.Q(letter_date__gte=start, letter_date__lt=end) | models.Q(
            letter_date__isnull=True, created_at__date__gte=start, created_at__date__lt=end
        )
    if filters.expiring:
        condition &= models.Q(
            kind=Document.Kind.DOCUMENT,
            expires_on__lte=today + timedelta(days=EXPIRING_DAYS),
        )
    return condition


@dataclass(frozen=True)
class Facet:
    value: str
    count: int


@dataclass(frozen=True)
class ArchiveFacets:
    kinds: list[Facet]
    types: list[Facet]
    years: list[Facet]
    senders: list[Facet]


# The senders facet names the most frequent; the rest are found through search.
SENDERS_MAX = 50


def list_archive_facets(membership: Membership) -> ArchiveFacets:
    """What the archive's filters can choose, counted over the reviewed documents the
    member sees (`visible_documents`, `status = done`): kinds and types in one pass with
    filtered counts, years and the 50 most frequent senders grouped. A value nobody sees
    is never counted; a zero count is left out."""
    archived = visibility.visible_documents(membership).filter(status=Document.Status.DONE)
    counts = archived.aggregate(
        **{
            f"kind_{kind}": models.Count("id", filter=models.Q(kind=kind))
            for kind in Document.Kind.values
        },
        **{
            f"type_{kind}": models.Count("id", filter=models.Q(document_type=kind))
            for kind in Document.Type.values
        },
    )
    kinds = [
        Facet(kind, int(counts[f"kind_{kind}"]))
        for kind in Document.Kind.values
        if counts[f"kind_{kind}"]
    ]
    types = sorted(
        (
            Facet(kind, int(counts[f"type_{kind}"]))
            for kind in Document.Type.values
            if counts[f"type_{kind}"]
        ),
        key=lambda facet: (-facet.count, facet.value),
    )
    years = [
        Facet(str(row["year"]), int(row["count"]))
        for row in archived.annotate(
            year=Coalesce(ExtractYear("letter_date"), ExtractYear("created_at"))
        )
        .values("year")
        .annotate(count=models.Count("id"))
        .order_by("-year")
        if row["year"] is not None
    ]
    senders = [
        Facet(str(row["sender"]), int(row["count"]))
        for row in archived.exclude(sender="")
        .values("sender")
        .annotate(count=models.Count("id"))
        .order_by("-count", "sender")[:SENDERS_MAX]
    ]
    return ArchiveFacets(kinds=kinds, types=types, years=years, senders=senders)


def inbox_counts(membership: Membership) -> dict[str, int]:
    """How many of the documents the member sees are in each inbox state, for the
    badges: one pass."""
    counts = visibility.visible_documents(membership).aggregate(
        **{state: models.Count("id", filter=_in_state(state)) for state in INBOX_STATES},
        automatic=models.Count("id", filter=_is_automatic()),
    )
    return {
        **{name: int(counts[name]) for name in (*INBOX_STATES, "automatic")},
        "duplicate": duplicates.count_pairs(membership),
    }


def waiting_letters(membership: Membership) -> int:
    """How many letters the member sees wait to be checked or named ("Inbox"'s review
    and unassigned states), for the line on top of "Mijn taken"."""
    letters = visibility.visible_documents(membership)
    # Two counts, each on its own partial index (`documents_document_review`,
    # `documents_document_unassigned`): the policy then runs only for the few matching
    # letters. One OR of the two (or an aggregate filter) runs it for every letter.
    return sum(
        letters.filter(_in_state(state)).count()
        for state in (InboxState.REVIEW, InboxState.UNASSIGNED)
    )


@dataclass(frozen=True)
class MenuCounts:
    inbox: int
    my_post_unread: int
    my_tasks: int
    my_tasks_overdue: int
    lingering: int
    shows_lingering: bool
    has_person: bool


def menu_counts(membership: Membership) -> MenuCounts:
    """The numbers of the menu in one answer (navigation spec S 9): letters waiting in the
    Inbox, unread letters in Mijn post, the member's open and overdue tasks, and for the
    sorters and admins of an organisation the letters that linger. Every number starts
    from `visible_documents` (or the task policy): nothing hidden is counted."""
    # Imported here: `tasks.services` reads letters through this module.
    from apps.tasks.services import count_my_tasks

    facts = member_facts(membership)
    own = sorted(facts.person_ids)
    unread = (
        visibility.visible_documents(membership)
        .filter(addressee_ids__overlap=own)
        .filter(reading.unread_q(membership))
        .count()
        if own
        else 0
    )
    tasks = count_my_tasks(membership)
    return MenuCounts(
        inbox=waiting_letters(membership),
        my_post_unread=unread,
        my_tasks=tasks.mine,
        my_tasks_overdue=tasks.overdue,
        lingering=lingering.count_lingering(membership),
        shows_lingering=lingering.applies_to(membership),
        has_person=bool(own),
    )


def lingering_out(membership: Membership) -> LingeringOut:
    """ "Blijft liggen" for the member (`lingering.lingering_letters`), as rows with their
    days. Empty unless they sort post or are an admin of an organisation with it on."""
    result = lingering.lingering_letters(membership)
    now = timezone.now()

    def rows(documents: list[Document]) -> list[LingeringLetterOut]:
        return [
            LingeringLetterOut(**summary.model_dump(), days=lingering.days_of(document, now=now))
            for document, summary in zip(
                documents, document_summaries_out(membership, documents), strict=True
            )
        ]

    return LingeringOut(
        too_long_in_inbox=rows(result.too_long_in_inbox),
        not_opened=rows(result.not_opened),
        inbox_days=result.inbox_days,
        unopened_days=result.unopened_days,
    )


def get_document(*, membership: Membership, document_id: UUID) -> Document:
    """A document the member sees, or 404 (one they may not see is not found either).

    A letter in the member's own trash answers `document_in_trash`; one in the trash they
    may not see, or whose purge started, is not found like any other.
    """
    document = visibility.visible_documents(membership).filter(id=document_id).first()
    if document is None:
        if visibility.trashed_documents(membership).filter(id=document_id).exists():
            raise ApiError(404, "document_in_trash", _("Deze brief staat in de prullenbak."))
        raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
    return document


def document_access(
    *, membership: Membership, document_id: UUID
) -> list[tuple[UUID, str, list[str]]]:
    """Who can see a letter the member sees, and why: (user id, name, reasons) by name.
    404 like `get_document` for a letter the member may not see."""
    document = get_document(membership=membership, document_id=document_id)
    reasons = visibility.viewer_reasons(document)
    names = dict(
        Membership.objects.filter(
            workspace_id=membership.workspace_id, user_id__in=list(reasons)
        ).values_list("user_id", "user__name")
    )
    return sorted(
        ((user_id, names.get(user_id, ""), why) for user_id, why in reasons.items()),
        key=lambda row: (row[1].casefold(), str(row[0])),
    )


def document_summary_out(
    document: Document,
    *,
    open_duplicate_count: int = 0,
    reader_names: list[str] | None = None,
    unread: bool = False,
    reminder_task_id: UUID | None = None,
) -> DocumentSummaryOut:
    """A row of a document list."""
    return _summary_out(
        document,
        _open_suggestions(document),
        open_duplicate_count,
        reader_names or [],
        unread,
        reminder_task_id,
    )


def reminder_task_ids(documents: list[Document]) -> dict[UUID, UUID]:
    """The open end-date reminder of each own document among `documents` (already read
    through the seam), in one query; the task policy follows the document's."""
    own = [d.id for d in documents if d.kind == Document.Kind.DOCUMENT]
    if not own:
        return {}
    return dict(
        Task.objects.filter(
            document_id__in=own, type=Task.Type.EXPIRY, status=Task.Status.OPEN
        ).values_list("document_id", "id")
    )


def document_summaries_out(
    membership: Membership, documents: list[Document]
) -> list[DocumentSummaryOut]:
    """Rows of a document list for the member, with the open duplicate pairs they see in
    full per letter (one query for all)."""
    counts = duplicates.pair_counts(membership, [d.id for d in documents])
    names = visibility.reader_names(documents)
    unread = reading.unread_ids(membership, documents)
    reminders = reminder_task_ids(documents)
    return [
        document_summary_out(
            d,
            open_duplicate_count=counts.get(d.id, 0),
            reader_names=names.get(d.id, []),
            unread=d.id in unread,
            reminder_task_id=reminders.get(d.id),
        )
        for d in documents
    ]


def document_out_as(membership: Membership, document: Document) -> DocumentOut:
    """`document_out` for the member, with the open duplicate pairs they see in full."""
    counts = duplicates.pair_counts(membership, [document.id])
    return document_out(document, open_duplicate_count=counts.get(document.id, 0))


def _summary_out(
    document: Document,
    open_suggestions: list[DocumentSuggestion],
    open_duplicate_count: int = 0,
    reader_names: list[str] | None = None,
    unread: bool = False,
    reminder_task_id: UUID | None = None,
) -> DocumentSummaryOut:
    proposed = _headline_proposals(document)
    status, late = status_of(
        document.status == Document.Status.DONE, letter_sync.stored_facts(document), _today()
    )
    # An own document has no letter status, also with an open reminder (archive AR7).
    is_letter = document.kind == Document.Kind.LETTER
    return DocumentSummaryOut(
        id=document.id,
        client_id=document.client_id,
        status=document.status,
        page_count=document.page_count,
        created_at=document.created_at,
        sender=document.sender,
        document_type=document.document_type,
        letter_date=document.letter_date,
        addressee_ids=_addressee_ids(document),
        label_ids=_label_ids(document),
        inbox_state=inbox_state(document),
        open_suggestion_count=len(open_suggestions),
        confirmed_by_ai=document.confirmed_by_ai,
        auto_label_ids=_auto_label_ids(document),
        auto_rule_label_ids=_auto_rule_label_ids(document),
        auto_addressee_ids=_auto_addressee_ids(document),
        proposed_sender="" if document.sender else proposed.get(Extraction.Field.SENDER, ""),
        proposed_document_type=""
        if document.document_type
        else proposed.get(Extraction.Field.DOCUMENT_TYPE, ""),
        proposed_letter_date=None
        if document.letter_date
        else _iso_date(proposed.get(Extraction.Field.LETTER_DATE, "")),
        open_duplicate_count=open_duplicate_count,
        visibility=document.visibility,
        reader_names=reader_names or [],
        unread=unread,
        letter_status=str(status) if is_letter else "",
        overdue=late and is_letter,
        open_task_count=document.task_open_count if is_letter else 0,
        waiting_until=document.task_wait_until
        if is_letter and status == LetterStatus.WAITING
        else None,
        kind=document.kind,
        title=document.title,
        expires_on=document.expires_on,
        remind_days=document.remind_days,
        retain_until=document.retain_until,
        reminder_task_id=reminder_task_id,
    )


def document_out_for(document: Document) -> DocumentOut:
    """The answer of an organising endpoint: the full document while the caller still
    sees it, else only its id and status with `can_read` false (S 4.9.3: `can_read` is
    computed last, as the caller; nothing of the letter is read as the caller after)."""
    if visibility.can_read(document.id):
        return document_out(document)
    return DocumentOut(
        id=document.id,
        client_id=document.client_id,
        status=visibility.status_of(document.id) or document.status,
        page_count=0,
        created_at=document.created_at,
        sender="",
        document_type="",
        letter_date=None,
        failure_code="",
        summary="",
        summary_by_ai=False,
        pages=[],
        proposals=[],
        tasks=[],
        suggestions=[],
        addressee_ids=[],
        label_ids=[],
        inbox_state="",
        can_read=False,
    )


def document_out(document: Document, *, open_duplicate_count: int = 0) -> DocumentOut:
    """The document with its pages, proposals and tasks, as the API shows it."""
    models.prefetch_related_objects([document], SUGGESTIONS)
    open_suggestions = _open_suggestions(document)
    names = visibility.reader_names([document]).get(document.id, [])
    actor = current_actor()
    unread = reading.is_unread_for_actor(document)
    # Imported here: the tasks app reads letters through this module.
    from apps.tasks import snooze
    from apps.tasks.assignment import flags_for_actor

    tasks = list(
        document.tasks.select_related("assigned_to").order_by("deadline", "created_at", "id")
    )
    flags = flags_for_actor(document.workspace_id, tasks)
    today = _today()
    own = snooze.snoozed_until(tasks, actor, today) if isinstance(actor, UUID) else {}
    reminder = next(
        (
            t.id
            for t in tasks
            if t.type == Task.Type.EXPIRY
            and t.status == Task.Status.OPEN
            and document.kind == Document.Kind.DOCUMENT
        ),
        None,
    )
    return DocumentOut(
        **_summary_out(
            document, open_suggestions, open_duplicate_count, names, unread, reminder
        ).model_dump(),
        shared_user_ids=visibility.shared_user_ids(document),
        new_person_suggestions=[
            NewPersonSuggestionOut(
                id=row.id, name=row.name, quote=row.quote, confidence=str(row.confidence)
            )
            for row in new_persons.open_for(document)
        ],
        can_make_public=document.visibility == Document.Visibility.RESTRICTED
        and isinstance(actor, UUID)
        and visibility.can_make_public(document, actor),
        failure_code=document.failure_code,
        summary=document.summary,
        summary_by_ai=document.summary_by_ai,
        pages=[
            PageOut(index=page.index, ocr_text=page.ocr_text)
            for page in document.pages.order_by("index")
        ],
        proposals=[
            ProposalOut(
                field=e.field,
                group=e.group,
                value=e.value,
                quote=e.quote,
                confidence=f"{e.confidence:.3f}",
                needs_attention=e.needs_attention,
                provider=e.provider,
                model=e.model,
            )
            for e in document.extractions.order_by("group", "created_at", "id")
        ],
        tasks=[_task_out(task, flags[task.id], today, own.get(task.id)) for task in tasks],
        suggestions=[
            SuggestionOut(
                id=s.id,
                action=s.action,
                label_id=s.label_id,
                person_id=s.person_id,
                origin=s.origin,
                rule_name=s.rule_name,
                quote=s.quote,
                confidence=f"{s.confidence:.3f}",
                preselected=s.preselected,
            )
            for s in open_suggestions
        ],
    )


def _task_out(task: Task, flags: "TaskFlags", today: date, snoozed: date | None = None) -> TaskOut:
    return TaskOut(
        id=task.id,
        type=task.type,
        description=task.description,
        deadline=task.deadline,
        deadline_source=task.deadline_source,
        deadline_quote=task.deadline_quote,
        status=task.status,
        completed_at=task.completed_at,
        created_by_ai=task.created_by_ai,
        amount=amount_text(task.amount),
        term_quote=task.term_quote,
        assigned_to_name=task.assigned_to.name if task.assigned_to is not None else "",
        assigned_to_id=task.assigned_to_id,
        can_take=flags.can_take,
        can_hand_over=flags.can_hand_over,
        can_release=flags.can_release,
        waiting_until=task.waiting_until,
        wait_over=task.waiting_until is not None and task.waiting_until <= today,
        snoozed_until=snoozed,
    )


def read_page(*, membership: Membership, document_id: UUID, index: int) -> tuple[bytes, str]:
    """The decrypted page file and its content type."""
    workspace = membership.workspace
    document = get_document(membership=membership, document_id=document_id)
    page = DocumentPage.objects.filter(document=document, index=index).first()
    if page is None:
        raise ApiError(404, "page_not_found", _("We kunnen deze pagina niet vinden."))
    sealed = default_storage().read(page.storage_path)
    data = crypto.open_sealed(
        workspace_key(workspace.id), sealed, aad=page_aad(workspace.id, document.id, page.index)
    )
    return data, page.content_type


def retry_processing(*, workspace: Workspace, document_id: UUID) -> Document:
    """Queue a failed document for processing again: a letter in `failed`, or an own
    document that ended `done` with a `failure_code` (archive spec S 6.2 point 5)."""
    with transaction.atomic():
        _lock_own_membership(workspace)
        document = (
            Document.objects.select_for_update(no_key=True)
            .filter(LIVE, workspace=workspace, id=document_id)
            .first()
        )
        if document is None:
            raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
        # Never sent to the AI again by someone with emergency access only.
        visibility.require_editable(document)
        own_failed = (
            document.kind == Document.Kind.DOCUMENT
            and document.status == Document.Status.DONE
            and document.failure_code != ""
        )
        if document.status != Document.Status.FAILED and not own_failed:
            raise ApiError(
                409,
                "document_not_failed",
                _("Dit document wordt nog verwerkt. Probeer het zo opnieuw.")
                if document.status == Document.Status.PROCESSING
                else _("Dit document hoeft niet opnieuw verwerkt te worden."),
            )
        document.status = Document.Status.PROCESSING
        document.failure_code = ""
        document.save(update_fields=["status", "failure_code", "updated_at"])
        defer_processing(workspace_id=workspace.id, document_id=document.id)
    return document


def reopen_document(*, workspace: Workspace, user: User, document_id: UUID) -> Document:
    """Take the AI's confirmation back: the document returns to review.

    Only a `done` document the AI confirmed. The confirmed fields are emptied and the
    tasks the AI made that are still open are removed (a task somebody ticked off stays).
    Labels, addressees, proposals and suggestions stay, so the normal review follows.
    """
    with transaction.atomic():
        lock_own_membership(workspace.id, user.id)
        document = (
            Document.objects.select_for_update(no_key=True)
            .filter(LIVE, workspace=workspace, id=document_id)
            .first()
        )
        if document is None:
            raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
        visibility.require_editable(document)
        if document.status != Document.Status.DONE or not document.confirmed_by_ai:
            raise ApiError(
                409,
                "document_not_reopenable",
                _("Dit document kun je niet opnieuw controleren."),
            )
        document.status = Document.Status.NEEDS_REVIEW
        document.sender = ""
        document.document_type = ""
        document.summary = ""
        document.letter_date = None
        document.confirmed_at = None
        document.confirmed_organisation = None
        document.summary_by_ai = False
        document.confirmed_by_ai = False
        document.save()
        delete_tasks(
            Task.objects.filter(document=document, created_by_ai=True, status=Task.Status.OPEN)
        )
        organise.record_reopened(document, actor=user)
        # Back to review: the facts follow the tasks that stay; no status line (the
        # letter is "Nieuw" again, the line above says why).
        letter_sync.sync(document, previous_status=Document.Status.DONE)
    return document


def set_document_addressees(
    *, workspace: Workspace, user: User, document_id: UUID, person_ids: list[UUID]
) -> Document:
    """The document's addressees become exactly `person_ids`."""
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        with visibility.keep_own_access(document), visibility.addressee_scope(document):
            organise.set_addressees(document=document, person_ids=person_ids, actor=user)
    return document


def set_document_labels(
    *, workspace: Workspace, user: User, document_id: UUID, label_ids: list[UUID]
) -> Document:
    """The document's labels become exactly `label_ids`."""
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        organise.set_labels(document=document, label_ids=label_ids, actor=user)
    return document


def accept_suggestion(
    *, workspace: Workspace, user: User, document_id: UUID, suggestion_id: UUID
) -> Document:
    """Make the proposed change, as the user's choice; accepting again changes nothing."""
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        suggestion = _suggestion(document, suggestion_id)
        if suggestion.state == DocumentSuggestion.State.ACCEPTED:
            return document
        origin = DocumentEvent.Origin.SUGGESTION
        if suggestion.label is not None:
            organise.add_label(document, suggestion.label, actor=user, origin=origin)
        elif suggestion.person is not None:
            if suggestion.person.archived_at is not None:
                raise ApiError(
                    422,
                    "validation_error",
                    _("Een gearchiveerde persoon kun je niet aan een document toevoegen."),
                )
            with visibility.addressee_scope(document):
                organise.add_addressee(document, suggestion.person, actor=user, origin=origin)
        suggestion.state = DocumentSuggestion.State.ACCEPTED
        suggestion.save(update_fields=["state", "updated_at"])
    return document


def accept_new_person_suggestion(
    *, membership: Membership, actor: AuditActor, document_id: UUID, suggestion_id: UUID
) -> Document:
    """A member makes the person the AI proposed and puts the letter on them."""
    with transaction.atomic():
        document = _lock_for_organising(membership.workspace, document_id)
        new_persons.accept(document, suggestion_id, actor=actor, actor_role=membership.role)
    return document


def dismiss_new_person_suggestion(
    *, workspace: Workspace, document_id: UUID, suggestion_id: UUID
) -> Document:
    """Say no to a proposed new person; the name is not proposed on this letter again."""
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        new_persons.dismiss(document, suggestion_id)
    return document


def dismiss_suggestion(*, workspace: Workspace, document_id: UUID, suggestion_id: UUID) -> Document:
    """Say no to an open proposal; the document itself does not change."""
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        suggestion = _suggestion(document, suggestion_id)
        if suggestion.state == DocumentSuggestion.State.OPEN:
            suggestions.mark_dismissed(
                document, suggestion.action, suggestions.target_id_of(suggestion)
            )
    return document


def _suggestion(document: Document, suggestion_id: UUID) -> DocumentSuggestion:
    suggestion = (
        DocumentSuggestion.objects.select_for_update(of=("self",))
        .select_related("label", "person")
        .filter(document=document, id=suggestion_id)
        .first()
    )
    if suggestion is None:
        raise ApiError(404, "suggestion_not_found", _("We kunnen dit voorstel niet vinden."))
    return suggestion


def _lock_own_membership(workspace: Workspace) -> None:
    """The request's membership first (`apps.workspaces.locks`); the system has none."""
    actor = current_actor()
    if isinstance(actor, UUID):
        lock_own_membership(workspace.id, actor)


def _lock_for_organising(workspace: Workspace, document_id: UUID) -> Document:
    """The document, locked after the caller's membership (the lock order); refused while
    it is still being processed."""
    _lock_own_membership(workspace)
    document = (
        Document.objects.select_for_update(no_key=True)
        .filter(LIVE, workspace=workspace, id=document_id)
        .first()
    )
    if document is None:
        raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
    if document.status == Document.Status.PROCESSING:
        raise ApiError(
            409,
            "document_processing",
            _("Dit document wordt nog verwerkt. Probeer het zo opnieuw."),
        )
    visibility.require_editable(document)
    return document


def list_document_events(*, membership: Membership, document_id: UUID) -> list[DocumentEvent]:
    """The document's history, newest first.

    Annotated with `undone` (an undo event points at it), `still_linked` (its label or
    person is on the document now), `still_automatic` (that link is still marked
    automatic) and `superseded` (a later event concerns the same label or person), which
    `DocumentEventOut.can_undo` reads.

    A line never tells the member about a letter they may not read (the trash counted):
    a `duplicate_dismissed` line about such a letter is left out, and any other line
    (`merged`, `task_moved`, `trashed` by a merge) loses its `related_document_id`.
    """
    document = get_document(membership=membership, document_id=document_id)
    related_readable = models.Exists(
        visibility.readable_documents(membership).filter(id=models.OuterRef("related_document_id"))
    )
    label_linked = DocumentLabel.objects.filter(
        document_id=models.OuterRef("document_id"), label_id=models.OuterRef("label_id")
    )
    person_linked = DocumentAddressee.objects.filter(
        document_id=models.OuterRef("document_id"), person_id=models.OuterRef("person_id")
    )
    later = DocumentEvent.objects.filter(
        document_id=models.OuterRef("document_id"), created_at__gt=models.OuterRef("created_at")
    )
    found: list[DocumentEvent] = list(
        DocumentEvent.objects.filter(document=document)
        .select_related("actor")
        .annotate(related_readable=related_readable)
        .exclude(
            kind=DocumentEvent.Kind.DUPLICATE_DISMISSED,
            related_document_id__isnull=False,
            related_readable=False,
        )
        .annotate(
            undone=models.Exists(DocumentEvent.objects.filter(undoes=models.OuterRef("pk"))),
            still_linked=models.Exists(label_linked) | models.Exists(person_linked),
            still_automatic=models.Exists(label_linked.filter(automatic=True))
            | models.Exists(person_linked.filter(automatic=True)),
            superseded=models.Exists(later.filter(label_id=models.OuterRef("label_id")))
            | models.Exists(later.filter(person_id=models.OuterRef("person_id"))),
        )
        .order_by("-created_at", "-id")
    )
    for event in found:
        if not getattr(event, "related_readable", False):
            event.related_document_id = None
    return found


def undo_document_event(
    *, workspace: Workspace, user: User, document_id: UUID, event_id: UUID
) -> Document:
    """Reverse a change a rule made; the rule will not make it again on this document."""
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        with visibility.keep_own_access(document), visibility.addressee_scope(document):
            organise.undo_event(document=document, event_id=event_id, actor=user)
    return document


def set_document_visibility(
    *,
    workspace: Workspace,
    user: User,
    actor: AuditActor,
    document_id: UUID,
    visibility_value: str,
    shared_user_ids: list[UUID],
) -> DocumentVisibilityOut:
    """ "Privé maken", "Voor iedereen zichtbaar maken" and "Delen met" (PB T17, R44).

    `restricted`: a letter that is not private becomes private with exactly these shares
    besides the scanner and the caller (an earlier share not sent again ends); a private
    one gets exactly these shares. `workspace`: a
    private letter becomes visible to everyone (403 `cannot_make_public` for someone not
    addressed while an addressee reads it), then the shares become exactly these (also on
    a letter that is not private). Anything else 422. The answer says whether the caller
    still reads it (they may remove their own share)."""
    if visibility_value not in Document.Visibility.values:
        raise ApiError(422, "validation_error", _("Kies privé of zichtbaar voor iedereen."))
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        if visibility_value == Document.Visibility.RESTRICTED:
            if document.visibility != Document.Visibility.RESTRICTED:
                made = visibility.make_private(
                    document, actor=actor, origin="user", share_user_ids=shared_user_ids
                )
                if made == "made_private":
                    # An earlier share not sent again ends here: nobody stays a reader
                    # silently (security review of PR #40). The scanner and the caller
                    # keep the shares `make_private` gave them.
                    keep = set(shared_user_ids) | {user.id}
                    if document.scanned_by_id is not None:
                        keep.add(document.scanned_by_id)
                    members = set(
                        Membership.objects.filter(
                            workspace=workspace, user_id__in=keep
                        ).values_list("user_id", flat=True)
                    )
                    visibility.set_shares(document, user_ids=keep & members, actor=actor)
            else:
                visibility.set_shares(document, user_ids=shared_user_ids, actor=actor)
        else:
            visibility.make_public(document, actor=actor, user_id=user.id)
            visibility.set_shares(document, user_ids=shared_user_ids, actor=actor)
        readable = visibility.can_read(document.id)
        return DocumentVisibilityOut(
            visibility=visibility.visibility_of(document.id),
            reader_names=visibility.reader_names([document]).get(document.id, [])
            if readable
            else [],
            shared_user_ids=visibility.shared_user_ids(document) if readable else [],
            can_read=readable,
        )
