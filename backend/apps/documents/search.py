"""Searching letters inside one workspace.

Design: docs/superpowers/specs/2026-11-20-search-design.md.

Two steps. The exact step uses PostgreSQL full-text search in Dutch (`search_vector`:
sender, summary, text), every word as a word beginning, or the names of the letter's
labels and persons. Only when that finds nothing does the fallback run: `pg_trgm` word
similarity over sender, summary and text, for typos. Every query starts from
`visibility.visible_documents`, so a hit is always a letter the member may see.

The term is a name or a number somebody looks for: nothing here logs it, audits it or
puts it in an error message, and it only ever reaches SQL as a bound parameter.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from uuid import UUID

from django.contrib.postgres.lookups import TrigramWordSimilar
from django.contrib.postgres.search import (
    SearchHeadline,
    SearchQuery,
    SearchRank,
    TrigramWordSimilarity,
)
from django.db import connection, models, transaction
from django.db.models.functions import Cast, Coalesce, Greatest, Left, Replace, TruncDate
from django.utils.translation import gettext as _

from apps.common.errors import ApiError
from apps.common.privacy import MASK, mask_bsns
from apps.documents import visibility
from apps.documents.models import Document, DocumentAddressee, DocumentLabel
from apps.documents.schemas import DocumentSearchHitOut, DocumentSearchOut, SnippetPartOut
from apps.documents.services import (
    INBOX_STATES,
    SUGGESTIONS,
    _has_addressee,
    _in_state,
    document_summary_out,
)
from apps.workspaces.models import Membership

MAX_TERM = 200
MAX_WORDS = 8
MIN_TERM = 2
MAX_OFFSET = 1000
# Added to the full-text rank when every word is in a label or person name.
NAME_BOOST = 1.0
# pg_trgm word similarity a word needs with the sender, summary or text in the fallback.
FUZZY_THRESHOLD = 0.45
_THRESHOLD_SETTING = "pg_trgm.word_similarity_threshold"
# Python's similarity a word of the fragment needs to be marked in the fallback.
FUZZY_MARK_RATIO = 0.7
SNIPPET_MAX = 240
SUMMARY_HEAD = 160
# Markers ts_headline puts around a hit: control characters, which stored text never holds.
START, STOP = "\x02", "\x03"

_MASK_LITERAL = re.compile(re.escape(MASK), re.IGNORECASE)
_WORD_CHARS = frozenset("-'.")
# A word of a fragment: letters and digits, joined by - ' or . ("e-mail", "o'brien").
_FRAGMENT_WORD = re.compile(r"[^\W_]+(?:[-'.][^\W_]+)*")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class SearchFilters:
    """All optional; together they all apply."""

    document_type: str = ""
    label_id: UUID | None = None
    person_id: UUID | None = None
    inbox_state: str = ""
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True)
class SnippetPart:
    text: str
    hit: bool


@dataclass(frozen=True)
class SearchHit:
    document: Document
    snippet: list[SnippetPart]


@dataclass(frozen=True)
class SearchResult:
    hits: list[SearchHit] = field(default_factory=list)
    # The offset of the next page; None when there is none.
    next_offset: int | None = None
    # The hits are approximate (the typo fallback).
    fuzzy: bool = False
    # A BSN was taken out of the term before searching.
    bsn_removed: bool = False


def clean_term(q: str) -> tuple[str, bool]:
    """The term with collapsed spaces, at most `MAX_TERM` characters, without BSNs and
    without the literal mask; and whether a BSN was removed."""
    text = " ".join(q.split())[:MAX_TERM]
    masked = mask_bsns(text)
    removed = masked.count(MASK) > text.count(MASK)
    return " ".join(_MASK_LITERAL.sub(" ", masked).split()), removed


def search_words(term: str) -> list[str]:
    """At most `MAX_WORDS` words, lower-cased, with only letters, digits, - ' and . kept;
    a word without a letter or digit is dropped."""
    words: list[str] = []
    for raw in term.split():
        word = "".join(c for c in raw if c.isalnum() or c in _WORD_CHARS).lower()
        if any(c.isalnum() for c in word):
            words.append(word)
    return words[:MAX_WORDS]


def _tsquery(words: list[str]) -> SearchQuery:
    """Every word as a quoted lexeme with a prefix match, all required.

    Quoting (with ' doubled) means no input can be read as tsquery syntax; the string is
    a bound parameter of `to_tsquery`, never part of the SQL text.
    """
    lexemes = " & ".join("'{}':*".format(word.replace("'", "''")) for word in words)
    return SearchQuery(lexemes, search_type="raw", config="dutch")


def _validate(filters: SearchFilters) -> None:
    if filters.inbox_state and filters.inbox_state not in INBOX_STATES:
        raise ApiError(422, "validation_error", _("Deze stand van de inbox kennen we niet."))
    if filters.document_type and filters.document_type not in Document.Type.values:
        raise ApiError(422, "validation_error", _("Deze soort brief kennen we niet."))
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise ApiError(422, "validation_error", _("De begindatum moet vóór de einddatum liggen."))


def _filtered(membership: Membership, filters: SearchFilters) -> "models.QuerySet[Document]":
    documents = (
        visibility.visible_documents(membership)
        .defer("ocr_text")
        .annotate(
            has_addressee=_has_addressee(),
            # Enough of the text for a fragment when the hit is not in the text.
            text_head=Left("ocr_text", SUMMARY_HEAD),
        )
    )
    if filters.document_type:
        documents = documents.filter(document_type=filters.document_type)
    if filters.inbox_state:
        documents = documents.filter(_in_state(filters.inbox_state))
    if filters.label_id is not None:
        documents = documents.filter(
            models.Exists(
                DocumentLabel.objects.filter(
                    document=models.OuterRef("pk"), label_id=filters.label_id
                )
            )
        )
    if filters.person_id is not None:
        documents = documents.filter(
            models.Exists(
                DocumentAddressee.objects.filter(
                    document=models.OuterRef("pk"), person_id=filters.person_id
                )
            )
        )
    if filters.date_from or filters.date_to:
        # A letter without a date falls under its upload date.
        documents = documents.annotate(filed_on=Coalesce("letter_date", TruncDate("created_at")))
        if filters.date_from:
            documents = documents.filter(filed_on__gte=filters.date_from)
        if filters.date_to:
            documents = documents.filter(filed_on__lte=filters.date_to)
    return documents


def _name_match(words: list[str]) -> models.Q:
    """Every word is part of the name of one of the letter's labels, or of the name or a
    name variant of one of its persons (archived ones too)."""
    match = models.Q()
    for word in words:
        label = DocumentLabel.objects.filter(
            document=models.OuterRef("pk"), label__name__icontains=word
        )
        person = (
            DocumentAddressee.objects.filter(document=models.OuterRef("pk"))
            .annotate(variants=Cast("person__name_variants", models.TextField()))
            .filter(
                models.Q(person__display_name__icontains=word) | models.Q(variants__icontains=word)
            )
        )
        match &= models.Exists(label) | models.Exists(person)
    return match


def _page(
    documents: "models.QuerySet[Document]", limit: int, offset: int
) -> tuple[list[Document], int | None]:
    rows = list(
        documents.prefetch_related("addressee_links", "label_links", SUGGESTIONS)[
            offset : offset + limit + 1
        ]
    )
    more = len(rows) > limit and offset + limit <= MAX_OFFSET
    return rows[:limit], (offset + limit if more else None)


def _exact(
    documents: "models.QuerySet[Document]", words: list[str], query: SearchQuery
) -> "models.QuerySet[Document]":
    names = _name_match(words)
    return (
        documents.filter(models.Q(search_vector=query) | names)
        .annotate(
            rank=SearchRank(models.F("search_vector"), query, cover_density=True)
            + models.Case(
                models.When(names, then=models.Value(NAME_BOOST)),
                default=models.Value(0.0),
                output_field=models.FloatField(),
            )
        )
        .order_by("-rank", "-created_at", "-id")
    )


def _fuzzy(documents: "models.QuerySet[Document]", words: list[str]) -> "models.QuerySet[Document]":
    """Every word word-similar to the sender, summary or text (`%>` uses the trigram
    indexes; the threshold is set for the query by `_similarity_threshold`)."""
    match = models.Q()
    score: models.Expression = models.Value(0.0)
    for word in words:
        value = models.Value(word, output_field=models.TextField())
        match &= (
            models.Q(TrigramWordSimilar(models.F("sender"), value))
            | models.Q(TrigramWordSimilar(models.F("summary"), value))
            | models.Q(TrigramWordSimilar(models.F("ocr_text"), value))
        )
        score = score + Greatest(
            TrigramWordSimilarity(word, "sender"),
            TrigramWordSimilarity(word, "summary"),
            TrigramWordSimilarity(word, "ocr_text"),
        )
    return (
        documents.filter(match)
        .annotate(similarity=models.ExpressionWrapper(score, output_field=models.FloatField()))
        .order_by("-similarity", "-created_at", "-id")
    )


def _fuzzy_page(
    documents: "models.QuerySet[Document]", words: list[str], limit: int, offset: int
) -> tuple[list[Document], int | None]:
    with transaction.atomic(), connection.cursor() as cursor:
        # Transaction-local: the operator `%>` compares with this setting.
        cursor.execute(
            "SELECT set_config(%s, %s, true)", [_THRESHOLD_SETTING, str(FUZZY_THRESHOLD)]
        )
        page = _page(_fuzzy(documents, words), limit, offset)
        cursor.execute("SET LOCAL pg_trgm.word_similarity_threshold TO DEFAULT")
    return page


def search_documents(
    membership: Membership, *, q: str, filters: SearchFilters, limit: int, offset: int
) -> SearchResult:
    """One page of the letters the member sees that match `q`, best first.

    Needs the workspace's RLS context. Filters are checked first (422 without the term).
    """
    _validate(filters)
    term, bsn_removed = clean_term(q)
    words = search_words(term)
    if len(term) < MIN_TERM or not words:
        return SearchResult(bsn_removed=bsn_removed)
    query = _tsquery(words)
    documents = _filtered(membership, filters)
    exact = _exact(documents, words, query)
    rows, next_offset = _page(exact, limit, offset)
    fuzzy = not rows and (offset == 0 or not exact.exists())
    if fuzzy:
        rows, next_offset = _fuzzy_page(documents, words, limit, offset)
    snippets = _snippets(membership, rows, words, query, fuzzy=fuzzy)
    return SearchResult(
        hits=[SearchHit(document=d, snippet=snippets[d.id]) for d in rows],
        next_offset=next_offset,
        fuzzy=fuzzy and bool(rows),
        bsn_removed=bsn_removed,
    )


# --- fragments ----------------------------------------------------------------------------


def _without_markers(text: str) -> str:
    return text.replace(START, "").replace(STOP, "")


def _tidy(parts: list[SnippetPart]) -> list[SnippetPart]:
    """Whitespace collapsed, adjacent parts of the same kind merged, empty parts dropped,
    at most `SNIPPET_MAX` characters in all."""
    merged: list[SnippetPart] = []
    for part in parts:
        text = _SPACE.sub(" ", part.text)
        if not text:
            continue
        if merged and merged[-1].hit == part.hit:
            merged[-1] = SnippetPart(merged[-1].text + text, part.hit)
        else:
            merged.append(SnippetPart(text, part.hit))
    if merged:
        merged[0] = SnippetPart(merged[0].text.lstrip(), merged[0].hit)
        merged[-1] = SnippetPart(merged[-1].text.rstrip(), merged[-1].hit)
    tidy: list[SnippetPart] = []
    room = SNIPPET_MAX
    for part in merged:
        if room <= 0:
            break
        if part.text:
            tidy.append(SnippetPart(part.text[:room], part.hit))
            room -= len(part.text)
    return tidy


def _from_headline(headline: str) -> list[SnippetPart]:
    parts: list[SnippetPart] = []
    for index, piece in enumerate(headline.split(START)):
        if index == 0:
            parts.append(SnippetPart(_without_markers(piece), False))
            continue
        hit, _stop, rest = piece.partition(STOP)
        parts.append(SnippetPart(_without_markers(hit), True))
        parts.append(SnippetPart(_without_markers(rest), False))
    return _tidy(parts)


def _is_similar(token: str, words: list[str], *, fuzzy: bool) -> bool:
    if fuzzy:
        return any(SequenceMatcher(None, token, word).ratio() >= FUZZY_MARK_RATIO for word in words)
    return any(token.startswith(word) for word in words)


def _marked(text: str, words: list[str], *, fuzzy: bool) -> list[SnippetPart]:
    """The start of `text` with the words that match marked (no stemming here)."""
    head = _without_markers(text)[:SUMMARY_HEAD]
    parts: list[SnippetPart] = []
    position = 0
    for match in _FRAGMENT_WORD.finditer(head):
        if _is_similar(match.group().lower(), words, fuzzy=fuzzy):
            parts.append(SnippetPart(head[position : match.start()], False))
            parts.append(SnippetPart(match.group(), True))
            position = match.end()
    parts.append(SnippetPart(head[position:], False))
    return _tidy(parts)


def _headlines(
    membership: Membership, documents: list[Document], query: SearchQuery
) -> dict[UUID, str]:
    """`ts_headline` over the text of the page's documents only, in one query."""
    text = Replace(Replace("ocr_text", models.Value(START)), models.Value(STOP))
    rows = (
        visibility.visible_documents(membership)
        .filter(id__in=[d.id for d in documents])
        .annotate(
            headline=SearchHeadline(
                text,
                query,
                config="dutch",
                start_sel=START,
                stop_sel=STOP,
                max_words=18,
                min_words=8,
                max_fragments=1,
            )
        )
        .values_list("id", "headline")
    )
    return dict(rows)


def _snippets(
    membership: Membership,
    documents: list[Document],
    words: list[str],
    query: SearchQuery,
    *,
    fuzzy: bool,
) -> dict[UUID, list[SnippetPart]]:
    headlines = {} if fuzzy or not documents else _headlines(membership, documents, query)
    snippets: dict[UUID, list[SnippetPart]] = {}
    for document in documents:
        headline = headlines.get(document.id, "")
        if START in headline:
            snippets[document.id] = _from_headline(headline)
        else:
            # The hit is in the sender, summary, a label or a person, or approximate.
            start = document.summary or str(getattr(document, "text_head", ""))
            snippets[document.id] = _marked(start, words, fuzzy=fuzzy)
    return snippets


def search_out(result: SearchResult) -> DocumentSearchOut:
    """The answer of `searchDocuments`. It never repeats the term."""
    return DocumentSearchOut(
        items=[
            DocumentSearchHitOut(
                **document_summary_out(hit.document).model_dump(),
                snippet=[SnippetPartOut(text=p.text, hit=p.hit) for p in hit.snippet],
            )
            for hit in result.hits
        ],
        next_offset=result.next_offset,
        fuzzy=result.fuzzy,
        bsn_removed=result.bsn_removed,
    )
