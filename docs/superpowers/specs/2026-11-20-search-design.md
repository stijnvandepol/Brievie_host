# Search for letters

Date: 2026-11-20. Branch `feat/search` (host). iOS follows separately.

## Intent

Stijn: "ik wil dat er ook makkelijk gezocht kan worden naar brieven in de web interface
maar ook in de app". One search box that finds a letter by what is in it, who sent it,
what it is about, its labels and the persons it is for.

## Goals, non-goals, anti-goals

- Goal: search inside one workspace over the masked letter text, sender, summary, label
  names and person names, with the list's filters, a short fragment with the hit marked,
  typo tolerance and word beginnings.
- Goal: one visibility seam. Search, list and counts take their documents from one
  function, so the rights model with departments and private post changes one place.
- Non-goal: a search service, Redis, search across workspaces, saved searches, iOS screens.
- Anti-goal: a search term in a log, a URL or an audit row (it can be a name or a number);
  a result that reveals a letter the caller may not see; a BSN that finds anything.

## Approaches

1. **PostgreSQL full-text (Dutch) with prefix matching, trigram fallback for typos
   (chosen).** A generated `tsvector` column on the document (sender weight A, summary B,
   text C) with a GIN index answers whole words, Dutch stems and word beginnings
   (`belast:*`) fast. Only when that finds nothing does a second query run with `pg_trgm`
   word similarity over sender and summary: a typo ("belastingdeinst") still finds
   the letter, and the answer says it is an approximate match. Two cheap, explainable
   steps instead of one blended score.
2. Trigram only (`ILIKE`/similarity on everything). Finds parts of words anywhere, but no
   Dutch stemming (betaal/betaling/betalen), poor ranking, and a larger index on the full
   text used for every query.
3. Full-text only. No typo tolerance at all: a misspelt sender finds nothing.

The choice was measured on 10,000 generated letters (see "Measured"); the numbers and what
they changed are in DECISIONS.

## Design

### Visibility seam

`apps/documents/visibility.py`: `visible_documents(membership: Membership) ->
QuerySet[Document]`. Today: the workspace's documents (RLS enforces the workspace as
before). `list_documents`, `inbox_counts`, `get_document` and search all start from it.
Labels and persons matched by name are joined through the same queryset, so a hit can only
ever be a visible document.

### Index

- Migration: `pg_trgm` and `unaccent` are not needed for step one; `pg_trgm` is created by
  the owner role (trusted extension; verified on PostgreSQL 18).
- `Document.search_vector`: a generated column
  `setweight(to_tsvector('dutch', sender), 'A') || setweight(to_tsvector('dutch', summary), 'B') || setweight(to_tsvector('dutch', ocr_text), 'C')`,
  GIN index. The text is already BSN-masked when stored.
- Trigram GIN indexes (`gin_trgm_ops`) on `sender` and `summary` for the fallback. There is
  none on `ocr_text`: it was 17 MB and the fallback over the whole text was too slow (see
  "Measured"), so typos are only found in the sender and the summary.
- `django.contrib.postgres` is in `INSTALLED_APPS` (part of Django, no new dependency).

### The query

`apps/documents/search.py`, `search_documents(membership, *, q, filters, limit, offset) -> SearchResult`.

1. Clean the term: NFKC, soft hyphens and zero-width characters removed, collapse spaces, at
   most 200 characters; `mask_bsns(q)` and then remove every `[BSN]` it produced (and the
   literal `[BSN]`). `bsn_removed` says that happened. A term with nothing
   left (or shorter than 2 characters) finds nothing: `items=[]`.
2. Words: every character that is not a letter, digit, `-`, `'` or `.` becomes a space; at
   most 8 words. A run of adjacent digit groups with exactly nine digits that passes the
   elfproef is removed (a BSN with a separator `mask_bsns` does not know: "111/222/333"),
   and so are the words "bsn" and "burgerservicenummer" (they would only find the stored
   mask); both set `bsn_removed`. Each word is a quoted prefix lexeme (`'word':*`), passed
   to `to_tsquery` as a query parameter (never string-built SQL).
3. The exact step: every word must be found, each in its own place: in `search_vector` (a
   word beginning, Dutch stems), or in the name of one of the letter's labels, or the name
   or a name variant of one of its persons (case-insensitive substring). Per word the
   matching labels and persons are looked up first (small tables). The words in no name at
   all must be in the text: they drive one lookup on the text index. When every word is in
   some name, the first word drives two lookups (the text index; the letters linked to its
   labels and persons). Each lookup applies the other words as row filters (text match OR
   linked to that word's labels/persons), orders newest first and keeps `MAX_CANDIDATES`
   (1000). Then one ranking of only those letters: `ts_rank_cd` against all words joined
   with OR (more words in the text rank higher) plus `NAME_BOOST` for every word the letter
   matches through a name; then newest first. A word in most letters therefore ranks the
   newest 1000 matches, not all of them.
4. Filters (all optional, all narrow): `document_type`, `label_id`, `person_id`,
   `inbox_state`, `date_from` / `date_to` on the letter date (a letter without one falls
   under its upload date).
5. Only when step 3 finds nothing (also for a later page of an approximate result): the
   fallback. A document matches when every word is word-similar (`pg_trgm`,
   `word_similarity >= 0.45`, `FUZZY_THRESHOLD`) to its sender or its summary, not its text.
   `fuzzy=True` on the answer when there are hits.
6. Fragment, for the page's rows only: `ts_headline('dutch', ocr_text, query)` with control
   characters as markers, split into parts in Python; when the hit is in the sender, summary,
   a label or a person, or in the fallback, the fragment is the start of the summary (else of
   the text, 160 characters) with the matching words marked in Python (word beginnings; in the
   fallback, similar words). At most 240 characters. Never HTML.
7. Brievie logs, audits or echoes nothing of the term. PostgreSQL logs a failing statement
   (its default), and with client-side binding that statement holds the term (see PRIVACY).

### API (additive)

`POST /workspaces/{id}/documents/search` (`searchDocuments`, any member, device token or
web session). POST, so the term is in the body: not in a URL, an access log or browser
history.

- In: `q: str` (max 200), `document_type: str = ""`, `label_id: UUID | None = None`,
  `person_id: UUID | None = None`, `inbox_state: str = ""`, `date_from: date | None = None`,
  `date_to: date | None = None`, `limit: int = 30` (1..100), `offset: int = 0` (0..1000).
- Out `DocumentSearchOut`: `items: list[DocumentSearchHitOut]`, `next_offset: int | None`
  (null: no more), `fuzzy: bool`, `bsn_removed: bool`.
- `DocumentSearchHitOut` = `DocumentSummaryOut` + `snippet: list[SnippetPartOut]`,
  `SnippetPartOut {text: str, hit: bool}`.
- Throttled per caller (`30/1m`), as other endpoints that cost work.

### Web

- A search field in the app bar, on every page. `/` focuses it (not while typing in a
  field), Enter searches, Escape clears and returns focus.
- Results page `Zoeken`: the term stays in memory (a small store, `web/src/search-term.ts`),
  not in the URL or router/history state; signing out and switching workspace forget it.
  Filter changes search once, 300 ms after the last change. Rows like
  "Alle post" with the fragment under them, hits in `<mark>`. Filters: soort, label, persoon,
  periode, status. "Meer laden" for the next page.
- States: empty (what you can search for, with examples), searching, no result (with a next
  step: fewer words, another spelling, or "Alle post"), approximate result ("Geen exacte
  treffer. Dit lijkt erop."), a BSN was removed ("Zoeken op een BSN kan niet: Brievie
  bewaart geen BSN's. We hebben het nummer uit je zoekopdracht gehaald.").

## Rulings

1. A search endpoint instead of `q` on the list: the list has no paging and its answer has
   no fragment; POST keeps the term out of URLs. Paging is offset-based with a ceiling
   (offset <= 1000): search results are for finding, not for walking the archive.
2. The BSN rule: a BSN in the term is removed before anything else (also with other
   separators, see step 2), and the answer says so. Stored text holds `[BSN]`, whose lexeme is
   `bsn`, so the literal mask and the words "bsn" and "burgerservicenummer" are removed too and
   can never be searched.
3. Label and person names are matched by substring per word, not by full-text: they are
   short, few, and people type parts of names.
4. The fallback runs only on an empty exact result, so an exact search never pays for it
   and never gets vague hits mixed in. It looks in the sender and the summary only: a typo
   in a word that is only in the letter's text finds nothing (a follow-up: a word list per
   workspace to correct such a typo).
5. No audit row for a search: a term in the audit log would be the leak the rule forbids.
6. The exact step is two indexed id lookups and one ranking of at most the newest 1000 matches
   of each, not one query with an OR: that read every letter (see "Measured").

## Measured

On a throw-away database with 10,000 generated Dutch letters of 180 to 420 words (PostgreSQL
18 on a Mac, warm, median of 7 runs of the service function, fragment and prefetch included):
the first version (one query, text index OR name subqueries, ranking every match) took about
550 ms for any term, and its typo fallback with word similarity over the whole text took 6 to
10 seconds. As built, an exact search takes 33 to 50 ms (a rare word, a word in most letters, a
stem, a word beginning, two words, a sender) and the typo fallback 230 to 300 ms (it only runs
when nothing matched exactly). With words combining across text and names (one label and one
person on 2,000 letters each): exact 35 to 60 ms, cross cases 34 to 95 ms (three words over
text, person and label). Sizes: table 32 MB; full-text GIN index 6.4 MB; trigram index on
sender 4.4 MB, on summary 2.3 MB; the dropped trigram index on the text was 17 MB.

## Plan

Global constraints: `CLAUDE.md` "Rules in short" (additive API with defaults, nullable only
primitives, no `anyOf`, explicit `operation_id`, `ApiError` with Dutch B1 messages, thin
endpoints, `mypy --strict`, no document content or search term in logs, tests first, tests
run as the non-superuser role, RLS forced). Never touch Docker project `brievie`; never
print `deploy/.env`; no push, no tags.

### Task 1: Seam, index and search (server)

Files: `backend/apps/documents/visibility.py` (new), `search.py` (new), `services.py`,
`models.py`, a migration, `schemas.py`, `api.py`; tests `test_documents_visibility.py`,
`test_documents_search.py`, `test_documents_search_api.py`; `docs/api/openapi.json`,
`web/src/api/schema.d.ts`.

Tests that must exist (each failing without its code): word, Dutch stem (betaling finds
betalen), prefix, two words both required, sender outranks text, label name and person name
(and a name variant) hits, every filter alone and one combination, paging with
`next_offset`, typo fallback with `fuzzy`, no fallback when exact hits exist, BSN in the
term removed (valid 11-proef number, with and without the word "BSN"), the literal `[BSN]`
finds nothing, term of one character, 201 characters refused (422), tsquery syntax in the
term is harmless (`a & !b | (c:*`, quotes, backslashes), a letter of workspace B is never
found from A (API, as a member of A; and as the app role without workspace context nothing
is returned), an archived person's name still finds its letters, the fragment marks the
hit and contains no markup, the term appears in no log line (caplog) and no audit row,
query count of a search does not grow with the page size.

### Task 2: Web

Files: `web/src/components/search-field.tsx`, `web/src/routes/search.tsx`, the shell/root
route, `web/src/api/queries.ts`, tests, `web/src/locales`.

### Task 3: Measure and decide (controller)

A script that fills the throw-away database with 10,000 generated Dutch letters, then
times exact, prefix, name and fallback queries (`EXPLAIN ANALYZE`, warm), index sizes, and
the same without the `ocr_text` trigram index. Numbers and the decision in DECISIONS.

### Task 4: Documents of record

`CLAUDE.md` (the seam; the term is never logged), `docs/SPEC.md`, `docs/DECISIONS.md`,
`docs/PRIVACY.md`, `docs/GLOSSARY.md`.
