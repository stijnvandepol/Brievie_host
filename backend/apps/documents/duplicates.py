"""Duplicate letters: two scans of one letter, proposed as a pair (spec
`docs/superpowers/specs/2026-11-20-duplicates-design.md`).

`visible_pairs` is the seam of every member-facing read and change of a pair: the list,
a pair's detail, the counts, merge and dismiss. It stands on `visibility.visible_documents`,
so a pair is shown only to a member who sees both letters, never with a letter in the
trash, and only as that member (the seam raises otherwise). A pair the member cannot see
in full answers as a missing one (`duplicate_not_found`).

Detection (`detect`), the autopilot's rail (`has_open_pair`) and the re-check after a
person's confirm (`recheck`) run as the system, over every live letter of the workspace,
only because their callers (`pipeline.steps`, `review`) already do: this module never
switches to the system itself. They write only pair rows and log ids and counts, never a
word of a letter.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.documents import organise, review, visibility
from apps.documents.duplicates_facts import (
    Fact,
    LetterFacts,
    Verdict,
    decide,
    number_overlap,
    numbers_in,
    payment_reference_in,
    strong,
)
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DuplicateCandidate,
    Extraction,
)
from apps.documents.visibility import visible_documents
from apps.tasks import services as task_services
from apps.tasks.models import Task
from apps.workspaces.locks import lock_own_membership
from apps.workspaces.models import Membership

# A letter is compared with the live letters that arrived at most this long before it.
WINDOW: Final = timedelta(days=180)
# The most terms (long digit runs, else words; never `bsn`) that find candidates.
TERMS: Final = 16
# The best-ranked candidates that are scored.
MAX_CANDIDATES: Final = 20
# The most open pairs one arrival gets.
MAX_OPEN_PER_LETTER: Final = 3


def duplicate_not_found() -> ApiError:
    return ApiError(404, "duplicate_not_found", _("We kunnen deze dubbele brieven niet vinden."))


def ordered(a: UUID, b: UUID) -> tuple[UUID, UUID]:
    """The two ids in the order a pair stores them (`first_id < second_id`)."""
    return (a, b) if a < b else (b, a)


def visible_pairs(membership: Membership) -> QuerySet[DuplicateCandidate]:
    """The open pairs whose two letters `membership`'s user both sees (trash left out)."""
    return _seen_pairs(membership).filter(state=DuplicateCandidate.State.OPEN)


def _seen_pairs(membership: Membership) -> QuerySet[DuplicateCandidate]:
    """The pairs in any state whose two letters the member sees: `visible_pairs` before
    its state filter, so a decided pair answers `duplicate_not_open` and never tells
    anything about a letter the member cannot see."""
    letters = visible_documents(membership).values("id")
    return DuplicateCandidate.objects.filter(
        workspace_id=membership.workspace_id,
        first_id__in=letters,
        second_id__in=letters,
    )


def duplicate_not_open() -> ApiError:
    return ApiError(409, "duplicate_not_open", _("Over deze twee brieven is al gekozen."))


def visibility_differs() -> ApiError:
    return ApiError(
        409,
        "duplicate_visibility_differs",
        _(
            "Deze twee brieven zijn niet voor dezelfde mensen zichtbaar. Kies 'Het zijn twee "
            "brieven', of laat ze eerst voor dezelfde mensen zichtbaar zijn."
        ),
    )


def viewers_differ(a: Document, b: Document) -> bool:
    """Whether two letters are seen by other people: one is private and the other not, or
    the members who see them (the policy's own condition, as the system) differ."""
    if (a.visibility == Document.Visibility.RESTRICTED) != (
        b.visibility == Document.Visibility.RESTRICTED
    ):
        return True
    seen = visibility.viewers([a.id, b.id])
    return seen.get(a.id) != seen.get(b.id)


@dataclass(frozen=True)
class TaskTwin:
    """A done task on one letter with an equal open task on the other."""

    done_document_id: UUID
    open_document_id: UUID
    type: str
    description: str


def task_twins(first: Document, second: Document) -> list[TaskTwin]:
    """Every done task of either letter whose equal (type, description, deadline, source)
    is open on the other one."""
    tasks = list(
        Task.objects.filter(document_id__in=[first.id, second.id]).order_by("created_at", "id")
    )
    return [
        TaskTwin(done.document_id, task.document_id, task.type, task.description)
        for done in tasks
        if done.status == Task.Status.DONE and done.document_id is not None
        for task in tasks
        if task.status == Task.Status.OPEN
        and task.document_id is not None
        and task.document_id != done.document_id
        and review.task_key(task) == review.task_key(done)
    ]


def merge(
    *,
    membership: Membership,
    user: User,
    actor: AuditActor,
    candidate_id: UUID,
    keep_id: UUID,
    complete_twin_tasks: bool = True,
) -> Document:
    """The two letters are one: the kept letter gets the other's labels, addressees and
    open tasks, and the other goes to the trash (30 days to take it back).

    Runs as the member. Refused when the pair is not open, `keep_id` is not one of it,
    either letter is still processing, or the letters are not seen by the same people
    (before uniting, and again after: a merge never changes who sees a letter). Labels
    and addressees are added through `organise` (origin `merge`, not automatic, not
    undoable); an open task of the other letter without an equal on the kept one moves;
    a done task completes its open twin on the kept letter only with
    `complete_twin_tasks`. A refusal anywhere, the trash's retention hook included, rolls
    the whole merge back.
    """
    with transaction.atomic():
        candidate, letters = _lock_pair(membership, user, candidate_id)
        if keep_id not in letters:
            raise ApiError(422, "validation_error", _("Kies een van de twee brieven om te houden."))
        if any(d.status == Document.Status.PROCESSING for d in letters.values()):
            raise ApiError(
                409,
                "document_processing",
                _("Dit document wordt nog verwerkt. Probeer het zo opnieuw."),
            )
        keep = letters[keep_id]
        other = next(d for i, d in letters.items() if i != keep_id)
        if viewers_differ(keep, other):
            raise visibility_differs()
        before = visibility.viewers([keep.id])
        with visibility.addressee_scope(keep):
            _unite(keep, other, user)
        # The scope's end synced the addressee list and the readers.
        if visibility.viewers([keep.id]) != before:
            raise visibility_differs()
        _merge_tasks(keep, other, user, complete_twin_tasks=complete_twin_tasks)
        # Imported here: the trash reaches the job queues, whose pipeline jobs import this
        # module (detection), so a module-level import would be circular.
        from apps.documents import trash

        trash.move_to_trash(
            other, user=user, actor=actor, reason="duplicate", related_document=keep
        )
        organise.record_merged(keep, actor=user, related_document=other)
        _decide(candidate, DuplicateCandidate.State.MERGED, user, kept=keep)
    return keep


def dismiss(*, membership: Membership, user: User, candidate_id: UUID) -> None:
    """The two letters are two letters: the pair is never proposed again. History on both;
    no audit row."""
    with transaction.atomic():
        candidate, letters = _lock_pair(membership, user, candidate_id)
        first, second = letters[candidate.first_id], letters[candidate.second_id]
        _decide(candidate, DuplicateCandidate.State.DISMISSED, user)
        organise.record_duplicate_dismissed(first, actor=user, related_document=second)
        organise.record_duplicate_dismissed(second, actor=user, related_document=first)


def _lock_pair(
    membership: Membership, user: User, candidate_id: UUID
) -> tuple[DuplicateCandidate, dict[UUID, Document]]:
    """The lock order: the member's own membership, both letters in id order (through the
    seam), then the pair. A pair the member cannot see in full answers as a missing one;
    a decided one as `duplicate_not_open`."""
    lock_own_membership(membership.workspace_id, user.id)
    ids = _seen_pairs(membership).filter(id=candidate_id).values_list("first_id", "second_id")
    found = ids.first()
    if found is None:
        raise duplicate_not_found()
    letters = {
        d.id: d
        for d in visible_documents(membership)
        .select_for_update(no_key=True)
        .filter(id__in=found)
        .order_by("id")
    }
    candidate = _seen_pairs(membership).select_for_update().filter(id=candidate_id).first()
    if len(letters) != 2 or candidate is None:
        raise duplicate_not_found()
    if candidate.state != DuplicateCandidate.State.OPEN:
        raise duplicate_not_open()
    return candidate, letters


def _unite(keep: Document, other: Document, user: User) -> None:
    """Every label and addressee of `other` that `keep` lacks, through `organise`."""
    have_labels = set(
        DocumentLabel.objects.filter(document=keep).values_list("label_id", flat=True)
    )
    for link in (
        DocumentLabel.objects.filter(document=other)
        .exclude(label_id__in=have_labels)
        .select_related("label")
        .order_by("created_at", "id")
    ):
        organise.add_label(keep, link.label, actor=user, origin=DocumentEvent.Origin.MERGE)
    have_persons = set(
        DocumentAddressee.objects.filter(document=keep).values_list("person_id", flat=True)
    )
    for person_link in (
        DocumentAddressee.objects.filter(document=other)
        .exclude(person_id__in=have_persons)
        .select_related("person")
        .order_by("created_at", "id")
    ):
        organise.add_addressee(
            keep, person_link.person, actor=user, origin=DocumentEvent.Origin.MERGE
        )


def _merge_tasks(keep: Document, other: Document, user: User, *, complete_twin_tasks: bool) -> None:
    """An open task of `other` without an equal on `keep` moves to `keep`; a done one
    completes its open equal on `keep` when asked (only a task that was on `keep` before
    the merge: the twins `task_twins` showed). Every other task stays with `other`."""
    on_keep: dict[tuple[object, ...], list[Task]] = defaultdict(list)
    for task in Task.objects.select_for_update().filter(document=keep).order_by("created_at", "id"):
        on_keep[review.task_key(task)].append(task)
    kept_before = {task.id for tasks in on_keep.values() for task in tasks}
    for task in (
        Task.objects.select_for_update().filter(document=other).order_by("created_at", "id")
    ):
        equals = on_keep[review.task_key(task)]
        if task.status == Task.Status.OPEN and not equals:
            task.document = keep
            task.save(update_fields=["document", "updated_at"])
            equals.append(task)
            organise.record_task_moved(keep, actor=user, related_document=other)
            organise.record_task_moved(other, actor=user, related_document=keep)
        elif task.status == Task.Status.DONE and complete_twin_tasks:
            for twin in equals:
                if twin.status != Task.Status.OPEN or twin.id not in kept_before:
                    continue
                # The one completion path (event `task_completed`, origin `merge`).
                task_services.complete_task(
                    workspace=keep.workspace,
                    user=user,
                    task_id=twin.id,
                    origin=DocumentEvent.Origin.MERGE,
                )
                twin.status = Task.Status.DONE


def _decide(
    candidate: DuplicateCandidate, state: str, user: User, *, kept: Document | None = None
) -> None:
    candidate.state = state
    candidate.kept = kept
    candidate.decided_by = user
    candidate.decided_at = timezone.now()
    candidate.save(update_fields=["state", "kept", "decided_by", "decided_at", "updated_at"])


def facts_of(document: Document) -> LetterFacts:
    """What `document` says, for the comparison: the confirmed values once a person or the
    AI confirmed it, otherwise the strongest extraction per field."""
    return _facts(document, list(document.extractions.all()))


def _facts(document: Document, extractions: Sequence[Extraction]) -> LetterFacts:
    def extracted(field: str) -> Fact | None:
        best = max(
            (e for e in extractions if e.field == field and e.value.strip()),
            key=lambda e: e.confidence,
            default=None,
        )
        return None if best is None else Fact(best.value, strong(False, best.confidence))

    def confirmed_or_extracted(value: str, field: str) -> Fact | None:
        if document.confirmed_at is not None:
            return Fact(value, strong=True) if value else None
        return extracted(field)

    reference = payment_reference_in(document.ocr_text)
    return LetterFacts(
        text=document.ocr_text,
        numbers=numbers_in(document.ocr_text),
        payment_reference=None if reference is None else Fact(reference, strong=True),
        sender=confirmed_or_extracted(document.sender, Extraction.Field.SENDER),
        letter_date=confirmed_or_extracted(
            document.letter_date.isoformat() if document.letter_date is not None else "",
            Extraction.Field.LETTER_DATE,
        ),
        document_type=confirmed_or_extracted(
            document.document_type, Extraction.Field.DOCUMENT_TYPE
        ),
        amount=extracted(Extraction.Field.INVOICE_AMOUNT_INCL),
        invoice_number=extracted(Extraction.Field.INVOICE_NUMBER),
        page_count=document.page_count,
    )


# The new letter's lexemes that can find it again (no table read): its long digit runs
# (client, invoice, meter numbers, references), or, when it has none, its words. The
# masked BSN (`bsn`) is never a term.
_LEXEMES_SQL: Final = """
SELECT t.lexeme
FROM unnest(to_tsvector('dutch', %(text)s)) AS t
WHERE t.lexeme <> 'bsn' AND (t.lexeme ~ '^[0-9]{6,}$' OR t.lexeme ~ '^[[:alpha:]]+$')
"""

# The candidates: letters of the pool that hold a term (the search index, `search_vector`
# GIN), the best `MAX_CANDIDATES` by `ts_rank_cd`, scored with `pg_trgm` in the same
# statement. The query arrives built (a constant, not computed in the statement), so the
# planner drives the scan through the index. A pair that has a row, in any state, is never
# a candidate again.
_CANDIDATES_SQL: Final = """
WITH candidates AS (
    SELECT d.id, ts_rank_cd(d.search_vector, %(query)s::tsquery) AS rank
    FROM documents_document AS d
    WHERE d.workspace_id = %(workspace)s
      AND d.id <> %(id)s
      AND d.trashed_at IS NULL
      AND d.status <> 'processing'
      AND d.created_at >= %(since)s
      AND d.search_vector @@ %(query)s::tsquery
      AND NOT EXISTS (
          SELECT 1 FROM documents_duplicatecandidate AS p
          WHERE p.first_id = LEAST(d.id, %(id)s) AND p.second_id = GREATEST(d.id, %(id)s)
      )
    ORDER BY rank DESC, d.id
    LIMIT %(limit)s
)
SELECT c.id, similarity(%(text)s, d.ocr_text)
FROM candidates AS c
JOIN documents_document AS d ON d.id = c.id
"""

_NUMBER: Final = re.compile(r"[0-9]{6,}")
_DIGIT: Final = re.compile(r"[0-9]")


def terms_of(lexemes: Iterable[str]) -> list[str]:
    """The `TERMS` terms that find a letter's candidates: its longest digit runs of 6 or
    more digits, or, when it has none, its longest words (longest first, then by spelling).

    A number a letter carries (client, invoice, meter number, payment reference) finds the
    same letter scanned again; template words find every letter of the sender (measured:
    the original was found for 34% of the rescans with words, 99% with numbers).
    """
    unique = set(lexemes)
    numbers = [lexeme for lexeme in unique if _NUMBER.fullmatch(lexeme)]
    chosen = numbers or [lexeme for lexeme in unique if not _DIGIT.search(lexeme)]
    return sorted(chosen, key=lambda lexeme: (-len(lexeme), lexeme))[:TERMS]


def candidate_query(text: str) -> str | None:
    """The OR `tsquery` (as text) of a letter's terms, or None when it has none."""
    with connection.cursor() as cursor:
        cursor.execute(_LEXEMES_SQL, {"text": text})
        terms = terms_of(row[0] for row in cursor.fetchall())
    if not terms:
        return None
    # Digits or letters only (the lexeme filter), so quoting cannot break out.
    return " | ".join(f"'{term}'" for term in terms)


def candidates(
    *, text: str, workspace_id: UUID, document_id: UUID, since: datetime
) -> dict[UUID, Decimal]:
    """The candidates of a letter with `text` in the pool, with their text similarity."""
    query = candidate_query(text)
    if query is None:
        return {}
    with connection.cursor() as cursor:
        cursor.execute(
            _CANDIDATES_SQL,
            {
                "text": text,
                "query": query,
                "workspace": workspace_id,
                "id": document_id,
                "since": since,
                "limit": MAX_CANDIDATES,
            },
        )
        return {row[0]: _score(float(row[1])) for row in cursor.fetchall()}


def detect(document: Document) -> int:
    """Store the pairs of a newly arrived letter with the letters it probably repeats; how
    many rows were written.

    The caller holds the letter locked while it is `processing`, and runs as the system:
    the pool is every live letter of the workspace, private ones included (nothing is
    shown here; `visible_pairs` decides who sees a pair). At most `MAX_OPEN_PER_LETTER`
    open pairs, the most similar first.
    """
    with transaction.atomic():
        room = MAX_OPEN_PER_LETTER - _open_of(document.id).count()
        if room <= 0:
            return 0
        scored = candidates(
            text=document.ocr_text,
            workspace_id=document.workspace_id,
            document_id=document.id,
            since=document.created_at - WINDOW,
        )
        if not scored:
            return 0
        others = Document.objects.filter(id__in=scored).only(
            "id",
            "ocr_text",
            "sender",
            "document_type",
            "letter_date",
            "confirmed_at",
            "page_count",
        )
        extractions = _extractions_of(scored)
        new = facts_of(document)
        likely: list[tuple[Decimal, Decimal, UUID]] = []
        for other in others:
            facts = _facts(other, extractions[other.id])
            overlap = number_overlap(new.numbers, facts.numbers)
            similarity = scored[other.id]
            verdict = decide(new, facts, text_similarity=similarity, number_overlap=overlap)
            if verdict == Verdict.LIKELY:
                likely.append((similarity, overlap, other.id))
        likely.sort(key=lambda found: (-found[0], -found[1], found[2]))
        rows = []
        for similarity, overlap, other_id in likely[:room]:
            first, second = ordered(document.id, other_id)
            rows.append(
                DuplicateCandidate(
                    workspace_id=document.workspace_id,
                    first_id=first,
                    second_id=second,
                    text_similarity=similarity,
                    number_overlap=overlap,
                )
            )
        DuplicateCandidate.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)


def has_open_pair(document_id: UUID) -> bool:
    """Whether the letter has an open pair whose other letter is live (not in the trash),
    under whatever actor the caller holds (the pipeline: the system)."""
    return (
        DuplicateCandidate.objects.filter(state=DuplicateCandidate.State.OPEN)
        .filter(
            Q(first_id=document_id, second__trashed_at__isnull=True)
            | Q(second_id=document_id, first__trashed_at__isnull=True)
        )
        .exists()
    )


def recheck(document: Document) -> int:
    """Decide the open pairs of a just-confirmed letter again, with its confirmed facts and
    the stored scores; a guard that now fires withdraws the pair. How many were withdrawn.

    The caller (`review`) runs it as the system: the confirming member may not see the
    other letter. No new search.
    """
    with transaction.atomic():
        rows = list(_open_of(document.id).select_for_update().order_by("id"))
        if not rows:
            return 0
        other_ids = [row.second_id if row.first_id == document.id else row.first_id for row in rows]
        others = Document.objects.in_bulk(other_ids)
        extractions = _extractions_of(other_ids)
        new = facts_of(document)
        withdrawn = []
        for row, other_id in zip(rows, other_ids, strict=True):
            other = others.get(other_id)
            if other is None:
                continue
            verdict = decide(
                new,
                _facts(other, extractions[other_id]),
                text_similarity=row.text_similarity,
                number_overlap=row.number_overlap,
            )
            if verdict == Verdict.NONE:
                withdrawn.append(row.id)
        if withdrawn:
            DuplicateCandidate.objects.filter(id__in=withdrawn).update(
                state=DuplicateCandidate.State.WITHDRAWN, updated_at=timezone.now()
            )
        return len(withdrawn)


def _open_of(document_id: UUID) -> QuerySet[DuplicateCandidate]:
    return DuplicateCandidate.objects.filter(
        Q(first_id=document_id) | Q(second_id=document_id),
        state=DuplicateCandidate.State.OPEN,
    )


def _extractions_of(document_ids: Iterable[UUID]) -> defaultdict[UUID, list[Extraction]]:
    found: defaultdict[UUID, list[Extraction]] = defaultdict(list)
    for extraction in Extraction.objects.filter(document_id__in=list(document_ids)):
        found[extraction.document_id].append(extraction)
    return found


def _score(value: float) -> Decimal:
    """A `pg_trgm` similarity as stored: 3 decimals, 0 to 1."""
    score = Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return min(max(score, Decimal("0")), Decimal("1"))
