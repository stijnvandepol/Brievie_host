# Host "Mensen" (one list of people, each with an account state) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One section "Mensen" lists everyone in a workspace who can receive post, each with an account state (niet actief, uitgenodigd, actief, uitgeschakeld) and their real last sign-in; admins invite, resend, withdraw and switch off from the row or the detail; the addressee picker adds a new person; nobody's sight of any letter changes, and (slice C, Besluit B2) a member can be made to manage the post of a person without an active account.

**Architecture:** The Person row is the account slot; a `User` and `Membership` exist only from activation (spec ruling M1), so no visibility helper changes in slices A and B. Two display columns (`Person.deactivated_at`, `Person.former_user`) make "uitgeschakeld" distinguishable from "never had an account". The state is derived in one function (`apps.people.accounts`), served by one new read (`listPeople`); every mutation reuses the existing endpoints (`createPerson`, `createInvitation`, `renewInvitation`, `revokeInvitation`, `removeMember`). Slice C adds `Person.managed_by` and one term to two SQL helpers, through the existing `sight_change` preview and confirmation.

**Tech Stack:** Python 3.13, Django 6.1, Django Ninja, PostgreSQL 18 (forced RLS), pytest; React 19, TanStack Router/Query, React Aria Components, Lingui, Vitest, MSW. No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-21-mensen-en-accounts-design.md`, cited "S 3.1". Rights design and plan cited as in the spec ("S 4.1" of the rights design is written "RD 4.1" here; "WZW T38").

## Global Constraints

From `CLAUDE.md` (verbatim where quoted):

- "Every table: UUID `id`, `created_at`, `updated_at`. Every workspace content table: `workspace_id` (subclass `WorkspaceModel`) and RLS enabled **and forced**." No new table in this plan; `Person` is already one.
- "`mypy --strict` and `ruff check` clean. No `Any`, no bare `except`." Datetimes timezone-aware.
- "API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an explicit camelCase `operation_id`."
- "Every non-2xx body: `{"code": "<snake_case>", "message": "<Dutch sentence>"}` via `apps.common.errors.ApiError`."
- "Thin endpoints: validate, call one function in the app's `services.py`, return." (`listPeople` calls `people.services.list_people`.)
- "Every mutating service function is wrapped in `transaction.atomic`."
- "A field added to an existing API schema has a default … the contract test compares with `backend/tests/fixtures/openapi_1b.json` and `openapi_1c.json`." "Nullable fields in API schemas are primitives only, and nullable inputs have a default; the contract test forbids `anyOf`."
- "Endpoints that administer people (members, invitations, roles, accounts, sessions or devices of others …) use `auth=session_auth`: a device token gets 401." `listPeople` and `setPersonManager` are `session_auth`.
- "`apps.audit.services.record()` is the only writer of audit rows. It takes only a code of the closed list and the value keys that code allows, and a name only for people …"
- "Only `apps.workspaces.members` changes a role or removes a membership"; "`apps.accounts.sessions.start_session` is the only sign-in path". Neither changes here.
- "Only `apps.documents.organise` adds or removes a document's labels and addressees." The picker's "Nieuwe persoon" creates the person, then sets addressees through the existing endpoint.
- "No document content, tokens, passwords or BSNs in logs." Person names are BSN-masked on create (existing `_clean_name`).
- User-facing text Dutch B1 through `gettext` / Lingui `t`, with English (`make messages`, `pnpm extract`), terms from `docs/GLOSSARY.md`. After every API change: `make openapi`, `cd web && pnpm gen:api`, commit both generated files.
- Tests first for logic. Conventional commits, each ending with the trailer `Co-Authored-By: Claude <noreply@anthropic.com>`.

From the environment (binding for every task):

- **Backend tests run in CI only.** Never connect to the local PostgreSQL, never `make dev`, `make test`, `docker` or `./brievie`. A backend "run" step means: commit, push the branch with a draft PR (ask the owner once at the start), read the `backend` job (and `visibility-perf` in slice C). `make lint`, `make typecheck` and every `pnpm` command run locally.
- **Start points.** Slice A (Tasks 1–5) branches from `main` (`ba477f0` or later) and touches no file of `feat/wie-ziet-wat-5`. Slice B (Tasks 6–10) starts **after `feat/wie-ziet-wat-5` is merged** (it edits `routes/members.tsx`, `persons.tsx`, `departments.tsx`, `inbox.tsx`, `components/page.tsx`, `person-picker.tsx`, `invite-dialog.tsx`, `role-dialog.tsx`, `router.tsx`, both `.po` catalogues). Slice C (Tasks 11–15) only after the owner chose B2 "now", after slice A.
- Migration numbers: `people/0004`, `workspaces/0009`, `audit` none (codes are choices). If another branch lands a migration first (intake: `documents`, letter status: `documents`/`tasks`), renumber in the same commit.
- Never commit files ending in " 2" or " 3" (iCloud copies).

From the spec, not left to the builder:

- **States (S 3.1):** `deactivated` needs `deactivated_at`; `invited` = an invitation with this `person_id`, `accepted_at IS NULL`, `revoked_at IS NULL` (expired ones included, flagged); `active` = `user_id` set. Precedence: active > invited > deactivated > inactive.
- **Rulings M1–M7 (S 12)** hold; M4: `former_user` is read only by `apps.people.accounts` and the list.
- Departments are never in `listPeople` (M2).

## Review Focus

1. **No letter changes readers.** The migration and every changed writer leave `viewers()` exactly as before. → Task 1 (`test_migration_keeps_every_viewer`), Task 11 (`test_function_migration_without_managers_keeps_every_viewer`).
2. **An inactive or switched-off person can do nothing an account does.** No sign-in, reset link, pairing, session or member row. → Task 5 (`test_inactive_person_has_no_account_paths`).
3. **Account facts only to admins, list only to web sessions.** → Task 4 (`test_member_gets_people_without_account_columns`, `test_device_token_gets_401`).
4. **Re-inviting a switched-off person** gives them exactly their own post back, private letters included only with the bound address, and clears the switched-off mark. → Task 2 (`test_reinvite_switched_off_person_restores_sight`).
5. **Slice C widens only with a confirmation** and never to the admin themself on private post; activation and removal end management and tell the manager. → Task 13.

## File structure

```
backend/apps/people/models.py              # + deactivated_at, former_user (A); managed_by (C)
backend/apps/people/migrations/0004_person_account_state.py   (A)
backend/apps/people/migrations/0005_person_managed_by.py      (C)
backend/apps/people/accounts.py            # NEW: state derivation + PersonAccount rows (A)
backend/apps/people/services.py            # list_people, create_person audit, archive revokes (A); set_manager (C)
backend/apps/people/schemas.py             # PersonAccountOut (A), PersonManagerIn (C)
backend/apps/people/api.py                 # listPeople (A), setPersonManager (C)
backend/apps/workspaces/members.py         # _unlink_and_unassign sets deactivated_at/former_user (A); clears managed_by (C)
backend/apps/workspaces/invitations.py     # _link_person clears them (A); ends management (C)
backend/apps/audit/codes.py                # PERSON_CREATED (A); PERSON_MANAGER_SET/_CLEARED (C)
backend/apps/workspaces/visibility_sql.py  # my_post_ids, covered_post_ids (C)
backend/apps/workspaces/migrations/0009_visibility_managed.py  (C)
backend/apps/workspaces/visibility_settings.py, visibility_rules.py, policy_text.py (C)
backend/apps/documents/models.py           # DocumentReader.Reason.MANAGED (C)
backend/apps/documents/visibility.py       # desired_readers, recompute_where, viewer_reasons (C)
web/src/routes/people.tsx, people-detail.tsx (+ tests)   (B)
web/src/components/person-actions.tsx (+ test)           (B)
web/src/router.tsx, components/page.tsx, components/invite-dialog.tsx, components/person-picker.tsx, api/queries.ts (B)
docs/DECISIONS.md, docs/GLOSSARY.md
```

---

## Slice A: server (from `main`)

### Task 1: Two display columns, and the proof that sight stays

**Files:**
- Modify: `backend/apps/people/models.py`
- Create: `backend/apps/people/migrations/0004_person_account_state.py`, `backend/tests/test_people_account_columns.py`, `backend/tests/test_people_migration_viewers.py`

**Interfaces:**
- `Person.deactivated_at = DateTimeField(null=True, blank=True)`.
- `Person.former_user = ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=SET_NULL, related_name="+")`.
- `Person.save()` additionally refuses (`ValueError`) `deactivated_at` or `former_user` on a department, and `former_user` without `deactivated_at`.
- `CheckConstraint(Q(former_user__isnull=True) | Q(deactivated_at__isnull=False), name="people_person_former_user_needs_deactivated")`.

- [ ] **Step 1: Failing tests:**

```python
def test_new_columns_default_empty(): ...                    # deactivated_at None, former_user None
def test_department_cannot_be_deactivated(): ...             # ValueError
def test_former_user_needs_deactivated_at(): ...             # IntegrityError on raw update too
def test_deleting_user_keeps_person(): ...                   # SET_NULL
def test_migration_keeps_every_viewer(): ...                   # seed at people 0003 (both kinds, E on/off, D on/off,
                                                             # private letters with share + addressee readers,
                                                             # an unlinked person, a removed member), viewers() of
                                                             # every letter before == after migrating to 0004
def test_visibility_sql_never_reads_former_user(): ...       # "former_user" not in any string of visibility_sql
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement, `makemigrations` (reversible; no `RunPython`). For the proof use `django.db.migrations.executor.MigrationExecutor` (migrate `people` back to 0003, seed, read `viewers()`, migrate forward, read again; `transaction=True` test, workspace context entered as the owner role does in `people/0002`). No migrator fixture exists, and `tests/test_rules_preset_migration.py` calls a data function directly, which does not fit a schema migration. **Step 4:** Push → PASS; `make lint typecheck`.
- [ ] **Step 5:** Commit `feat(people): mark when a person's account was switched off`.

### Task 2: Switching off sets the mark, accepting clears it, archiving withdraws

**Files:**
- Modify: `backend/apps/workspaces/members.py` (`_unlink_and_unassign`), `backend/apps/workspaces/invitations.py` (`_link_person`), `backend/apps/people/services.py` (`create_person`, `update_person`), `backend/apps/audit/codes.py`
- Test: `backend/tests/test_people_account_lifecycle.py`

**Interfaces:**
- `_unlink_and_unassign`: the queryset update also sets `deactivated_at=now`, `former_user_id=user_id` (same statement, so no `save()` check for a non-member is needed).
- `_link_person`: `person.deactivated_at = None`, `person.former_user = None` with `user`; `update_fields` gains both.
- `AuditCode.PERSON_CREATED = "person_created", _("Persoon toegevoegd")`, values `_NONE`, target kind person; written by `create_person` for both kinds (a department too: "Persoon toegevoegd" names the target kind; keep one code).
- `update_person` archiving (`archived` false → true) revokes the open invitation naming the person (`invitations._revoke(..., reason="person_archived")`, through a new public `revoke_invitations_for_person(*, workspace_id, person_id, actor) -> int` in `invitations.py`, lock order: memberships are not taken, invitation rows `FOR UPDATE`).

- [ ] **Step 1: Failing tests:**

```python
def test_remove_member_marks_person_switched_off(): ...      # deactivated_at set, former_user = removed user, user None
def test_leave_marks_person_switched_off(): ...
def test_accept_for_switched_off_person_clears_mark(): ...
def test_reinvite_switched_off_person_restores_sight(): ...  # E off: their own letters visible again via viewers();
                                                             # private letter: email_required without address,
                                                             # reader row after accept with the bound address
def test_accept_by_other_account_still_allowed(): ...        # the invitation decides, not former_user (M4)
def test_create_person_records_person_created(): ...         # one row, no values, name of the person
def test_archiving_revokes_open_invitation(): ...            # invitation_revoked reason person_archived
def test_sight_unchanged_by_marking(): ...                   # viewers() after removal equals main's behaviour (P7 sorters)
```

- [ ] **Step 2:** Push → FAIL. **Step 3:** Implement. **Step 4:** Push → PASS (also the existing `test_workspaces_remove_member.py`, `test_workspaces_accept_invitation.py`, `test_visibility_invitations.py`).
- [ ] **Step 5:** Commit `feat(people): switching off, re-inviting and archiving keep the account state`.

### Task 3: The state, in one place

**Files:**
- Create: `backend/apps/people/accounts.py`, `backend/tests/test_people_accounts.py`
- Modify: `backend/apps/people/services.py` (`list_people`)

**Interfaces:**
```python
class AccountState(StrEnum): INACTIVE = "inactive"; INVITED = "invited"; ACTIVE = "active"; DEACTIVATED = "deactivated"

def account_state(*, user_id: UUID | None, deactivated_at: datetime | None,
                  has_open_invitation: bool) -> AccountState: ...   # pure; precedence of the plan's constraints

@dataclass(frozen=True)
class PersonAccount:
    person: Person; state: AccountState; role: str; sorts_post: bool; department_ids: tuple[UUID, ...]
    email: str; last_sign_in_at: datetime | None; last_app_use_at: datetime | None
    invitation_id: UUID | None; invitation_expires_at: datetime | None; invitation_expired: bool
    has_private_post: bool; can_manage_account: bool

def list_people(workspace: Workspace, *, viewer: Membership, state: str = "") -> list[PersonAccount]: ...
```
- `list_people`: persons of kind `person` from `list_persons` (its counts and order), memberships of the workspace in one query, users (`last_login`, `email`) of linked and former users in one, `Device` max `last_seen_at` (unrevoked) grouped in one, open invitations with a `person_id` in one, `access.private_post_readers` reused through `list_members`' rule for `can_manage_account` (call `workspaces.services.list_members` once and index by user id), `has_private_post` from one system query (`Document.objects.filter(visibility=RESTRICTED, addressee_ids__overlap=ids)` unnested, as the system, booleans only). For a viewer who is no admin: `email=""`, times `None`, invitation fields empty, `has_private_post=False`, `can_manage_account=False`. Unknown `state` → 422 `validation_error`.
- The email shown for a switched-off person is the former user's (prefill for "Opnieuw uitnodigen").

- [ ] **Step 1: Failing tests:**

```python
@pytest.mark.parametrize(...)  # all 16 combinations of user/deactivated/invitation
def test_account_state_precedence(...): ...
def test_list_people_states(): ...                 # one person per state, expired invitation flagged
def test_list_people_leaves_out_departments(): ...
def test_last_sign_in_from_last_login(): ...       # sign in through start_session -> last_sign_in_at set
def test_never_signed_in_is_null(): ...
def test_last_app_use_ignores_revoked_devices(): ...
def test_switched_off_shows_former_account(): ...  # email and last sign-in of former_user
def test_non_admin_gets_no_account_facts(): ...
def test_has_private_post_is_boolean_only(): ...   # also for a letter the admin cannot read; no count anywhere
def test_list_people_query_count(django_assert_max_num_queries): ...   # constant in the number of persons (<= 12)
```

- [ ] **Step 2–4:** Push → FAIL, implement, push → PASS.
- [ ] **Step 5:** Commit `feat(people): derive each person's account state`.

### Task 4: `listPeople`

**Files:**
- Modify: `backend/apps/people/schemas.py`, `backend/apps/people/api.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`
- Test: `backend/tests/test_people_list_api.py`

**Interfaces:**
- `GET /workspaces/{workspace_id}/people`, `operation_id="listPeople"`, `auth=session_auth`, any member (`workspace_member(..., min_role=VIEWER)`), query `state: str = ""`, `response={200: list[PersonAccountOut], 404: ErrorOut, 422: ErrorOut, **AUTH_ERRORS}`.
- `PersonAccountOut` exactly as S 8 (every non-identity field with a default; nullable fields primitives).

- [ ] **Step 1: Failing tests:**

```python
def test_admin_gets_people_with_account_columns(client): ...
def test_member_gets_people_without_account_columns(client): ...
def test_device_token_gets_401(client): ...
def test_other_workspace_is_404(client): ...
def test_state_filter(client): ...
def test_unknown_state_is_422(client): ...
def test_schema_has_no_anyof(): ...                 # via the contract test helpers
```

- [ ] **Step 2–4:** Push → FAIL, implement, `make openapi`, `cd web && pnpm gen:api`, push → PASS (`test_openapi_contract` against `openapi_1b.json` and `openapi_1c.json` unchanged).
- [ ] **Step 5:** Commit `feat(people): list people with their account state`.

### Task 5: Nothing an account does works without one

**Files:**
- Create: `backend/tests/test_people_no_account_paths.py`
- Modify (only if a test fails): `backend/apps/accounts/resets.py`, `backend/apps/devices/services.py`

- [ ] **Step 1: Tests** (they should pass at once; any failure is a finding to fix and record):

```python
def test_inactive_person_has_no_account_paths(client): ...
    # for a niet-actief, an uitgenodigd and an uitgeschakeld person: no User with a matching
    # name/email is created; listMembers omits them; createPasswordReset for person.id -> 404
    # member_not_found; listUserDevices/endUserSessions likewise; login with the invitation's
    # email -> the usual invalid-credentials answer; listPeople shows user_id None
def test_switched_off_user_cannot_sign_in_or_pair(client): ...   # last membership gone: is_active False
def test_invited_person_cannot_use_expired_or_revoked_link(client): ...
def test_activation_only_through_invitation(): ...  # no service but _link_person and link_member writes Person.user
                                                    # (grep test over apps/, like test_actor_callers)
```

- [ ] **Step 2:** Push → PASS (or fix). **Step 3:** `docs/DECISIONS.md`: "2026-11-2x: Mensen, slice A" with rulings M1–M5, M7 and the audit code. **Step 4:** Commit `test(people): an inactive person can do nothing an account does`.

---

## Slice B: web (after `feat/wie-ziet-wat-5` is merged)

### Task 6: "Mensen" in the main navigation, the list and the redirects

**Files:**
- Create: `web/src/routes/people.tsx` (`/w/$workspaceId/mensen`), `web/src/routes/people.test.tsx`
- Modify: `web/src/router.tsx` (route; `/personen` and `/leden` redirect in `beforeLoad`), `web/src/components/page.tsx` (one `NavItem` "Mensen", icon `people`, where "Personen" was; "Leden" removed; "Afdelingen" untouched), `web/src/api/queries.ts` (`peopleQuery(workspaceId, state)`), MSW handlers in `web/src/test-utils.tsx`
- Delete after the move: `web/src/routes/persons.tsx`, `members.tsx` and their tests, once every behaviour is covered by Tasks 6–8 (the builder lists which test moved where in the commit body)

- [ ] **Step 1: Failing Vitest tests (by role):** `shows one nav item Mensen and no Leden or Personen`, `old paths redirect to Mensen`, `lists people with status words` (Niet actief, Uitgenodigd, Actief, Uitgeschakeld), `shows Laatst ingelogd or Nooit ingelogd`, `filters by status` (radio group, sends `state`), `searches by name`, `member sees no e-mail, last sign-in or invitation columns`, `archived people only under Gearchiveerd`, `departments are not listed`.
- [ ] **Step 2:** `pnpm test` → FAIL. **Step 3:** Implement; dates with the existing date helpers (Europe/Amsterdam). **Step 4:** `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm extract` (Dutch + English). **Step 5:** Commit `feat(web): one Mensen section replaces Leden and Personen`.

### Task 7: Uitnodigen, Opnieuw versturen, Intrekken, Uitschakelen on the row

**Files:**
- Create: `web/src/components/person-actions.tsx` (+ test)
- Modify: `web/src/components/invite-dialog.tsx` (prop `person?: {id, name, email}`: person fixed and its picker hidden; email prefilled), `web/src/routes/people.tsx`

**Behaviour:**
- niet actief → button **Uitnodigen**; uitgeschakeld → **Opnieuw uitnodigen** (email prefilled); both `createInvitation` with `person_id`. A 422 `email_required` shows its sentence at the address field. An admin role runs WZW T42's preview dialog (409 `confirmation_required`).
- uitgenodigd → **Opnieuw versturen** (`renewInvitation`; the one-time link dialog with "De oude link werkt niet meer.") and **Intrekken** (`revokeInvitation`, confirm dialog); "Verlopen" badge when `invitation_expired`.
- actief → **Uitschakelen** (`removeMember`; when `has_private_post`, the sentence of S 4 in the dialog), Rol wijzigen, Verdeelt post (existing dialogs from branch 5), Apparaten (link to `/leden/$userId/apparaten`), Herstellink where `can_manage_account`.
- Only admins see actions; a member sees "Persoon toevoegen" only.

- [ ] **Step 1: Failing tests:** `invites a never signed in person from the row`, `reinvite prefills the former address`, `email required shows the server sentence`, `resend shows the new link once`, `withdraw asks first and refreshes the row to Niet actief`, `switch off warns about private post`, `switch off without private post has no warning`, `member sees no actions`.
- [ ] **Step 2–4:** as Task 6. **Step 5:** Commit `feat(web): invite, resend, withdraw and switch off from Mensen`.

### Task 8: The person's detail

**Files:**
- Create: `web/src/routes/people-detail.tsx` (`/w/$workspaceId/mensen/$personId`, + test)
- Modify: `web/src/router.tsx`, `web/src/routes/people.tsx` (name links to detail)

**Behaviour:** heading name and status; facts (Rol, Verdeelt post, Afdelingen, Laatst ingelogd, Laatst in de app, Uitgenodigd tot / Uitgeschakeld op); the same actions as the row (`person-actions.tsx`); "Naam en schrijfwijzen" (`updatePerson`, existing rules: an admin for a person with an account); Archiveren; "Brieven van <naam>" linking to Alle post filtered on the person; for an inactive person one sentence who sees their post now, from `getVisibility`'s switches (plan copy: E on "Iedereen in deze werkruimte ziet de post van <naam>.", else "De post van <naam> gaat naar wie post verdeelt."). If the letter-status plan has landed, its person status block moves here.

- [ ] **Step 1: Failing tests:** `shows the account facts for an admin`, `shows the invite action for a never signed in person`, `shows who sees the post of an inactive person in both switch states`, `unknown person shows not found`.
- [ ] **Step 2–5:** as Task 6; commit `feat(web): a detail page per person`.

### Task 9: "Nieuwe persoon" in the addressee picker

**Files:**
- Modify: `web/src/components/person-picker.tsx` (+ test), callers in `web/src/routes/inbox.tsx` and `web/src/routes/document.tsx` only if the picker's props change

**Behaviour:** typed text with no exact match (ignoring case and accents, the same normalisation as the server's `common.text.normalise`, ported as a small pure function with its own tests) → last option "Nieuwe persoon: ‘<tekst>’"; choosing it calls `createPerson` and selects the new person; a 409 `person_exists` selects the existing one instead; a 422 (BSN in the name) shows the server sentence. When the text equals a variant of an existing person, that person is listed first with "(ook: <variant>)". Viewers have no option (403 otherwise).

- [ ] **Step 1: Failing tests:** `offers a new person for an unknown name`, `creates and selects it`, `an existing name selects that person`, `variant match listed first`, `bsn in name shows the sentence`, `viewer cannot add`.
- [ ] **Step 2–5:** as Task 6; commit `feat(web): add a new person from the addressee picker`.

### Task 10: Words and records

**Files:** `docs/GLOSSARY.md` (Mensen → People; niet actief → not active; uitgenodigd → invited; actief → active; uitgeschakeld → switched off; nooit ingelogd → never signed in; laatst ingelogd → last signed in; Uitnodigen → Invite; Opnieuw uitnodigen → Invite again; Opnieuw versturen → Send again; Intrekken → Withdraw; Uitschakelen → Switch off; Nieuwe persoon → New person; Beheerd door → Managed by), `docs/DECISIONS.md` ("Mensen, slice B"), `docs/SPEC.md` (sections that name Leden/Personen pages point to Mensen).

- [ ] Commit `docs: Mensen words and decisions`.

---

## Slice C: "Beheerd door" (only when the owner chose B2 "now")

### Task 11: The column, the helpers, and the proof

**Files:**
- Modify: `backend/apps/people/models.py`, `backend/apps/workspaces/visibility_sql.py`, `backend/apps/documents/models.py` (`DocumentReader.Reason.MANAGED = "managed"`)
- Create: `backend/apps/people/migrations/0005_person_managed_by.py`, `backend/apps/workspaces/migrations/0009_visibility_managed.py` (`CREATE OR REPLACE` of `brievie_my_post_ids` and `brievie_covered_post_ids` from `visibility_sql`, reverse to the 0007 text), `backend/tests/test_visibility_managed_sql.py`
- Modify: `backend/tests/perf/data.py` (2 % of persons managed), `.github/workflows/visibility-perf.yml` path lists if needed

**Interfaces:**
- `Person.managed_by = ForeignKey(user, null=True, blank=True, on_delete=SET_NULL, related_name="+")`; `save()` refuses it on a department or while `user_id` is set; `CheckConstraint(Q(managed_by__isnull=True) | Q(user__isnull=True), name="people_person_managed_only_without_account")`.
- `brievie_my_post_ids(uid)`: `UNION ALL SELECT p.id FROM public.people_person p WHERE p.managed_by_id = uid AND p.user_id IS NULL AND p.kind = 'person'`.
- `brievie_covered_post_ids()`: `UNION SELECT p.id FROM public.people_person p WHERE p.managed_by_id IS NOT NULL AND p.user_id IS NULL`.

- [ ] **Step 1: Failing tests:** `test_function_migration_without_managers_keeps_every_viewer` (seed as Task 1; `MigrationExecutor` from `workspaces` 0008 to 0009; `viewers()` equal), `test_manager_sees_managed_persons_post`, `test_managed_person_no_longer_goes_to_sorters`, `test_department_cannot_be_managed`, `test_managed_requires_no_account`, `test_backup_check_still_finds_helpers` (`verify_restore`).
- [ ] **Step 2–4:** Push → FAIL, implement, push → PASS; read the `visibility-perf` job: every measured query within the M0 budget (DECISIONS "measured before it is built"); record the numbers in DECISIONS.
- [ ] **Step 5:** Commit `feat(visibility): a manager sees the post of the person they manage`.

### Task 12: The rule as data, readers and reasons

**Files:**
- Modify: `backend/apps/workspaces/visibility_settings.py` (`snapshot`: a member's `person_ids` include managed persons), `backend/apps/workspaces/visibility_rules.py` (docstring only: `person:<id>` covers "own or managed"), `backend/apps/documents/visibility.py` (`desired_readers`: reason `managed` for private letters of managed persons; `recompute_where(user_id=…)` includes managed persons; `viewer_reasons`: "beheert de post van %(person)s"), `backend/apps/workspaces/policy_text.py` (sentence for a member who manages someone), `backend/tests/visibility_transitions.py` (rows: manage set, manage cleared), `backend/tests/test_policy_text_truth.py` (states with a manager)
- Test: `backend/tests/test_visibility_managed_readers.py`

- [ ] **Step 1: Failing tests:** `test_snapshot_counts_managed_as_own`, `test_expected_sight_matches_viewers_with_manager` (all 16 switch states), `test_private_letter_gets_managed_reader`, `test_viewer_reasons_names_management`, `test_policy_text_names_every_viewer_with_manager`.
- [ ] **Step 2–5:** Push → FAIL, implement, push → PASS; commit `feat(visibility): managed post in the snapshot, readers and reasons`.

### Task 13: `setPersonManager`, and what ends management

**Files:**
- Modify: `backend/apps/people/services.py` (`set_manager`), `schemas.py` (`PersonManagerIn`), `api.py`, `backend/apps/workspaces/visibility_settings.py` (`MemberChange(kind="manage", user_id, person_id)` in `member_change_key` and the preview), `backend/apps/workspaces/members.py` (`_unlink_and_unassign` clears `managed_by` where the removed user manages; `sight_change` already wraps it), `backend/apps/workspaces/invitations.py` (`_link_person` clears `managed_by`; `notify_join` tells the former manager `sight_narrowed`), `backend/apps/audit/codes.py` (`PERSON_MANAGER_SET`, `PERSON_MANAGER_CLEARED`, values `{"member"}`), `backend/tests/test_task_release_callers.py` (`set_manager` releases)
- Create: `backend/tests/test_people_manager.py`

**Interfaces:**
- `set_manager(*, workspace, person_id, user_id: UUID | None, actor: AuditActor, confirmed: bool, preview_token: str) -> Person` in `transaction.atomic`: `_lock_for_sight` (admin by the locked rows, else 403 `forbidden`), person `FOR NO KEY UPDATE`; 409 `is_a_department`; 409 `person_has_account` when `user_id` set on the person; 404 `member_not_found`; 409 `cannot_grant_self` when the actor names themselves and the person has private post; `sight_change(narrowing=user_id is None, …)`; `recompute_where(person_id=…)`; `record_grant` for the new manager; `release_unseeing_assignees`; former manager's own export removed (as unlink); audit set/cleared with `member` = the manager's person name.
- `PUT /workspaces/{workspace_id}/persons/{person_id}/manager`, `operation_id="setPersonManager"`, `auth=session_auth`, admin; `PersonAccountOut.managed_by_user_id: UUID | None = None`.

- [ ] **Step 1: Failing tests:** `test_set_manager_needs_confirmation_when_gaining`, `test_stale_preview_token_is_409`, `test_cannot_manage_own_private_post_person`, `test_member_gets_403`, `test_device_token_gets_401`, `test_activation_ends_management_and_tells_manager`, `test_removing_manager_sends_post_to_sorters` (P7), `test_switching_off_with_manager_keeps_private_post_readable`, `test_audit_rows_name_the_manager`.
- [ ] **Step 2–4:** Push → FAIL, implement, `make openapi`, `pnpm gen:api`, push → PASS.
- [ ] **Step 5:** Commit `feat(people): an admin names who manages the post of a person without an account`.

### Task 14: Leak paths with a manager

**Files:** `backend/tests/test_leak_managed.py` (reuse `visibility_support` seeds)

- [ ] Tests: a non-manager member, with E off, sees none of the managed person's letters through lists, search, tasks, counts, export, suggestions, duplicates, trash (one case per path of RD 4.10, like `test_leak_lists.py`); the manager loses all of them the moment management ends (same transaction). Commit `test(visibility): managed post leaks nowhere`.

### Task 15: "Beheerd door" in the web

**Files:** `web/src/routes/people-detail.tsx`, `web/src/components/person-actions.tsx` (+ tests)

- [ ] Behaviour: on an inactive or switched-off person, admins see "Beheerd door" (member select, "Niemand"); a gain runs the preview dialog (WZW T42); the Uitschakelen dialog of a person with private post offers "Laat de post beheren door …" in the same step. Tests: `sets a manager after the preview`, `clears it without a preview`, `self with private post shows the server sentence`, `switch off offers a manager when there is private post`. Commit `feat(web): choose who manages a person's post`; DECISIONS "Mensen, slice C" (M6, the measurement).

---

## Other plans the build must update

- **Intake** (`origin/docs/intake-and-2fa-design`, c45b285, `docs/superpowers/plans/2026-11-20-host-inname-zonder-iphone.md` and its spec section 3.2a): `folder_targets` ("a person without an account is not active unless `added_by_admin`") follows Besluit B5. With the recommendation: every non-archived person of kind person is `active` whatever its account state; its test `a person without an account gets no folder until added_by_admin` becomes `every person gets a folder, active or not`; `addScanFolder` stays for restoring a stopped folder and for linking an unknown folder's name. Do not edit that branch from here; whoever builds second applies it.
- **Letter status** (`origin/docs/letter-status-design`): its status block on `routes/persons.tsx` moves to `routes/people-detail.tsx`.
- **"Wie ziet wat" web** (`feat/wie-ziet-wat-5`): no change to its tasks; slice B starts after its merge and reuses its preview dialog, role dialog, sorts-post control and Afdelingen page.

## iOS (planned separately in `Brievie_ios`)

- No change is required: `listPersons` and `createPerson` (the only person operations in `openapi-generator-config.yaml`) are unchanged, and "Nieuwe persoon" in review now creates an inactive account by the same call.
- Optional, small: mark persons without an account in the review picker ("geen account", from `user_id == nil`), so the scanner knows the letter goes to whoever sorts post. Words from GLOSSARY.
- Pairing is per account and unchanged; an inactive person has no account to pair.
- Mensen itself stays web-only (`listPeople` is `session_auth`).

## Not in this plan (and where each hooks in)

- Merging two persons: a later action on the Mensen detail, through `organise` with a preview.
- AI proposing an unknown name (Besluit B3): new output field in `pipeline/schema.py`, a suggestion kind in `DocumentSuggestion`, shown in the picker of Task 9.
- E-mailing invitations: no SMTP in Brievie.

## Self-review

- Every spec section maps to a task: 3.1/3.3 → Tasks 1–3; 3.4 → Task 3; 4 → Tasks 7, 11–15; 5 → Task 9; 6 → Tasks 4–5; 7 → Task 1; 8 → Tasks 4, 13; 9 → Tasks 6–8; 10 → "Other plans"; 11 → iOS.
- No new mutation path for accounts: activation stays `createInvitation`/`acceptInvitation`, switching off stays `removeMember`/`leaveWorkspace`.
- Slices A and B change no SQL helper and no reader row; slice C is the only change of sight and goes through `sight_change`.
