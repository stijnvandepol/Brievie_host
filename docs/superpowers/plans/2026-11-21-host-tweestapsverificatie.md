# Two-step sign-in (TOTP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user adds an authenticator app and gets recovery codes; a password sign-in then needs a code. The server owner can require it (server admins, or everyone) with a grace period; an admin can clear a lost factor with a trace; and once it exists, a password sign-in through the public address needs it. This is what makes `BRIEVIE_PUBLIC_WEB=true` (external access) a normal choice instead of a red line.

**Architecture:** Pure TOTP (`apps/accounts/totp.py`, standard library) and two keys derived from the master key (`apps/common/crypto.py`). Storage in two server tables (`SecondFactor`, `RecoveryCode`). One decision point, `apps.accounts.signin.begin_sign_in`, replaces every direct call to `start_session`; a pending step lives in the anonymous Django session; `start_session` itself refuses a user who needs a second factor and brings no `SecondFactorPassed`. Rate limits per pending sign-in, per account (doubling lockout) and per address. Web: a code step after the password, a "Beveiliging" page, an admin "Tweestapsverificatie wissen".

**Tech Stack:** Django 6.1, Django Ninja, PostgreSQL 18, Procrastinate (existing hourly session job), `cryptography` (already present: `HKDFExpand`); React + TanStack + React Aria + Lingui, `qrcode.react` (already present, used by `pair-app.tsx`). **No new dependency** (spec 1: no `pyotp`, no `django-otp`), so no DECISIONS line for a library; Task 14 records the design rulings.

**Spec:** `docs/superpowers/specs/2026-11-20-tweestapsverificatie-design.md` (read it first; its "Besluit" lines bind), cited "S 3" etc. External access: `docs/superpowers/specs/2026-11-21-externe-toegang-design.md` and its plan `docs/superpowers/plans/2026-11-21-host-externe-toegang.md` (branch `docs/external-access-design`), cited "EA 7.6", "EA T5".

**Where today's code is ahead of the spec (code wins):**
- The authority for clearing someone's second factor is `apps.accounts.authority.account_authority` as it is now: three conditions (single membership and no server admin; the target reads no private post there; the actor reads all post there), not the two of S 7.
- `start_session` has four callers: `accounts/services.py` `login` (password) and `set_up_server` (setup), `workspaces/invitations.py` (invitation), `accounts/resets.py` `complete_password_reset` (reset). All four go through `begin_sign_in`.
- `views.login` calls `signin_limits.remember_browser` after the password; with a second step that cookie would mark a half-signed-in browser as known. It moves to the moment the sign-in completes.
- `createPairingCode` needs `recently_authenticated` (15 minutes); a device token is itself a second factor (S 8), so for a user with a confirmed factor it also needs a session that passed the second step (Task 10).

## Merge points

- **M1, before the web sign-in is reachable from the internet** (`BRIEVIE_PUBLIC_WEB=true`): Tasks 1-14. TOTP, storage, `begin_sign_in` on all four callers plus the guard, the code step with lockout, recovery codes, set-up with the "Ik heb ze bewaard" gate, admin clear and `clear_second_factor`, pairing needing the second step, the server-wide rule `BRIEVIE_REQUIRE_SECOND_FACTOR` with its grace, the public-route rule (EA B2) and `BRIEVIE_SECOND_FACTOR_AVAILABLE = True`.
- **M2, after:** Task 15 (per-workspace requirement), Task 16 ("Nieuwe telefoon" and the clock warning). Until M2 a user replaces a phone by removing and adding again (allowed while not required) or through a recovery code plus an admin clear.
- The app-only tunnel (`BRIEVIE_PUBLIC_WEB=false`) does not wait for any of this (EA B1).

## Global Constraints

- Read `CLAUDE.md` "Rules in short"; all of it applies. In particular:
- `apps.accounts.sessions.start_session` stays the only caller of Django's `login`. Nobody calls `start_session` directly except `begin_sign_in` and `verify_sign_in_code`; a test greps `backend/apps` for `start_session(` and allows exactly those two modules.
- Every password check (`login`, `verify_password`, `reauthenticate`, and the new `removeMySecondFactor` and `clearUserSecondFactor`) goes through `_refuse_when_locked` and `_checking_password_for("<via>")` like `reauthenticate`, so axes and `apps.accounts.signin_limits` count it. A wrong password there is 400 `wrong_password`, as `reauthenticate` answers.
- No secret, code or recovery code in a log line, an audit value, a notice, an exception message or any response after the one that shows it. Those responses set `Cache-Control: no-store`.
- `apps.audit.services.record()` is the only audit writer; only codes of the closed list, only allowed value keys (Task 3 extends them additively).
- New tables: UUID `id`, `created_at`, `updated_at` (`BaseModel`). `SecondFactor` and `RecoveryCode` are server tables like `UserSession`: no `workspace_id`, no RLS.
- API: only under `/api/v1`, additive. Every new field has a default; nullable fields are primitives with a default; no `anyOf`; explicit camelCase `operation_id`; errors through `ApiError` with a Dutch B1 sentence. Every new endpoint `auth=session_auth` (a device token gets 401) except `verifySignInCode` (`auth=None`, pending state) and the three set-up calls, which also accept the pending `setup` state (`PendingSetupAuth`). Adding 202 to `login` is additive for the contract test (success codes only grow).
- User-facing text in Dutch (B1) and English: `gettext` + `make messages` (backend), Lingui + `pnpm extract` (web); terms from `docs/GLOSSARY.md`.
- Every mutating service in `transaction.atomic`. The code check updates `last_used_step`, `failed_count` and the session in one transaction under `select_for_update` on the `SecondFactor` row. A failed check must keep its counters: raise the `ApiError` after the counter's transaction committed (as `login` keeps axes' row).
- Builders never run `docker`, `./brievie`, `make dev` or `make test` on the Mac, never connect to a local PostgreSQL. Locally: tests that need no database (Task 1), `make lint typecheck`, `cd web && pnpm test lint typecheck`. Database tests run in CI.
- Conventional commits, each ending with the line `Co-Authored-By: Claude <noreply@anthropic.com>`.

## Review Focus

- A reset link, an invitation or the setup must not sign in a user who has a confirmed factor: each answers 202 `{step: "code"}` and no `UserSession` exists → Task 6 `test_every_caller_stops_at_the_code_step` (parametrised over the four `via` values).
- A code seen once cannot be used again, not even inside its 90-second window, also by two requests at the same moment → Task 4 `test_code_for_a_used_step_refused` and Task 7 `test_two_parallel_sign_ins_with_one_code_one_wins` (CI, two connections).
- The 202 response sets no `brievie_known` cookie; only a completed sign-in does → Task 7 `test_known_browser_cookie_only_after_the_code`.
- A pending session reaches no normal endpoint: `getMe`, `listDocuments`, `createPairingCode` answer 401 → Task 6 `test_pending_session_is_not_signed_in`.
- A wrong code after the per-account lockout still counts and still answers 429 even with the right code; the lockout doubles up to 24 hours → Task 4 `test_lockout_doubles_and_caps`.
- A workspace admin may clear a factor only where `account_authority` allows it (three conditions); a member with private post there needs a server admin → Task 9 `test_clear_follows_account_authority`.
- With external access, a password sign-in on the public route of a user without a factor is refused before any session, and the right password still counts as a sign-in attempt only once → Task 13 `test_public_route_needs_a_second_factor`.

---

### Task 1: Derived keys and TOTP (pure)

**Files:**
- Modify: `backend/apps/common/crypto.py`
- Create: `backend/apps/accounts/totp.py`, `backend/apps/accounts/recovery_codes.py`
- Test: `backend/tests/test_accounts_totp.py`, `backend/tests/test_accounts_recovery_codes.py`, extend `backend/tests/test_common_crypto.py` (all without database)

**Interfaces — Produces:**
- `crypto.SECOND_FACTOR_INFO = b"brievie/second-factor/v1"`, `crypto.RECOVERY_CODES_INFO = b"brievie/recovery-codes/v1"`; `derive_second_factor_key(master_key: bytes) -> bytes`, `derive_recovery_code_key(master_key: bytes) -> bytes` (32 bytes, `HKDFExpand(SHA256)` as `derive_backup_password`); `second_factor_aad(user_id: UUID) -> bytes` = `b"second-factor/<user_id>"`.
- `totp.STEP_SECONDS = 30`, `DIGITS = 6`, `WINDOW = 1`, `DRIFT_MAX = 10`.
- `totp.new_secret() -> bytes` (20 bytes, `secrets.token_bytes`); `totp.code_at(secret: bytes, step: int) -> str` (RFC 4226 HOTP, HMAC-SHA1, dynamic truncation, zero-padded); `totp.step_of(now: datetime) -> int`.
- `totp.match(secret: bytes, code: str, *, now: datetime, last_used_step: int) -> int | None` — the matching step in `step-WINDOW .. step+WINDOW` that is greater than `last_used_step`, compared with `hmac.compare_digest`; `None` otherwise. A code that is not exactly 6 digits after removing spaces is `None`.
- `totp.drift_steps(secret: bytes, code: str, *, now: datetime) -> int | None` — the step distance 2..`DRIFT_MAX` (either side) where the code matches, for the set-up hint only.
- `totp.otpauth_uri(secret: bytes, *, host: str, email: str) -> str` — `otpauth://totp/Brievie%20(<host>):<email>?secret=<base32 no padding>&issuer=Brievie%20(<host>)&algorithm=SHA1&digits=6&period=30`, labels percent-encoded; `totp.grouped_secret(secret: bytes) -> str` (base32 in groups of four).
- `recovery_codes.ALPHABET` = the pairing alphabet (`apps.devices` code alphabet, imported, not copied); `new_codes() -> list[str]` (ten, `XXXXX-XXXXX`); `normalise(code: str) -> str`; `code_hash(key: bytes, code: str) -> str` (HMAC-SHA256 hex of the normalised code).

- [ ] **Step 1: Write the failing tests.** `test_rfc6238_sha1_vectors` (appendix B: secret `12345678901234567890`, times 59, 1111111109, 1111111111, 1234567890, 2000000000, 20000000000 → the 8-digit vectors' last 6 digits); `test_window_accepts_one_step_either_side_only`; `test_step_boundary` (29.999 s and 30.000 s); `test_code_for_a_used_step_refused` (`last_used_step` equal to the matching step → `None`; one below → match); `test_drift_reported_between_2_and_10_steps` (3 steps off → 3, 11 off → `None`, 1 off → `None` since it matches); `test_non_digits_refused`; `test_otpauth_uri_encodes_label_and_has_no_padding`; `test_derived_keys_differ_from_each_other_and_from_the_master_key`; `test_recovery_codes_are_ten_unique_from_the_alphabet`; `test_recovery_hash_ignores_hyphen_and_case`; `test_recovery_hash_depends_on_the_key`.
- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_accounts_totp.py tests/test_accounts_recovery_codes.py tests/test_common_crypto.py` → FAIL.
- [ ] **Step 3: Implement** (about 40 lines for TOTP; no Django import in `totp.py` and `recovery_codes.py`).
- [ ] **Step 4: Run** → PASS; `make lint typecheck`.
- [ ] **Step 5: Commit** `feat(accounts): TOTP and recovery codes from the standard library`.

### Task 2: Tables, settings and the server-wide rule

**Files:**
- Modify: `backend/apps/accounts/models.py`, `backend/brievie/settings.py`, `backend/apps/common/checks.py`, `deploy/.env.example`, `deploy/docker-compose.yml` (pass the two variables to `api` and `worker`), `backend/tests/test_deploy_files.py`
- Create: one `accounts` migration
- Test: `backend/tests/test_accounts_second_factor_models.py`, extend `backend/tests/test_system_checks.py`

**Interfaces — Produces:**
- `SecondFactor(BaseModel)`: `user` (`OneToOneField(User, CASCADE, related_name="second_factor")`), `secret_sealed: BinaryField`, `confirmed_at: DateTimeField(null)`, `last_used_step: BigIntegerField(default=0)`, `failed_count: IntegerField(default=0)`, `first_failed_at: DateTimeField(null)`, `locked_until: DateTimeField(null)`, `lockouts: IntegerField(default=0)`, and for the replacement of Task 16 (added now, so M2 needs no migration of this table) `pending_secret_sealed: BinaryField(null)`, `pending_since: DateTimeField(null)`. One row per user.
- `RecoveryCode(BaseModel)`: `user` (FK, CASCADE, `related_name="recovery_codes"`), `code_hash: CharField(64, unique=True)`, `used_at: DateTimeField(null)`.
- `User.second_factor_required_since: DateTimeField(null)`; `UserSession.second_factor: BooleanField(default=False)`.
- Settings: `BRIEVIE_REQUIRE_SECOND_FACTOR: str` (`none` | `server_admins` | `everyone`, default `none`; another value → `ImproperlyConfigured`), `BRIEVIE_SECOND_FACTOR_GRACE_DAYS: int` (default 7, 0-30, else `ImproperlyConfigured`).
- Check `brievie.W004` (next free id; read `checks.py` first): `BRIEVIE_REQUIRE_SECOND_FACTOR` is not `none` while no server admin has a confirmed factor → warning "Nog geen serverbeheerder heeft tweestapsverificatie aan." (database read guarded like the other checks).

- [ ] Tests first: defaults; `RecoveryCode.code_hash` unique; deleting a user removes both; settings parse and refuse a bad value (settings module reload with env, as existing settings tests do); `.env.example` documents both variables commented out; compose passes them to `api` and `worker`.
- [ ] Run (CI) → FAIL; implement; `makemigrations --check` clean; lint, typecheck.
- [ ] Commit `feat(accounts): second-factor tables and the server-wide rule`.

### Task 3: Audit codes and notices

**Files:**
- Modify: `backend/apps/audit/codes.py`, `backend/apps/accounts/models.py` (`AccountNotice.Code`), `backend/apps/accounts/notices.py`, `backend/locale/en/LC_MESSAGES/django.po` (`make messages`), one `accounts` migration if `choices` need it
- Test: extend `backend/tests/test_audit_codes.py` (or the existing closed-list test) and `backend/tests/test_accounts_notices.py`

**Interfaces — Produces (additive):**
- New `AuditCode.SECOND_FACTOR_RECOVERY_USED` ("Herstelcode gebruikt", values `left`), `AuditCode.SECOND_FACTOR_RECOVERY_RENEWED` ("Nieuwe herstelcodes", no values).
- `ALLOWED_VALUES`: `SECOND_FACTOR_ADDED` → `{"replaced"}`; `SECOND_FACTOR_CLEARED` → `{"via", "devices_revoked"}` (`via`: `admin`, `command`); `SIGN_IN_FAILED` → `{"step"}` (`password`, `second_factor`, `recovery_code`); `SIGN_IN_SUCCEEDED` → `{"via", "second_factor"}` (`totp`, `recovery_code`, `none`, `sso`); `SIGN_IN_LOCKED_OUT.via` gains the value `second_factor` (comment); `SESSION_ENDED.reason` gains `second_factor_required` and `second_factor_cleared` (comment). Update the comments that list the values.
- New notice codes with messages (Dutch from the spec, English in the catalogue): `SECOND_FACTOR_LOCKED` ("Iemand heeft op %(date)s met jouw wachtwoord geprobeerd in te loggen, maar had de code niet. Verander je wachtwoord als jij dat niet was."), `SECOND_FACTOR_REQUIRED` ("Zet tweestapsverificatie aan vóór %(until)s. Daarna kun je pas inloggen als je het hebt aangezet."), `SECOND_FACTOR_RECOVERY_USED` ("Op %(date)s is met een herstelcode op je account ingelogd. Je hebt nog %(count)s herstelcodes."). `SECOND_FACTOR_CLEARED` exists and is kept.

- [ ] Tests first: each new code is in the closed list with its keys; a value key outside the list still raises; every notice code has a Dutch message and an English translation; no message holds a placeholder for a code or secret.
- [ ] Run → FAIL; implement; `make messages` and fill the English; run; lint, typecheck.
- [ ] Commit `feat(audit): codes and notices for two-step sign-in`.

### Task 4: The second-factor service

**Files:**
- Create: `backend/apps/accounts/second_factor.py`
- Test: `backend/tests/test_accounts_second_factor.py` (database: CI; a fake clock through a `now` argument)

**Interfaces — Consumes:** Tasks 1-3, `apps.common.crypto.seal`/`open_sealed` (read the module for the real names), `apps.common.crypto.master_key()` (or the existing accessor). **Produces:**
- `@dataclass(frozen=True) class SecondFactorPassed: user_id: UUID; method: Literal["totp", "recovery_code", "none", "sso"]` — constructed only in this module (a module-private token object passed to `__init__`; `__post_init__` refuses another), plus `passed_without_factor(user)` and `passed_by_sso(user)` for `begin_sign_in` and the SSO path.
- `LOCK_AFTER = 5`, `LOCK_COUNT_WINDOW = timedelta(minutes=15)`, `FIRST_LOCK = timedelta(minutes=15)`, `MAX_LOCK = timedelta(hours=24)`; `lock_length(lockouts: int) -> timedelta` (15 min × 2^(n-1), capped).
- `has_factor(user: User) -> bool` (confirmed only).
- `begin_setup(user: User) -> SetupSecret` (`secret_text`, `otpauth_uri`): creates or replaces an **unconfirmed** row (refuses 409 `second_factor_exists` when a confirmed one exists; Task 16 adds replacement).
- `confirm_setup(user: User, code: str, *, now: datetime) -> list[str]`: the code must match the unconfirmed secret (drift 2-10 steps → 409 `second_factor_clock_drift` with the spec's sentence; else 400 `second_factor_invalid`); sets `confirmed_at`, `last_used_step`; deletes old recovery codes, stores ten new hashes; audit `second_factor_added`; returns the ten codes (shown once).
- `verify_code(user: User, code: str, *, now: datetime) -> SecondFactorPassed` and `verify_recovery_code(user: User, code: str, *, now: datetime) -> SecondFactorPassed`: under `select_for_update`; locked → 429 `second_factor_locked` (`Retry-After`); match → reset `failed_count`, `first_failed_at`, `lockouts`, store the step (TOTP) or `used_at` (recovery, audit `second_factor_recovery_used` with `left`, notice); no match → count (window `LOCK_COUNT_WINDOW`), on the fifth lock with `lock_length`, notice `SECOND_FACTOR_LOCKED`, audit `sign_in_locked_out` (`via` = `second_factor`); audit `sign_in_failed` (`step`). The counters commit before the error is raised (a nested `transaction.atomic` that returns a result, then raise outside it).
- `renew_recovery_codes(user: User, code: str, *, now: datetime) -> list[str]` (needs a valid TOTP code; audit `second_factor_recovery_renewed`).
- `remove_factor(request: HttpRequest, *, password: str, code: str, now: datetime) -> None` (own account; password through the axes/`signin_limits` path with via `second_factor_remove`; code checked; 409 `second_factor_required` when `requirement_for(user).required`; deletes the row and the codes; audit `second_factor_removed`).
- `clear_factor(user: User, *, revoke_devices: bool, via: Literal["admin", "command"], actor: AuditActor) -> int` (deletes the row and codes, ends sessions (`end_sessions`, audit `session_ended` reason `second_factor_cleared`), revokes devices when asked (the existing device revoke service, one call per device), audit `second_factor_cleared` with `via` and `devices_revoked`, notice `SECOND_FACTOR_CLEARED` with the actor's name (none for `command`); returns the number of revoked devices).
- `purge_unconfirmed(*, now: datetime) -> int` (rows with `confirmed_at` null older than one hour).

- [ ] Tests first: set-up then confirm returns ten codes and stores only hashes; a sealed secret does not open with another user's AAD; drift hint at 3 steps; a confirmed factor refuses a second `begin_setup`; `test_code_for_a_used_step_refused`; `test_lockout_doubles_and_caps` (5 wrong → locked 15 min; after expiry 5 wrong → 30 min; … ≤ 24 h; the right code while locked → 429; the right code after → counters reset); a recovery code works once, case and hyphen ignored, the count left is in the audit row and the notice; renewal stops the old codes; `remove_factor` with a wrong password counts for axes and `signin_limits` and answers 400; remove while required → 409; `clear_factor` ends sessions, revokes devices only when asked, writes the audit row and the notice; no audit value, notice value or log record contains the secret, a code or a recovery code (capture logs; scan every `AuditEvent.values` and `AccountNotice.values`).
- [ ] Run (CI) → FAIL; implement; run; lint, typecheck.
- [ ] Commit `feat(accounts): set up, check and clear a second factor`.

### Task 5: Who must have it

**Files:**
- Create: `backend/apps/accounts/second_factor_policy.py`
- Test: `backend/tests/test_accounts_second_factor_policy.py`

**Interfaces — Produces:**
- `@dataclass(frozen=True) class Requirement: required: bool; by_server: bool; by_workspaces: tuple[str, ...]; grace_until: datetime | None`.
- `requirement_for(user: User, *, now: datetime) -> Requirement` — server rule (`everyone`; `server_admins` and `user.is_server_admin`); the workspace rule is added by Task 15 through one function `_workspace_requirements(user)` that returns `()` until then. Strictest wins (S 5). `grace_until` = `second_factor_required_since + grace`; `None` when not required or the user has a factor.
- `note_required_since(user: User, *, now: datetime) -> None` — sets `second_factor_required_since` the first time a requirement applies (atomic `UPDATE … WHERE second_factor_required_since IS NULL`), clears it when no requirement applies any more.
- `in_grace(req: Requirement, *, now: datetime) -> bool`.

- [ ] Tests first: `none` → never; `server_admins` → admins only; `everyone` → all; required since set once and kept on a second call; grace ends after `BRIEVIE_SECOND_FACTOR_GRACE_DAYS`; grace 0 → setup at once; a user who has a factor is never "in grace".
- [ ] Run (CI) → FAIL; implement; run; lint, typecheck; commit `feat(accounts): decide who must use two-step sign-in`.

### Task 6: `begin_sign_in`, the pending step and the guard in `start_session`

**Files:**
- Create: `backend/apps/accounts/signin.py`
- Modify: `backend/apps/accounts/sessions.py` (`start_session(..., second_factor: SecondFactorPassed | None)`), `backend/apps/accounts/services.py` (`login`, `set_up_server`), `backend/apps/accounts/resets.py` (`complete_password_reset`), `backend/apps/workspaces/invitations.py` (accepting), their API functions (return type)
- Test: `backend/tests/test_accounts_signin.py`, adjust the four existing callers' tests where they assert a session

**Interfaces — Produces:**
- `PENDING_KEY = "brievie_pending_sign_in"`, `PENDING_LIFETIME = timedelta(minutes=5)`, `PENDING_MAX_FAILURES = 5`.
- `@dataclass(frozen=True) class SignedIn: user: User` and `@dataclass(frozen=True) class StepNeeded: step: Literal["code", "setup"]; expires_in_seconds: int`; `SignInResult = SignedIn | StepNeeded`.
- `begin_sign_in(request: HttpRequest, user: User, *, via: str, method: str = UserSession.Method.PASSWORD, now: datetime | None = None) -> SignInResult` — S 3: no factor needed → `start_session(..., second_factor=passed_without_factor(user))` and, in grace, notice `SECOND_FACTOR_REQUIRED`; a confirmed factor → pending `code`; required, none, grace over → pending `setup`. Writing the pending state cycles the session key (`request.session.cycle_key()`), stores `{user_id, via, method, step, started_at, failures}` with no secret. SSO (`method == sso`) → `passed_by_sso`. Calls `note_required_since` first.
- `pending(request: HttpRequest, *, now: datetime) -> Pending | None` (expired → removed, `None`).
- `start_session` raises `RuntimeError("second factor not passed")` when `second_factor` is `None`, or its `user_id` differs, or `requirement_for(user).required and not in_grace` and the pass is `none`, or `has_factor(user)` and the pass is `none`. It sets `UserSession.second_factor` true for `totp`, `recovery_code` and `sso`, and adds `second_factor` to the `sign_in_succeeded` audit values.
- The four callers return `SignInResult`; their endpoints answer 200 `UserOut` for `SignedIn` and 202 `SignInStepOut` for `StepNeeded`. The schema `SignInStepOut` and the 202 entries of the three existing operations land in this task (the endpoints need them); Task 7 adds the code endpoint.

- [ ] Tests first: `test_every_caller_stops_at_the_code_step` (parametrised: password, setup (a factor cannot exist at setup: instead assert setup with `BRIEVIE_REQUIRE_SECOND_FACTOR=server_admins` and grace 0 gives `setup`), invitation with an existing account, reset); `test_no_factor_signs_in_as_today` (each caller, 200, `UserSession.second_factor` false); `test_grace_signs_in_with_a_notice`; `test_pending_session_is_not_signed_in` (`getMe`, `listMySessions`, `createPairingCode` → 401; no `UserSession` row); `test_pending_expires_after_five_minutes`; `test_session_key_cycled_on_pending`; `test_start_session_without_a_pass_raises` (a user with a factor, `None` and `passed_without_factor`); `test_only_two_modules_call_start_session` (source grep of `backend/apps`).
- [ ] Run (CI) → FAIL; implement; run the new file plus `tests/test_accounts_*.py`, `tests/test_workspaces_invitations*.py`; lint, typecheck.
- [ ] Commit `feat(accounts): every sign-in path stops for the second step`.

### Task 7: API: the code step

**Files:**
- Modify: `backend/apps/accounts/api.py`, `backend/apps/accounts/schemas.py`, `backend/apps/accounts/services.py`, `docs/api/openapi.json` (`make openapi`), `web/src/api/schema.d.ts` (`pnpm gen:api`)
- Test: `backend/tests/test_accounts_signin_api.py`

**Interfaces — Produces:**
- (From Task 6) `SignInStepOut`: `step: str` (`code` | `setup`), `expires_in_seconds: int = 300`, as 202 of `login`, `setupServer`, `completePasswordReset` and accepting an invitation (read its operation id in `workspaces/api.py`). Here: `login` sets `remember_browser` only for `SignedIn`.
- `POST /auth/login/code`, `operation_id="verifySignInCode"`, `auth=None`, throttle `ClientAddressThrottle("second_factor_code", "10/m")` (`apps.common.throttling`), body `SignInCodeIn`: `code: str = ""`, `recovery_code: str = ""` (exactly one; else 422 `validation_error`). 200 `UserOut` (signs in through `start_session` with the pass; `remember_browser` for the pending email); 401 `second_factor_invalid` ("Code klopt niet. Probeer het opnieuw."); 401 `sign_in_expired` ("Je bent te lang bezig geweest. Log opnieuw in."); 429 `second_factor_locked` ("Te vaak een verkeerde code. Probeer het over 15 minuten opnieuw."). Five wrong codes in one pending sign-in drop the pending state (next answer `sign_in_expired`).
- `UserOut.has_second_factor: bool = False`; `SessionOut.second_factor: bool = False`.

- [ ] Tests first: login with a factor → 202 `{step: "code"}`; `test_known_browser_cookie_only_after_the_code`; right code → 200, session `second_factor` true, audit `sign_in_succeeded` with `second_factor=totp`; wrong code → 401, audit `sign_in_failed` `step=second_factor`; five wrong in one pending → `sign_in_expired`; recovery code path; locked → 429 with `Retry-After`; `test_two_parallel_sign_ins_with_one_code_one_wins` (two pending sessions, same code, two threads with their own connections: one 200, one 401); the endpoint never answers a code or secret; contract test passes (202 added, no `anyOf`).
- [ ] Run (CI) → FAIL; implement; `make openapi`, `pnpm gen:api`; run; lint, typecheck.
- [ ] Commit `feat(accounts): sign in with a code after the password`.

### Task 8: API: own second factor ("Beveiliging")

**Files:**
- Create: `backend/apps/accounts/pending_auth.py` (`PendingSetupAuth`)
- Modify: `backend/apps/accounts/api.py`, `schemas.py`, `docs/api/openapi.json`, `web/src/api/schema.d.ts`
- Test: `backend/tests/test_accounts_second_factor_api.py`

**Interfaces — Produces** (S "API sketch"; every response with a secret or codes sets `Cache-Control: no-store`):
- `GET /me/second-factor` `getMySecondFactor` (session): `SecondFactorOut` `enabled: bool = False`, `required: bool = False`, `required_by: str = "none"` (`none` | `server` | `workspace`), `required_by_workspaces: list[str] = []`, `grace_until: str | None = None`, `recovery_codes_left: int = 0`.
- `POST /me/second-factor/setup` `beginSecondFactorSetup` (session or pending `setup`): `SetupSecretOut` `secret: str`, `otpauth_uri: str`.
- `POST /me/second-factor/setup/confirm` `confirmSecondFactorSetup` (session or pending `setup`): in `code`; out `RecoveryCodesOut` `recovery_codes: list[str]`. With a session: ends the user's other sessions (D 7.2) and marks this one `second_factor`. With the pending `setup` state: signs in (`start_session` with the `totp` pass).
- `POST /me/second-factor/recovery-codes` `renewRecoveryCodes`: in `code`; out `RecoveryCodesOut`.
- `DELETE /me/second-factor` `removeMySecondFactor`: in `password`, `code`; 204; 400 `wrong_password`; 409 `second_factor_required`.
- `PendingSetupAuth`: accepts only a pending state with step `setup` for the three set-up operations; every other endpoint keeps `session_auth`.

- [ ] Tests first: device token → 401 on all five; `no-store` on the three secret responses; the pending `setup` state reaches only the three set-up calls (`getMe` 401); confirm through pending signs in with `second_factor` true; confirm with a session ends the other sessions; the secret is shown only by `beginSecondFactorSetup` (a second `GET` never returns it); `removeMySecondFactor` wrong password counts for axes.
- [ ] Run (CI) → FAIL; implement; `make openapi`, `pnpm gen:api`; run; lint, typecheck.
- [ ] Commit `feat(accounts): manage your own second factor`.

### Task 9: Admin clear and the management command

**Files:**
- Modify: `backend/apps/accounts/api.py`, `schemas.py`, `backend/apps/accounts/user_actions.py`
- Create: `backend/apps/accounts/management/commands/clear_second_factor.py`
- Test: `backend/tests/test_accounts_clear_second_factor.py`, `backend/tests/test_accounts_clear_second_factor_command.py`

**Interfaces — Produces:**
- `POST /users/{user_id}/second-factor/clear` `clearUserSecondFactor`, `auth=session_auth`: in `password` (the admin's own; axes/`signin_limits` path, via `second_factor_clear`), `revoke_devices: bool = True`; out `ClearSecondFactorOut` `devices_revoked: int = 0`. Authority `account_authority(actor, user_id)` (404 `user_not_found`, 403 `server_admin_only`, 403 `own_account`). An admin who has a factor must act from a session with `second_factor` true (403 `second_factor_session_required`, "Log opnieuw in met je code om dit te doen."). 404 `second_factor_not_set` when the member has none.
- `manage.py clear_second_factor --email <address> [--keep-devices]`: `clear_factor(..., via="command", actor=system)`; prints a Dutch line with the number of revoked devices, never the email of anyone else; exit 1 for an unknown address.

- [ ] Tests first: `test_clear_follows_account_authority` (server admin anyone; workspace admin only under the three conditions; a member who reads private post there → 403 `server_admin_only`); wrong admin password → 400 and counted; devices revoked by default, kept with `revoke_devices=false`; the member's sessions ended; notice "… heeft op … je tweestapsverificatie gewist."; audit `second_factor_cleared` with `via=admin`, `devices_revoked`; after clearing, a member who must have it goes straight to `setup` at the next sign-in (no new grace); the command clears and audits with `via=command`.
- [ ] Run (CI) → FAIL; implement; `make openapi`, `pnpm gen:api`; run; lint, typecheck.
- [ ] Commit `feat(accounts): admins and the server can clear a lost second factor`.

### Task 10: Pairing and re-authentication follow the second step

**Files:**
- Modify: `backend/apps/accounts/sessions.py` (`recently_authenticated` gains `with_second_factor: bool = False`), `backend/apps/devices/api.py` or its service (`createPairingCode`), `backend/apps/accounts/services.py` (`reauthenticate` takes an optional `code`)
- Test: extend `backend/tests/test_devices_pairing*.py` (read the existing names first), `backend/tests/test_accounts_password_api.py`

**Interfaces — Produces:**
- `createPairingCode`: for a user with a confirmed factor, the session must have `second_factor` true; otherwise 403 `second_factor_session_required`. Sessions signed in before the factor was confirmed are ended at confirm anyway (Task 8); this closes the grace case and SSO-less leftovers.
- `reauthenticate` body gains `code: str = ""`; for a user with a factor, an empty or wrong code answers 400 `second_factor_invalid` (counted as in Task 4); the window then also requires the session's `second_factor`. Users without a factor: unchanged.

- [ ] Tests first: a user with a factor and a `second_factor` session pairs as today; the same user's session without it → 403; `reauthenticate` with password only → 400 for a user with a factor, 200 for one without; the `device_paired` notice still arrives.
- [ ] Run (CI) → FAIL; implement; `make openapi`, `pnpm gen:api`; run; lint, typecheck.
- [ ] Commit `feat(devices): pairing a phone needs the second step when it is on`.

### Task 11: The hourly job

**Files:**
- Modify: `backend/apps/accounts/jobs.py` (`remove_stale_sessions`, cron `23 * * * *`)
- Test: extend `backend/tests/test_accounts_jobs.py` (or the existing session job test)

**Interfaces — Produces:** in the same job, each in its own short transaction: `purge_unconfirmed(now)`; `note_required_since` for every active user (so a requirement set in `.env` starts the grace without waiting for a sign-in); end sessions with `second_factor` false of users whose grace ended and who have no factor (`end_sessions`, audit `session_ended` reason `second_factor_required`, no notice: the next sign-in explains).

- [ ] Tests first: an unconfirmed row older than an hour goes, a newer one stays; `everyone` set → `required_since` filled for all, idempotent; after the grace the sessions without the second step end and the ones with it stay; a user with SSO sessions keeps them.
- [ ] Run (CI) → FAIL; implement; run; lint, typecheck; commit `feat(accounts): start the grace and end sessions after it`.

### Task 12: Web: the code step, set-up and "Beveiliging"

**Files:**
- Create: `web/src/routes/security.tsx` (+ `.test.tsx`), `web/src/components/sign-in-code.tsx` (+ `.test.tsx`), `web/src/components/second-factor-setup.tsx` (+ `.test.tsx`)
- Modify: `web/src/routes/login.tsx`, `setup.tsx`, `password-reset.tsx`, `invitation.tsx` (all four handle 202), `web/src/routes/members.tsx` or `member-devices.tsx` ("Tweestapsverificatie wissen"), `web/src/routes/sessions.tsx` ("met tweestapsverificatie"), `web/src/routes/pair-app.tsx` (403 `second_factor_session_required` and the code field in the re-authentication dialog), `web/src/router.tsx`, `web/src/routes/settings.tsx` (link), `web/src/api/queries.ts`, `web/src/locales/*`

**Interfaces — Consumes:** Tasks 7-10.
- A 202 from any of the four flows shows `<SignInCode step=… />`: step `code` → "Typ de code uit je authenticator-app." with a 6-digit field (`inputmode="numeric"`, `autocomplete="one-time-code"`) and the link "Telefoon kwijt? Gebruik een herstelcode."; step `setup` → `<SecondFactorSetup pending />`.
- `<SecondFactorSetup>`: the S 9 three steps, the QR (`QRCodeSVG` from `qrcode.react`, as `pair-app.tsx`) and the grouped secret; after confirm the ten codes with "Downloaden" (a `.txt` made in the browser, no server round trip) and "Ik heb ze bewaard" (required before the dialog closes; Escape and the close button do nothing until then); the drift error shows its server sentence.
- "Beveiliging": status, "Aanzetten", "Nieuwe herstelcodes", "Uitzetten" (password + code), the requirement line ("Werkruimte Kantoor vraagt tweestapsverificatie." / server), grace date, codes left with the warning at two or fewer, the paired devices with "Ontkoppel" (link to the devices page).
- Admin: "Tweestapsverificatie wissen" with the admin's password and the checkbox "Ontkoppel ook de apparaten van <naam>" (checked).

- [ ] Tests first (by role, mock server as existing route tests): login → 202 → code → home; wrong code shows the server sentence; recovery-code link switches the field; setup step from login; the setup dialog cannot close before "Ik heb ze bewaard"; the codes download as a file; reset and invitation flows reach the code step; "Uitzetten" asks password and code; the admin clear sends `revoke_devices: true` by default; a member without rights sees no clear button; `pnpm extract` English filled.
- [ ] Run `cd web && pnpm test` → FAIL; implement; `pnpm extract`; `pnpm test lint typecheck build` → PASS.
- [ ] Commit `feat(web): two-step sign-in, set-up and the Beveiliging page`.

### Task 13: The public route needs the second step (EA B2) and the switch to "available"

**Depends on:** external access Tasks 2 (`apps.access.route.is_public_route`), 5 (`PublicRouteGate`) and 7 (`BRIEVIE_SECOND_FACTOR_AVAILABLE`, `brievie.W002`, `getExternalAccess.second_factor_available`). If they have not merged, this task waits; do not add a fallback import or a local copy of the route check.

**Gated on Stijn's answer to EA B2** (default: yes, as advised there). If he answers no, only the last two bullets below remain.

**Files:**
- Modify: `backend/apps/accounts/signin.py`, `backend/brievie/settings.py` (`BRIEVIE_SECOND_FACTOR_AVAILABLE = True`), `backend/apps/common/checks.py` and its tests (`test_w002_*`), `backend/apps/access/status.py` tests (`public_web_without_second_factor` no longer fires), `docs/SELFHOST.md`
- Test: `backend/tests/test_accounts_signin_public_route.py`

Built 2026-11-25; the password path answers as a wrong password instead of 403 (owner's
no-enumeration rule, docs/DECISIONS.md "The public route needs the second step").

**Interfaces — Produces:**
- In `begin_sign_in`, before anything else for `method == password`: when `is_public_route(request)` and the user has no confirmed factor → 403 `second_factor_needed_outside` ("Zet eerst thuis tweestapsverificatie aan. Daarna kun je ook van buiten inloggen."), no session and no pending state (a pending `setup` over the public route would let a password alone enrol a factor from the internet). The password was right, so the attempt is not a failure for axes; it is audited as `sign_in_failed` with `step=second_factor`.
- `BRIEVIE_SECOND_FACTOR_AVAILABLE = True`: `brievie.W002` stops warning for `BRIEVIE_PUBLIC_WEB=true`; the check `public_web_without_second_factor` becomes, instead, a warning `public_web_admin_without_second_factor` while a server admin has no factor (add to the closed check list of EA S 9, additive).
- With `BRIEVIE_PUBLIC_WEB=false` nothing changes (the gate already refuses sign-in on the public route).

- [x] Tests first: `test_public_route_needs_a_second_factor` (route header public, `BRIEVIE_BEHIND_CADDY=True`, `BRIEVIE_PUBLIC_WEB=True`: no factor → 403, no `UserSession`, no pending key; with a factor → 202 `code`); the private route is unchanged; a reset link or invitation accepted on the public route by a user without a factor → 403 too; the rewritten `test_w002_*`; the status check list.
- [x] Run (CI) → FAIL; implement; run; lint, typecheck.
- [x] Commit `feat(accounts): a password sign-in from outside needs the second step`.

### Task 14: Documents of record (end of M1)

**Files:**
- Modify: `docs/SELFHOST.md` (NTP on the server, Synology default on; `BRIEVIE_REQUIRE_SECOND_FACTOR`; `clear_second_factor`; "van buiten" needs it), `docs/PRIVACY.md` (secret sealed under a derived key, codes keyed-hashed, back-up holds the sealed secret), `docs/GLOSSARY.md` (Tweestapsverificatie = two-step sign-in, Herstelcode = recovery code, Authenticator-app), `docs/DECISIONS.md`, `CLAUDE.md` ("Rules in short": `begin_sign_in` is the only caller of `start_session` besides the code step; `start_session` refuses without a pass)

- [ ] DECISIONS lines: standard-library TOTP (no `pyotp`/`django-otp`, no dependency); derived keys; no session before the code; reset does not skip the second step; server-wide rule as an environment variable; per-account lockout with doubling; pairing and `reauthenticate` need the second step; the public route rule (EA B2) and `BRIEVIE_SECOND_FACTOR_AVAILABLE`.
- [ ] Commit `docs(accounts): two-step sign-in in the self-host, privacy and decision notes`. **M1 ends here: the web sign-in may be offered on the internet.**

---

## M2

### Task 15: Required per workspace

**Files:**
- Modify: `backend/apps/workspaces/models.py` (`Workspace.require_second_factor = BooleanField(default=False)`, `second_factor_grace_days = PositiveSmallIntegerField(default=7)`; on `Workspace`, not under RLS, like the other switches), `backend/apps/workspaces/settings_service.py` (`update_settings` gains both; audit `workspace_setting_changed` per changed value, as today), `backend/apps/accounts/second_factor_policy.py` (`_workspace_requirements`), workspaces API (`getWorkspaceSecurity`/`updateWorkspaceSecurity`, admin, `auth=session_auth`), web workspace settings, one migration
- Test: `backend/tests/test_workspaces_security.py`, extend Task 5's tests

- [ ] Tests first: a member of two workspaces is required when either requires it (strictest); grace per workspace from when it first applied, the shortest wins; switching off clears `required_since` when nothing else requires it; one audit row per changed value; device token 401; member 403; `grace_days` 0-30 else 422.
- [ ] Run (CI) → FAIL; implement; `make openapi`, `pnpm gen:api`; web tests; lint, typecheck; commit `feat(workspaces): require two-step sign-in per workspace`.

### Task 16: "Nieuwe telefoon" and the clock warning

**Files:**
- Modify: `backend/apps/accounts/second_factor.py` (`begin_replacement`, `confirm_replacement` using `pending_secret_sealed`), API (`beginSecondFactorReplacement`, `confirmSecondFactorReplacement`, session only, `no-store`), `web/src/routes/security.tsx`, `web/src/routes/home.tsx` (server admin: compare the `Date` header of the last API answer with the browser clock; more than 30 s → "De klok van de server loopt niet gelijk. Inloggen met een code kan dan mislukken.")
- Test: extend Task 4 and Task 8 test files; web tests

- [ ] Tests first: replacement needs a code (or recovery code) of the old secret; the old secret works until the new one is confirmed; confirm swaps atomically, audit `second_factor_added` with `replaced`, other sessions end; the clock line shows only for a server admin and only above 30 s.
- [ ] Run → FAIL; implement; run; commit `feat(accounts): move two-step sign-in to a new phone`.

---

## iOS

No change is required. The app never signs in with a password (no call to `/auth/login` in `Packages/BrievieKit/Sources`); it pairs with a code or a proof, and both start from a web session that, after Task 10, passed the second step when the account has one. Already paired phones keep working (a device token is itself a possession factor, S 8). Two follow-ups for the app, not blockers: the help text for pairing a new phone says "log eerst op de webinterface in met je code"; later a link from the app to "Beveiliging" on the web.

## Owner checks (manual, not blockers)

On the test server only with a test account: set up with Microsoft Authenticator and the iPhone Passwords app; sign in, sign out, sign in with a recovery code; clear as admin; with external access on, sign in through the tunnel with and without a factor.
