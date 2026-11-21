# Brievie host

Self-hosted Django server for Brievie. The product spec is `docs/SPEC.md` (read
sections 11, 12, 14, 18 and 19 before changing architecture). Decisions live in
`docs/DECISIONS.md`, Dutch-English terms in `docs/GLOSSARY.md`.

## Commands (from the repo root)

- `make dev`: start PostgreSQL 18 (creates `deploy/.env` from the example first)
- `make test`: pytest (runs as the non-superuser `brievie_owner` role)
- `make lint`: `ruff check` + `ruff format --check`
- `make typecheck`: `mypy --strict`
- `make migrate`: apply migrations as the owner role
- `make openapi`: write `docs/api/openapi.json`
- `uv run python manage.py configure_ai --workspace <id> --mode off|external|local [--supplier mistral|openai|claude] [--accept-external-ai]`: AI per workspace (`external` asks to type `akkoord`)
- `uv run python manage.py set_server_admin --email <address> [--remove]` and `reset_password --email <address>`: server admin and a lost password, on the server

Web (`web/`, from the repo root through `cd web &&`; Node 24+ and pnpm):

- `pnpm test`: Vitest (queries by role); `pnpm build`: `tsc --noEmit` + Vite build
- `pnpm lint`: Biome; `pnpm typecheck`: `tsc --noEmit`
- `pnpm gen:api`: regenerate `src/api/schema.d.ts` from `docs/api/openapi.json` (after `make openapi`)
- `pnpm extract`: update the Dutch text catalogue after changing visible strings

## Rules in short

- Code, comments, commits, table and field names in English. User-facing text in
  Dutch (B1): in the backend always through `gettext`, in `web/` always through Lingui
  (`t` macros; run `pnpm extract` afterwards).
- Every table: UUID `id`, `created_at`, `updated_at`. Every workspace content table:
  `workspace_id` (subclass `WorkspaceModel`) and RLS enabled **and forced**.
- `mypy --strict` and `ruff check` clean. No `Any`, no bare `except`.
- Money is `Decimal`; datetimes are timezone-aware.
- API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an
  explicit camelCase `operation_id`. Endpoints without auth say `auth=None`.
- Every non-2xx body: `{"code": "<snake_case>", "message": "<Dutch sentence>"}` via
  `apps.common.errors.ApiError`.
- Thin endpoints: validate, call one function in the app's `services.py`, return.
- Every mutating service function is wrapped in `transaction.atomic`, so raising inside
  it rolls back its own writes. An error response (`ApiError`, Ninja `HttpError`,
  `Http404`) does not roll back the request transaction; only a 500 does.
- No document content, tokens, passwords or BSNs in logs.
- Text goes to an external AI service only with a recorded consent for that address
  (`AISettings.consented_base_url` and the history `AIConsentEvent`, both written only by
  `apps.pipeline.settings_service.configure`). An AI API key is write-only: no response
  or log line holds it. It is sealed with its workspace and address in the AAD
  (`_key_aad`) and opens nowhere else.
- No new dependency without a line in `docs/DECISIONS.md`.
- Apps of phase 1c: `people` (persons, name matching), `labels`, `rules` (engine, executor,
  presets) and `tasks`; `documents.organise` and `documents.suggestions` order post.
- Only `apps.documents.organise` adds or removes a document's labels and addressees (it
  writes the history).
- A label or addressee proposal is a `DocumentSuggestion`; nothing automatic repeats a
  dismissed one.
- The AI autopilot (`auto_organise`, `auto_confirm`; both off by default) is written only by
  `apps.pipeline.settings_service.configure_autopilot`: admin, AI not `off`, `acknowledged`
  when a switch goes on, one audit row per changed switch. `configure` switches both off
  (audited) when AI goes off, the mode changes (external <-> local), or consent is granted
  for another address; another model in the same mode at the same address keeps them.
  `BRIEVIE_AI_AUTOPILOT_ALLOWED=false` stops it server-wide, also where a switch is stored
  as on.
- `updateAiSettings`, `previewAiSettings` and `updateAiAutopilot` are session-only
  (`auth=session_auth`): a device token gets 401.
- Automatic changes have no actor and are marked: `DocumentLabel`/`DocumentAddressee`
  `automatic`, `Document.confirmed_by_ai`, `Task.created_by_ai`, history origin `ai` or
  `name_match`. Never record a user for what nobody did.
- The pipeline applies only on a `processing` document under its row lock and reads
  `autopilot_in_force` at that moment. Nothing automatic happens (no apply, no confirm,
  whatever the switches) when processing failed or the organise step (`organise_processed`)
  failed; no confirm when applying failed; no confirm while the letter has an open
  duplicate pair (`duplicates.has_open_pair`) or the duplicate check failed.
- `apps.pipeline.autopilot.decide_confirmation` and `mentions_term` stay pure: no database.
  `mentions_term` blocks rather than guesses (linear-time regexes): a letter that names a term
  stays a proposal unless it has actions and every one has a deadline. An invoice due date
  needs a payment action, and every payment a deadline.
- The autopilot applies `ai` picks from `AUTO_APPLY_FROM`, `name_match` picks from
  `PRESELECT_FROM`, and a rule's label proposal (history origin `rule_autopilot`) only
  when `rule_labels_to_apply` names it: the rule holds on settled facts alone
  (`apps.rules.engine.holds_on_settled`: letterhead, confirmed sender and type, labels and
  persons on the letter; never an open proposal or the AI's guess), or the AI's own pick
  of that label has `ai_confidence` >= `AUTO_APPLY_FROM`. None when a matching rule's
  label was dismissed on the letter, more than `RULE_LABELS_MAX` qualify, or the admin
  agreed to a notice older than `RULE_LABELS_FROM_NOTICE`. Never a rule's addressee
  proposal. An `ai` pick of a person only when its quote names that person (`quote_names`).
- A person's confirm with `sets_organisation` clears `automatic` on the links that stay; the
  autopilot's change (`ai`, `name_match`, `rule_autopilot`) is then no longer undoable.
  Hand edits leave the flag alone.
- Raise `AUTOPILOT_NOTICE_VERSION` when the autopilot notice promises more; a switch
  agreed to under an older version reports `notice_outdated` and keeps the older scope.
- The rules engine (`apps.rules.engine`) stays pure: no Django import.
- No Redis, no Celery, no Node runtime container. Background jobs: Procrastinate, in an
  app's `jobs.py`; a job takes ids only and enters `workspace_context` itself, in short
  transactions (none open during an external call).
- Text is BSN-masked (`apps.common.privacy.mask_bsns`) before it is stored, logged or sent
  to an AI service. Document files are sealed (`apps.common.crypto`) before they touch disk.
- A field added to an existing API schema has a default (never newly required; the
  contract test compares with `backend/tests/fixtures/openapi_1b.json` and
  `openapi_1c.json`).
- Nullable fields in API schemas are primitives only, and nullable inputs have a default
  (the iOS generator needs both); the contract test forbids `anyOf`.
- Apps of phase 1d part A: `audit` (the log), `accounts` (sessions, resets, notices, account
  state), `devices` (pairing, proof, expiry) and `workspaces` (members, invitations).
- `apps.audit.services.record()` is the only writer of audit rows. It takes only a code of
  the closed list and the value keys that code allows, and a name only for people, devices,
  invitations and workspaces; a label, rule, document, export or session is an id.
  `brievie_app` may not UPDATE or DELETE audit rows or `AIConsentEvent` (a migration
  REVOKE, tested as that role); old rows go only through the purge functions.
- `apps.accounts.sessions.start_session` is the only sign-in path (the only caller of
  Django's `login`); a web session without a `UserSession` row is signed out.
- Endpoints that administer people (members, invitations, roles, accounts, sessions or
  devices of others, workspace settings, the log, exports, linking a person to a member)
  use `auth=session_auth`: a device token gets 401. A response that carries a secret once
  sets `Cache-Control: no-store`.
- Every password check of a sign-in path (`login`, `verify_password`) goes through
  `apps.accounts.signin_limits` (per-account slow-down) as well as axes. `createPairingCode`
  needs a session that signed in or called `reauthenticate` within 15 minutes, and every
  paired device leaves the owner a `device_paired` notice.
- Text is BSN-masked with `apps.common.privacy`; keep it linear-time (bounded quantifiers,
  no nesting, one pass over the label windows) and add every new missed form as a test.
- Only `apps.workspaces.members` changes a role or removes a membership (under the
  workspace's membership lock); only `apps.accounts.account_state` switches an account off or
  changes server-admin rights after setup (under `lock_active_server_admins`).
- Export files are made, read and removed only through the guarded functions in
  `apps/documents/export_files.py`.
- A member-facing read of documents starts from `apps.documents.visibility.visible_documents`
  (the one place that says which letters a member may see).
- Every member read or change of a duplicate pair starts from
  `apps.documents.duplicates.visible_pairs` (both letters through `visible_documents`); a
  merge never changes who sees a letter (409 `duplicate_visibility_differs`), trashes only
  through `trash.move_to_trash(reason="duplicate")`, and completes a twin task only when
  `complete_twin_tasks` is true (through `tasks.services.complete_task`). Detection runs only
  on arrival, as the system through the pipeline.
- A trashed letter (`Document.trashed_at`) is out of every read: through the seam, and off it
  every query filters `models.LIVE` (the policies do not know the trash). The trash itself is
  read only through `visibility.trashed_documents(membership)` (the same rights as the seam);
  a letter the member may not see answers as a missing one (`document_not_found`). A future
  agenda or reminder reads tasks through `apps.tasks.services` and so inherits the filter.
- Trash, restore, purge-now and empty go through `apps.documents.trash` as the caller;
  `apps.documents.purge` is the trash's only system caller and purges only under
  `backup_lock`. Only `apps/documents/page_files.py` removes a letter's page files, and it
  refuses on its own (all paths judged first, the storage root judged, no links followed);
  its tests make and remove files only under `tmp_path`. `DocumentStorage.delete` is for the
  upload rollback only. A retention term hooks in at `apps.documents.retention.check_removal`.
- Brievie never logs, audits or echoes a search term, nor puts it in a URL or an error
  message (PostgreSQL's own log can hold a failing statement; see PRIVACY). A BSN, also with
  other separators, the literal `[BSN]` and the words "bsn"/"burgerservicenummer" are removed
  from the term before use. Search is `POST searchDocuments`, never a `q` on a `GET`.
- Tests first for logic. Conventional commits.
