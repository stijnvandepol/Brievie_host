"""Searching letters inside one workspace.

Design: docs/superpowers/specs/2026-11-20-search-design.md.

Two steps. The exact step uses PostgreSQL full-text search in Dutch (`search_vector`:
sender, summary, text), every word as a word beginning, or the names of the letter's
labels and persons. Only when that finds nothing does the fallback run: `pg_trgm` word
similarity over sender and summary, for typos (not the text: measured too slow). Every
query starts from `visibility.visible_documents`, so a hit is always a letter the member
may see.

The term is a name or a number somebody looks for: nothing here logs it, audits it or
puts it in an error message, and it only ever reaches SQL as a bound parameter.
"""

import re
import unicodedata
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
from apps.common.privacy import MASK, mask_bsns, passes_elfproef
from apps.documents import visibility
from apps.documents.models import Document, DocumentAddressee, DocumentLabel
from apps.documents.schemas import DocumentSearchHitOut, DocumentSearchOut, SnippetPartOut
from apps.documents.services import (
    HEADLINE_PROPOSALS,
    INBOX_STATES,
    SUGGESTIONS,
    _has_addressee,
    _in_state,
    document_summary_out,
)
from apps.labels.models import Label
from apps.people.models import Person
from apps.workspaces.models import Membership

MAX_TERM = 200
MAX_WORDS = 8
MIN_TERM = 2
MAX_OFFSET = 1000
# Added to the full-text rank for every word the letter matches through a label or
# person name.
NAME_BOOST = 1.0
# pg_trgm word similarity a word needs with the sender or the summary in the fallback.
FUZZY_THRESHOLD = 0.45
# The exact step ranks at most this many matches (the newest): ranking reads every
# match's whole vector, which on a word in most letters costs far more than finding them
# (measured on 10,000 letters, DECISIONS 2026-11-20).
MAX_CANDIDATES = 1000
_THRESHOLD_SETTING = "pg_trgm.word_similarity_threshold"
# Python's similarity a word of the fragment needs to be marked in the fallback.
FUZZY_MARK_RATIO = 0.7
SNIPPET_MAX = 240
SUMMARY_HEAD = 160
# Markers ts_headline puts around a hit: control characters, which stored text never holds.
START, STOP = "\x02", "\x03"

_MASK_LITERAL = re.compile(re.escape(MASK), re.IGNORECASE)
_WORD_CHARS = frozenset("-'.")
# Characters that do not show: removed before anything else, so they split no number.
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\u2060\ufeff"))
# Words that only ever find the mask in stored text.
_BSN_WORDS = frozenset({"bsn", "burgerservicenummer"})
_WORD_PARTS = re.compile(r"[-'.]")
BSN_DIGITS = 9
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
    """The term normalised (NFKC, invisible characters removed), with collapsed spaces,
    at most `MAX_TERM` characters, without BSNs and without the literal mask; and
    whether a BSN was removed."""
    text = " ".join(unicodedata.normalize("NFKC", q).translate(_INVISIBLE).split())[:MAX_TERM]
    masked = mask_bsns(text)
    removed = masked.count(MASK) > text.count(MASK)
    return " ".join(_MASK_LITERAL.sub(" ", masked).split()), removed


def _without_bsn_digits(words: list[str]) -> tuple[list[str], bool]:
    """`words` without every run of adjacent digit groups that holds exactly nine digits
    and passes the elfproef: a BSN written with a separator `mask_bsns` does not know
    ("111/222/333"). Groups inside a word count too ("111-222"); a word that loses a
    group keeps its other parts as words. And whether a run was removed."""
    # (word index, group) for every group of every word, split on - ' and .
    parts = [(index, part) for index, word in enumerate(words) for part in _WORD_PARTS.split(word)]
    parts = [(index, part) for index, part in parts if part]
    dropped: set[int] = set()
    start = 0
    while start < len(parts):
        end, digits = start, ""
        while end < len(parts) and parts[end][1].isdigit() and len(digits) < BSN_DIGITS:
            digits += parts[end][1]
            end += 1
        if len(digits) == BSN_DIGITS and passes_elfproef(digits):
            dropped.update(range(start, end))
            start = end
        else:
            start += 1
    if not dropped:
        return words, False
    touched = {parts[n][0] for n in dropped}
    kept: list[str] = []
    for index, word in enumerate(words):
        if index not in touched:
            kept.append(word)
            continue
        kept.extend(
            part for n, (owner, part) in enumerate(parts) if owner == index and n not in dropped
        )
    return kept, True


def search_words(term: str) -> tuple[list[str], bool]:
    """At most `MAX_WORDS` words, lower-cased, of letters, digits, - ' and . (every other
    character splits words); and whether a BSN or the word "BSN" was taken out.

    A word without a letter or digit is dropped, and so are the words "bsn" and
    "burgerservicenummer": stored text holds `[BSN]` for every masked number, so they
    would only find masked letters.
    """
    split = "".join(c if c.isalnum() or c in _WORD_CHARS else " " for c in term).lower()
    candidates, removed = _without_bsn_digits(split.split())
    words: list[str] = []
    for word in candidates:
        if word.strip("-'.") in _BSN_WORDS:
            removed = True
        elif any(c.isalnum() for c in word):
            words.append(word)
    return words[:MAX_WORDS], removed


def _tsquery(words: list[str], operator: str = "&") -> SearchQuery:
    """Every word as a quoted lexeme with a prefix match, joined by `operator` (& all
    required, | any).

    Quoting (with ' doubled) means no input can be read as tsquery syntax; the string is
    a bound parameter of `to_tsquery`.
    """
    lexemes = f" {operator} ".join(_lexeme(word) for word in words)
    return SearchQuery(lexemes, search_type="raw", config="dutch")


def _lexeme(word: str) -> str:
    """`word` as a quoted tsquery lexeme with a prefix match."""
    return "'{}':*".format(word.replace("'", "''"))


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


@dataclass(frozen=True)
class _Names:
    """The labels and persons whose name holds a word."""

    labels: list[UUID]
    persons: list[UUID]

    def __bool__(self) -> bool:
        return bool(self.labels or self.persons)

    def linked(self) -> models.Q:
        """The letters linked to one of these labels or persons (by index on the link
        tables)."""
        return models.Q(
            pk__in=DocumentLabel.objects.filter(label_id__in=self.labels).values("document_id")
        ) | models.Q(
            pk__in=DocumentAddressee.objects.filter(person_id__in=self.persons).values(
                "document_id"
            )
        )


def _names(membership: Membership, word: str) -> _Names:
    """The labels whose name, and the persons (archived ones too) whose name or a name
    variant, holds `word` (case-insensitive substring). Labels and persons are few."""
    workspace_id = membership.workspace_id
    labels = list(
        Label.objects.filter(workspace_id=workspace_id, name__icontains=word).values_list(
            "id", flat=True
        )
    )
    persons = list(
        Person.objects.filter(workspace_id=workspace_id)
        .annotate(variants=Cast("name_variants", models.TextField()))
        .filter(models.Q(display_name__icontains=word) | models.Q(variants__icontains=word))
        .values_list("id", flat=True)
    )
    return _Names(labels, persons)


def _word_match(word: str, names: _Names) -> models.Q:
    """A letter has `word` in its sender, summary or text, or through a name."""
    match = models.Q(search_vector=_tsquery([word]))
    return match | names.linked() if names else match


def _page(
    documents: "models.QuerySet[Document]", limit: int, offset: int
) -> tuple[list[Document], int | None]:
    rows: list[Document] = list(
        documents.prefetch_related(
            "addressee_links", "label_links", SUGGESTIONS, HEADLINE_PROPOSALS
        )[offset : offset + limit + 1]
    )
    more = len(rows) > limit and offset + limit <= MAX_OFFSET
    return rows[:limit], (offset + limit if more else None)


def _without_stop_words(words: list[str]) -> list[str]:
    """`words` without the ones the `dutch` configuration drops, in one query."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT numnode(to_tsquery('dutch', w)) > 0 FROM unnest(%s::text[]) WITH ORDINALITY"
            " AS t(w, n) ORDER BY n",
            [[_lexeme(word) for word in words]],
        )
        kept = [row[0] for row in cursor.fetchall()]
    return [word for word, keep in zip(words, kept, strict=True) if keep]


def _exact(
    membership: Membership,
    documents: "models.QuerySet[Document]",
    words: list[str],
) -> "models.QuerySet[Document] | None":
    """The exact matches, best first; None when there are none.

    Every word must be found: in the sender, summary or text (a word beginning, Dutch
    stems), or in the name of one of the letter's labels or persons. The words that are
    in no name at all must be in the text: they drive one lookup on the text index.
    Without such a word the first word drives two lookups (the text index; its labels and
    persons). Each lookup applies the other words as row filters and keeps the newest
    MAX_CANDIDATES; then one ranking of only those letters. One query with an OR of text
    and names could not use the text index and read every letter of the workspace.
    """
    newest = ("-created_at", "-id")
    # A Dutch stop word ("een", "de") is no lexeme: full-text search ignores it, so it is
    # not required here either (unless every word is one).
    words = _without_stop_words(words) or words
    names = {word: _names(membership, word) for word in words}
    text_only = [word for word in words if not names[word]]

    def lookup(driver: models.Q, others: list[str]) -> set[UUID]:
        rows = documents.filter(driver)
        for word in others:
            rows = rows.filter(_word_match(word, names[word]))
        return set(rows.order_by(*newest).values_list("pk", flat=True)[:MAX_CANDIDATES])

    if text_only:
        named_words = [word for word in words if names[word]]
        ids = lookup(models.Q(search_vector=_tsquery(text_only)), named_words)
    else:
        first, *others = words
        ids = lookup(models.Q(search_vector=_tsquery([first])), others) | lookup(
            names[first].linked(), others
        )
    if not ids:
        return None
    rank: models.Expression = SearchRank(
        models.F("search_vector"), _tsquery(words, "|"), cover_density=True
    )
    for word in words:
        if names[word]:
            rank = rank + models.Case(
                models.When(names[word].linked(), then=models.Value(NAME_BOOST)),
                default=models.Value(0.0),
                output_field=models.FloatField(),
            )
    return (
        documents.filter(pk__in=ids)
        .annotate(rank=models.ExpressionWrapper(rank, output_field=models.FloatField()))
        .order_by("-rank", *newest)
    )


def _fuzzy(documents: "models.QuerySet[Document]", words: list[str]) -> "models.QuerySet[Document]":
    """Every word word-similar to the sender or the summary (`%>` uses the trigram
    indexes; the threshold is set for the query by `_fuzzy_page`).

    Not the letter's text: word similarity over whole letters took 6 to 10 seconds on
    10,000 letters (DECISIONS 2026-11-20). A typo in a word that is only in the text
    finds nothing, and the answer's next step is another spelling.
    """
    match = models.Q()
    score: models.Expression = models.Value(0.0)
    for word in words:
        value = models.Value(word, output_field=models.TextField())
        match &= models.Q(TrigramWordSimilar(models.F("sender"), value)) | models.Q(
            TrigramWordSimilar(models.F("summary"), value)
        )
        score = score + Greatest(
            TrigramWordSimilarity(word, "sender"),
            TrigramWordSimilarity(word, "summary"),
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
    words, removed = search_words(term)
    bsn_removed = bsn_removed or removed
    if len(term) < MIN_TERM or not words:
        return SearchResult(bsn_removed=bsn_removed)
    # Any word marks the fragment: a word can also be found through a name.
    query = _tsquery(words, "|")
    documents = _filtered(membership, filters)
    exact = _exact(membership, documents, words)
    rows, next_offset = _page(exact, limit, offset) if exact is not None else ([], None)
    # Only when nothing matched exactly, also on a later page of an approximate result.
    fuzzy = exact is None
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
