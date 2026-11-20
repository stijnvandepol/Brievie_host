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
   word similarity over sender, summary and text: a typo ("belastingdeinst") still finds
   the letter, and the answer says it is an approximate match. Two cheap, explainable
   steps instead of one blended score.
2. Trigram only (`ILIKE`/similarity on everything). Finds parts of words anywhere, but no
   Dutch stemming (betaal/betaling/betalen), poor ranking, and a larger index on the full
   text used for every query.
3. Full-text only. No typo tolerance at all: a misspelt sender finds nothing.

The choice is measured on 10,000 generated letters before it is final (task 3); the numbers
go into DECISIONS.

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
- Trigram GIN indexes (`gin_trgm_ops`) on `sender` and `ocr_text` for the fallback; whether
  the one on `ocr_text` earns its size is part of the measurement.

### The query

`apps/documents/search.py`, `search_documents(membership, *, q, filters, limit, offset) -> SearchResult`.

1. Clean the term: strip, collapse spaces, at most 200 characters; `mask_bsns(q)` and then
   remove every `[BSN]` it produced. `bsn_removed` says that happened. A term with nothing
   left (or shorter than 2 characters) finds nothing: `items=[]`.
2. Words: split on whitespace, at most 8, each stripped of tsquery syntax (letters, digits,
   `-`, `'`, `.` kept; the rest dropped). Query: every word must match, each as a prefix
   (`word:*`), built with bound parameters only (never string-built SQL).
3. A document matches when its `search_vector` matches the query, **or** every word matches
   (case-insensitive substring) the name of one of its labels or the name or a name variant
   of one of its persons. Rank: `ts_rank_cd` plus a fixed boost for a label/person name
   match; then newest first.
4. Filters (all optional, all narrow): `document_type`, `label_id`, `person_id`,
   `inbox_state`, `date_from` / `date_to` on the letter date (a letter without one falls
   under its upload date).
5. Only when step 3 finds nothing on the first page: the fallback. A document matches when
   every word is word-similar (`pg_trgm`, `word_similarity >= 0.45`) to its sender, summary
   or text. `fuzzy=True` on the answer.
6. Fragment, for the page's rows only: `ts_headline('dutch', ocr_text, query)` with control
   characters as markers, split into parts in Python; when the hit is in the sender, summary,
   a label or a person, or in the fallback, the fragment is the start of the summary (else of
   the text) with the similar words marked in Python. Never HTML.
7. Nothing of the term is logged, audited or put in an error message.

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
- Results page `Zoeken`: the term stays in memory (router state), not in the URL. Rows like
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
2. The BSN rule: a BSN in the term is removed before anything else, and the answer says so.
   Stored text holds `[BSN]`, so the literal mask is removed too and can never be searched.
3. Label and person names are matched by substring per word, not by full-text: they are
   short, few, and people type parts of names.
4. The fallback runs only on an empty exact result, so an exact search never pays for it
   and never gets vague hits mixed in.
5. No audit row for a search: a term in the audit log would be the leak the rule forbids.

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
