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

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.common.errors import ApiError
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
from apps.documents.models import Document, DuplicateCandidate, Extraction
from apps.documents.visibility import visible_documents
from apps.workspaces.models import Membership

# A letter is compared with the live letters that arrived at most this long before it.
WINDOW: Final = timedelta(days=180)
# The new letter's longest lexemes (no digits, not `bsn`) that find candidates.
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
    letters = visible_documents(membership).values("id")
    return DuplicateCandidate.objects.filter(
        workspace_id=membership.workspace_id,
        state=DuplicateCandidate.State.OPEN,
        first_id__in=letters,
        second_id__in=letters,
    )


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


# The new letter's `TERMS` longest lexemes (letters only: no numbers, and not the masked
# BSN) joined with OR find the candidates in the search index; the best `MAX_CANDIDATES`
# by `ts_rank_cd` are scored with `pg_trgm` in the same statement. A pair that has a row,
# in any state, is never a candidate again.
_CANDIDATES_SQL: Final = """
WITH terms AS (
    SELECT t.lexeme
    FROM unnest(to_tsvector('dutch', %(text)s)) AS t
    WHERE t.lexeme ~ '^[[:alpha:]]+$' AND t.lexeme <> 'bsn'
    ORDER BY length(t.lexeme) DESC, t.lexeme
    LIMIT %(terms)s
),
query AS (
    SELECT string_agg(terms.lexeme, ' | ')::tsquery AS q FROM terms
),
candidates AS (
    SELECT d.id, ts_rank_cd(d.search_vector, query.q) AS rank
    FROM documents_document AS d, query
    WHERE d.workspace_id = %(workspace)s
      AND d.id <> %(id)s
      AND d.trashed_at IS NULL
      AND d.status <> 'processing'
      AND d.created_at >= %(since)s
      AND d.search_vector @@ query.q
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
        with connection.cursor() as cursor:
            cursor.execute(
                _CANDIDATES_SQL,
                {
                    "text": document.ocr_text,
                    "terms": TERMS,
                    "workspace": document.workspace_id,
                    "id": document.id,
                    "since": document.created_at - WINDOW,
                    "limit": MAX_CANDIDATES,
                },
            )
            scored: dict[UUID, Decimal] = {
                row[0]: _score(float(row[1])) for row in cursor.fetchall()
            }
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
