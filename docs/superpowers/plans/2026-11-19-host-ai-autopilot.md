# AI autopilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An opt-in per workspace that lets Brievie apply the AI's label/addressee proposals and confirm letters itself, recognisable and reversible.

**Architecture:** Two booleans on `AISettings`, written only by `apps.pipeline.settings_service`. The pipeline's last transaction (which holds the document's row lock) applies qualifying proposals through `apps.documents.organise` and, when a pure decision function allows it, confirms through the same code a person's confirm uses. Markers on the link rows, the document and the tasks make everything automatic visible; undo and a new reopen reverse it.

**Tech Stack:** Django 6.1, Django Ninja, PostgreSQL 18 (forced RLS), Procrastinate, React + TanStack + React Aria + Lingui (web).

**Spec:** `docs/superpowers/specs/2026-11-19-ai-autopilot-design.md` (read it first; its Rulings bind).

## Global Constraints

- Read `CLAUDE.md` "Rules in short"; all of it applies. In particular:
- Only `apps.documents.organise` adds or removes a document's labels and addressees. `apps.rules.engine` stays pure.
- A dismissed `DocumentSuggestion` is never repeated or applied by anything automatic.
- No made-up actor: automatic changes have `actor=None` / `confirmed_by=None`.
- API additive only; a field added to an existing schema has a default; nullable only primitives; no `anyOf`. Every endpoint has an explicit camelCase `operation_id` and typed schemas. Errors via `ApiError` with a Dutch B1 message.
- User-facing text Dutch (B1): backend through `gettext`, web through Lingui `t` macros (`pnpm extract` afterwards). Code, comments, commits in English.
- `mypy --strict`, `ruff check`, `ruff format --check` clean; no `Any`, no bare `except`.
- No document content, tokens or BSNs in logs (log ids and exception type names only).
- Every mutating service function wrapped in `transaction.atomic`.
- Tests first; each fix or guard has a test that fails without it. Tests run as the non-superuser role (`make test`). Run only the test files you touch plus the ones named in your task; the controller runs the full suite.
- Constants, exact: `AUTO_APPLY_FROM = Decimal("0.800")`; auto-confirm threshold = the existing `NEEDS_ATTENTION_BELOW` (`Decimal("0.85")`, `apps.documents.models`); name match threshold = the existing `PRESELECT_FROM` (`apps.people.matching`).
- Never run code that deletes or moves files outside `tmp_path`; never touch Docker project `brievie`; never print `deploy/.env`; no push, no tags.
- Conventional commits, each ending with the line `Co-Authored-By: Claude Code <noreply@anthropic.com>`.

## Review Focus

- A person removed a label the AI applied, then the letter is processed again (retry after failure is the only re-run): the label must not return.
- Autopilot switched on while the server forbids it by env after the fact: nothing is applied.
- AI switched off and on again: both switches are off.
- A deadline in the past or before the letter date with high model confidence: no auto-confirm.
- A member (not admin) or a device token calls `updateAiAutopilot`: refused.

---

### Task 1: Data model and server switch

**Files:**
- Modify: `backend/apps/pipeline/models.py`, `backend/apps/documents/models.py`, `backend/apps/tasks/models.py`, `backend/brievie/settings.py`, `deploy/.env.example`, `deploy/docker-compose.yml`
- Create: one migration per app (`pipeline`, `documents`, `tasks`)
- Test: `backend/tests/test_autopilot_models.py`

**Interfaces — Produces:**
- `AISettings.auto_organise: bool` (default False), `AISettings.auto_confirm: bool` (default False).
- `DocumentLabel.automatic: bool`, `DocumentAddressee.automatic: bool` (default False): Brievie put this link there itself.
- `Document.confirmed_by_ai: bool` (default False): the standing confirmation is the AI's.
- `Task.created_by_ai: bool` (default False).
- `DocumentEvent.Origin.AI = "ai"`, `DocumentEvent.Origin.NAME_MATCH = "name_match"`; `DocumentEvent.Kind.REOPENED = "reopened"`.
- `settings.BRIEVIE_AI_AUTOPILOT_ALLOWED: bool` = `env.bool("BRIEVIE_AI_AUTOPILOT_ALLOWED", default=True)`; a commented line with one sentence of explanation in `deploy/.env.example`; passed to the `api` and `worker` services in the compose file the way the other optional `BRIEVIE_*` variables are.

- [ ] Write tests: defaults of every new field; the new choices exist; the setting defaults to True (and `override_settings` can flip it).
- [ ] Add fields, choices, setting; `uv run python manage.py makemigrations` (as the project does it: see `Makefile`); check `makemigrations --check` is clean.
- [ ] Run `make lint typecheck` and the new test file plus `tests/test_deploy_files.py`; commit `feat(autopilot): fields and the server switch`.

### Task 2: The switches: service, audit, API

**Files:**
- Modify: `backend/apps/pipeline/settings_service.py`, `backend/apps/pipeline/schemas.py`, `backend/apps/pipeline/api.py`
- Test: `backend/tests/test_pipeline_autopilot_settings.py`, `backend/tests/test_pipeline_autopilot_api.py`

**Interfaces — Consumes:** Task 1 fields and setting; `record`, `AuditCode.WORKSPACE_SETTING_CHANGED` (values `setting`, `value`), `Actor`, `_audit_actor` as `configure` uses them.

**Interfaces — Produces (in `settings_service`):**
- `@dataclass(frozen=True) class Autopilot: organise: bool; confirm: bool; allowed: bool` — `allowed` is the server switch.
- `autopilot(workspace_id: UUID) -> Autopilot` — the stored switches (False when no settings row).
- `autopilot_in_force(workspace_id: UUID) -> Autopilot` — what the pipeline may do now: both False unless `settings.BRIEVIE_AI_AUTOPILOT_ALLOWED` and the mode is not `off`.
- `autopilot_notice() -> str` — the Dutch text to agree to. It must say, in B1: Brievie puts letters under persons and labels and (for confirm) confirms them itself; nobody checks first; the AI can make mistakes; a deadline is always in the letter itself, so look at the letter when it matters; everything automatic is marked "Automatisch door AI" and can be undone; a task without a deadline may mean the AI missed it.
- `configure_autopilot(*, workspace_id: UUID, organise: bool, confirm: bool, acknowledged: bool, actor: Actor) -> Autopilot` — locks the `AISettings` row (`select_for_update`); raises `AutopilotError(reason)` with reasons `forbidden` (server switch false and a switch would go on), `needs_ai` (no row or mode `off` and a switch would go on), `ack_required` (a switch goes from off to on without `acknowledged`). Switching off always works. One audit row per switch whose value changed: `values={"setting": "ai_auto_organise" | "ai_auto_confirm", "value": <bool>}`; none when nothing changed.
- `configure` (existing): when the result is mode `off`, or the event is `GRANTED` for another address than before, both switches become False in the same transaction, with the same audit rows (actor = the configure actor).

**API:**
- `AiStatusOut`: `auto_organise: bool = False`, `auto_confirm: bool = False`, `autopilot_allowed: bool = True` (the in-force server switch); filled in `from_status` callers.
- `GET /workspaces/{workspace_id}/ai/autopilot`, `operation_id="getAiAutopilot"`, any member: `AiAutopilotOut {auto_organise: bool, auto_confirm: bool, allowed: bool, notice: str}`.
- `PUT` same path, `operation_id="updateAiAutopilot"`, admin only, same auth, error set and throttle style as `updateAiSettings` (web session; a device token is refused — check how `updateAiSettings` achieves that and do the same): body `AiAutopilotIn {auto_organise: bool, auto_confirm: bool, acknowledged: bool = False}`; errors 409 `ai_autopilot_ack_required`, 409 `ai_autopilot_needs_ai`, 409 `ai_autopilot_forbidden`, each with a Dutch sentence. Returns `AiAutopilotOut`.

- [ ] Tests first, service: each reason; off→on with ack writes one audit row naming the user; on→off without ack works and audits; no change, no row; `configure(mode off)` and a new-address `GRANTED` reset both and audit; `autopilot_in_force` is all False with the server switch off or mode off even when stored True.
- [ ] Tests first, API: member reads; member PUT 403; admin PUT; device token PUT refused like `updateAiSettings`; the three 409 codes; `getAiStatus` carries the new fields.
- [ ] Implement; run the two new files plus `tests/test_pipeline_ai_settings_api.py tests/test_pipeline_ai_consent.py tests/test_pipeline_ai_status_api.py`; lint, typecheck; commit `feat(autopilot): the switches, their notice and their audit rows`.

### Task 3: Organise automatically (a) and undo

**Files:**
- Modify: `backend/apps/documents/organise.py`, `backend/apps/pipeline/steps.py`, `backend/apps/documents/schemas.py` (`DocumentEventOut.resolve_can_undo`), `backend/apps/documents/services.py` only if `list_document_events` filters on origin
- Create: `backend/apps/pipeline/autopilot.py`
- Test: `backend/tests/test_autopilot_organise.py`, additions to `backend/tests/test_documents_undo_api.py`

**Interfaces — Consumes:** `settings_service.autopilot_in_force`; `organise.add_label/add_addressee`; `suggestions.mark_accepted`; `DocumentEvent.Origin.AI/NAME_MATCH`; link field `automatic`.

**Interfaces — Produces:**
- `organise.add_label` / `add_addressee`: when `origin` is `DocumentEvent.Origin.AI` or `NAME_MATCH` the created link has `automatic=True` (rule and user adds keep False).
- `organise.undo_event`: also undoable for origin `AI` and `NAME_MATCH` (same conditions otherwise); `DocumentEventOut.can_undo` agrees.
- `apps/pipeline/autopilot.py`: `AUTO_APPLY_FROM = Decimal("0.800")`; `apply_open_proposals(document: Document) -> int` — for each OPEN `DocumentSuggestion` of the document, in a stable order (addressees first, then labels, each by confidence descending then id): origin `ai` with `confidence >= AUTO_APPLY_FROM`, or origin `name_match` with `confidence >= PRESELECT_FROM`, is applied through `organise` with `actor=None` and the matching event origin, then `suggestions.mark_accepted`. Origin `rule` rows are left. A target that is gone or (person) archived is skipped; nothing goes over `LABELS_MAX` / `ADDRESSEES_MAX`. Returns how many were applied. The caller holds the document lock and RLS context.
- `steps.run_pipeline`: after `_organise_guarded(...)`, only when a provider answered (not in `mark_failed`) and `autopilot_in_force(workspace_id).organise`, call `apply_open_proposals` in its own guarded savepoint (same pattern and logging as `_organise_guarded`: type name and id only). Read `autopilot_in_force` inside the final workspace context, not before the AI call.

- [ ] Tests first (pipeline level, with a fake provider as `tests/test_pipeline_steps.py` does): switch off → proposals stay open, nothing on the document; switch on → qualifying AI pick and strong name match are on the document with `automatic=True`, events have origin `ai` / `name_match` and no actor, suggestion rows `accepted`; AI pick at 0.799 stays open; a letter that carries an instruction to the AI and a keyword a `propose` rule matches (the rule proposes its label at confidence 1): the label stays a proposal, also when the AI picked the same label (the row's origin is then `rule`); weak name match stays open; rule proposal stays open; a dismissed change is not applied; server switch false → nothing; processing failed → nothing; limits respected; a removal by hand after an automatic add, then `organise_processed` + `apply_open_proposals` again → not re-added.
- [ ] Undo tests: an `ai` add and a `name_match` add can be undone (`can_undo` true, link gone, suggestion dismissed, `undo` event names the user); still refused for `user` and `suggestion` origins.
- [ ] Implement; run the new file, `tests/test_documents_undo_api.py tests/test_documents_organise.py tests/test_pipeline_steps.py tests/test_rules_executor.py`; lint, typecheck; commit `feat(autopilot): apply the AI's label and addressee picks, undoable`.

### Task 4: Confirm automatically (b) and reopen

**Files:**
- Modify: `backend/apps/pipeline/autopilot.py`, `backend/apps/pipeline/steps.py`, `backend/apps/pipeline/validation.py` (only to expose what the decision needs), `backend/apps/documents/review.py`, `backend/apps/documents/organise.py` (`record_confirmed`, a `record_reopened`), `backend/apps/documents/services.py`, `backend/apps/documents/api.py`
- Test: `backend/tests/test_autopilot_decision.py` (pure), `backend/tests/test_autopilot_confirm.py`, `backend/tests/test_documents_reopen_api.py`

**Interfaces — Produces:**
- `autopilot.decide_confirmation(*, payload: ExtractionPayload, values: Sequence[ValidatedValue], text: str, today: date) -> ConfirmIn | None` — pure (no database). Returns None when any guard rail of the spec's section "(b) Confirm automatically" fails (except the open-proposal one, which the caller checks). Rules, exact: sender value present with confidence >= `NEEDS_ATTENTION_BELOW`; letter date value present with confidence >= `NEEDS_ATTENTION_BELOW`; `payload.document_type` (stripped, lower-cased) is a key of `DOCUMENT_TYPES`; every action's type is a key of `ACTION_TYPES` and its description is not blank; at most 20 actions; for an action whose `deadline.value` is not empty: it parsed, its validated confidence >= `NEEDS_ATTENTION_BELOW`, `deadline >= today` and `deadline >= letter date`; and when any action has no deadline, `mentions_term(text)` must be False. `mentions_term(text: str) -> bool` (pure, same module, case-insensitive): True for `binnen <number or written number een..dertig> (dag|dagen|werkdag|werkdagen|week|weken|maand|maanden)`, `uiterlijk`, `vóór`/`voor` directly followed by a date in a form `dates_in` reads or by `<day> <month name>`, `vervaldatum`, `betaaltermijn`, `termijn`, `deadline`, `ten laatste`. No guard rail may use the confidence of a generated value (document type, summary, action type, description): it is always 0.500. The returned `ConfirmIn`: sender, type code, letter date, `summary` = the validated summary or "", `summary_by_ai=True`, one `TaskIn` per action (`deadline_source="in_document"` with the validated quote when it has a deadline, else `"manual"` with no deadline), `sets_organisation=False`.
- `review.confirm_automatically(document: Document, data: ConfirmIn) -> None` — the caller holds the lock on a `processing` document. Shares the writing code with `confirm_document` (refactor into one private function; `confirm_document`'s behaviour and its tests must not change). Differences: validates the same way (`_validate`), `confirmed_by=None`, `confirmed_by_ai=True`, tasks `created_by_ai=True`, the `confirmed` event has `origin=AI`, `actor=None`; then `_rules_guarded`.
- `confirm_document` (a person): sets `confirmed_by_ai=False` (it is by definition not the AI's).
- `steps.run_pipeline`: after (a), when `autopilot_in_force(...).confirm` and a payload exists: `data = decide_confirmation(...)`; skip when None; skip when `organise` is also in force and an OPEN suggestion remains; else `confirm_automatically` in a guarded savepoint. On success the status stays `done` (do not overwrite it with `needs_review`); on any failure the document becomes `needs_review` as today. The log line gains only counts/booleans.
- `services.reopen_document(*, workspace: Workspace, user: User, document_id: UUID) -> Document` — locks the document; 404 `document_not_found`; unless `status == done and confirmed_by_ai`: 409 `document_not_reopenable` ("Dit document kun je niet opnieuw controleren."). Then: status `needs_review`; `sender`, `document_type`, `summary` emptied, `letter_date`, `confirmed_at`, `confirmed_organisation` None, `summary_by_ai` False, `confirmed_by_ai` False; delete the document's tasks with `created_by_ai=True` and status `open`; write a `reopened` event (origin `user`, actor = user). Labels, addressees, extractions and suggestions stay.
- `POST /workspaces/{workspace_id}/documents/{document_id}/reopen`, `operation_id="reopenDocument"`, any member (same auth and error set as `confirmDocument`), returns `DocumentOut`.

- [ ] Tests first, pure decision: one passing baseline, then one test per guard rail, each differing from the baseline in exactly that one thing (missing sender; sender confidence 0.849; sender not in the letter (confidence 0); missing letter date; unknown document type word; unknown action type word; blank description; 21 actions; deadline not found in letter; deadline confidence 0.849; deadline yesterday; deadline before letter date; deadline value given but unparsable; an action without deadline while the text says "binnen 14 dagen na dagtekening"; a letter date "November 2026" whose quote has no day — build this one through `validate()` so the real validation gives it confidence 0). And: action without deadline passes with source `manual` when the text names no term; zero actions passes even when the text names a term. `mentions_term`: a table of phrases that must and must not match ("binnen 14 dagen", "binnen twee weken", "uiterlijk 1 december 2026", "vóór 01-12-2026", "de vervaldatum"; not: "voor u", "voor meer informatie", "binnenkort").
- [ ] Tests first, pipeline: switch off → `needs_review`; on and all rails hold → `done`, `confirmed_by_ai`, `confirmed_by is None`, tasks `created_by_ai`, event origin `ai` without actor, rules of trigger `confirmed` ran; a failing rail → `needs_review` with proposals intact; both switches on with an open proposal left → `needs_review`; server switch false → `needs_review`; a person's later `confirm_document` on a reopened document works and leaves `confirmed_by_ai` False; the existing `tests/test_documents_review_api.py` passes unchanged.
- [ ] Tests first, reopen: happy path (fields emptied, open AI task gone, a ticked-off AI task stays, event written); person-confirmed document → 409; `processing`/`needs_review` → 409; other workspace → 404; after reopen the normal confirm works.
- [ ] Implement; run the three new files plus `tests/test_documents_review_api.py tests/test_pipeline_steps.py tests/test_pipeline_validation.py tests/test_pipeline_jobs.py`; lint, typecheck; commit `feat(autopilot): confirm a letter when every guard rail holds, and reopen`.

### Task 5: What clients see: fields, filter, counter, contract

**Files:**
- Modify: `backend/apps/documents/schemas.py`, `backend/apps/documents/services.py`, `backend/apps/documents/api.py`, `docs/api/openapi.json` (via `make openapi`), `web/src/api/schema.d.ts` (via `cd web && pnpm gen:api`)
- Test: `backend/tests/test_autopilot_api_fields.py`; `backend/tests/test_openapi_contract.py` must stay green untouched

**Interfaces — Produces:**
- `DocumentSummaryOut`: `confirmed_by_ai: bool = False`, `auto_label_ids: list[UUID] = []`, `auto_addressee_ids: list[UUID] = []` (use `Field(default_factory=list)` as the neighbours do) — the links on the document now with `automatic=True`. No extra query per document in the list (prefetch as `label_ids` does).
- `TaskOut.created_by_ai: bool = False`.
- `listDocuments`: query parameter `automatic: bool = False`; when true only documents with `confirmed_by_ai` or at least one automatic label/addressee link; combines with the other filters.
- `InboxCountsOut.automatic: int = 0` — the number of such documents.
- Descriptions of `listDocuments`, `undoDocumentEvent`, `DocumentEventOut` comments updated for the new origins and kind.

- [ ] Tests first: fields on summary and detail after an automatic run and after undo/reopen (they go back to empty/false); the filter alone and combined with `person_id`; the counter; a list query count that does not grow with the number of documents (follow how existing list tests assert queries, if they do).
- [ ] Implement; `make openapi`; `cd web && pnpm gen:api && pnpm typecheck`; run the new file plus `tests/test_openapi_contract.py tests/test_documents_api.py` (or the file that covers `listDocuments`); lint, typecheck; commit `feat(autopilot): show what was automatic in the API`.

### Task 6: Web

**Files:**
- Modify: `web/src/routes/ai.tsx` (+ test), `web/src/components/document-table.tsx` (+ test), `web/src/routes/document.tsx` (+ test), `web/src/components/document-history.tsx`, `web/src/routes/post.tsx` (+ test), `web/src/locales/*`
- Create if it keeps `ai.tsx` readable: `web/src/components/autopilot-panel.tsx` (+ test)

**Consumes:** `getAiAutopilot`, `updateAiAutopilot`, `reopenDocument`, `listDocuments?automatic=true`, the new fields (generated in `web/src/api/schema.d.ts`). Existing building blocks: `web/src/components/ui.tsx`, `confirm-dialog.tsx`, the patterns of `ai.tsx` and `settings.tsx`.

**Behaviour:**
- AI page, section "Automatisch verwerken", for an admin only: hidden while AI is off; when the server forbids it, one sentence saying the server's administrator switched this off, no switches. Two switches: "Brieven zelf onder personen en labels zetten" and "Brieven zelf bevestigen". Switching one **on** opens a confirmation dialog showing `notice` from the API verbatim, with buttons "Aanzetten" and "Annuleren"; only "Aanzetten" sends `acknowledged: true`. Switching off saves at once. API errors show their Dutch message.
- A badge "Automatisch door AI" in the document table and on the document page when `confirmed_by_ai` or either auto list is not empty. On the document page, labels/addressees in the auto lists are marked individually (a small "automatisch" hint is enough; keep the page calm).
- History: lines for origin `ai` ("Automatisch door AI toegevoegd"), `name_match` ("Automatisch toegevoegd: naam herkend"), a `confirmed` event of origin `ai` ("Automatisch bevestigd door AI"), kind `reopened`; the undo button appears wherever `can_undo`.
- Document page, when `confirmed_by_ai`: button "Zelf controleren" with a confirm dialog ("De brief gaat terug naar Te controleren. De taken die de AI maakte en die nog open staan, verdwijnen. Je controleert de brief daarna in de app."), calling `reopenDocument`.
- Post page: a filter "Automatisch door AI" that sets `automatic=true`.
- Tests query by role (Vitest), as the neighbours do: switches render per state (AI off, forbidden, member), the dialog gates the PUT and sends `acknowledged`, badge shows/hides, reopen calls the endpoint, filter passes the parameter.

- [ ] Tests first, then implement; `pnpm extract`; `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build`; commit `feat(web): autopilot switches and the "Automatisch door AI" mark`.

### Task 7: Documents of record

**Files:** `CLAUDE.md`, `docs/SPEC.md`, `docs/DECISIONS.md`, `docs/PRIVACY.md`, `docs/GLOSSARY.md`, `docs/SELFHOST.md`

- `CLAUDE.md` rules: keep "nothing automatic repeats a dismissed one"; add the autopilot rules in the file's style: the switches are written only by `apps.pipeline.settings_service.configure_autopilot`; automatic changes have no actor and are marked (`automatic`, `confirmed_by_ai`, `created_by_ai`, event origin `ai`/`name_match`); the pipeline applies only on a `processing` document and reads `autopilot_in_force` at that moment; `decide_confirmation` stays pure.
- `SPEC.md`: line 26 ("AI stelt voor, de gebruiker bevestigt") gains the opt-in exception; the pipeline section describes (a), (b), the guard rails with the real signals, and the limits (ruling 4). The app still never pays, sends or answers anything.
- `DECISIONS.md`: one numbered item with the rulings of the spec (short).
- `PRIVACY.md`: automated processing without a person in between is opt-in per workspace, logged, marked and reversible; no extra data leaves the server because of it.
- `GLOSSARY.md`: "Automatisch door AI" / autopilot, "Zelf controleren" / reopen.
- `SELFHOST.md`: `BRIEVIE_AI_AUTOPILOT_ALLOWED`.

- [ ] Write; check every claim against the code as built (run nothing but `make lint` for the markdown-adjacent tests, e.g. `tests/test_docs*.py` if present); commit `docs: the autopilot opt-in and its limits`.
