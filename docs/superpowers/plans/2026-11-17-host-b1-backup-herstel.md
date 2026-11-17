# Host B1: back-up en herstel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command (`./brievie backup`) makes a complete, encrypted, verified back-up of a running server into a folder, a restic REST server or S3-compatible storage; one command (`./brievie restore`) brings it back on an empty machine with the phones still paired; CI proves that on every change.

**Architecture:** A new Django app `apps.backup` holds all logic as services and management commands: a restic wrapper that builds argv lists and environments (never shell strings), a server-level `BackupRun` table, the manifest, the storage sweep, the restore steps and the verification. Two kinds of container run it: the always-on `keeper` (a Procrastinate worker on the queue `backup`, with the read-only `brievie_backup` role, the back-up password, the documents and the authority copy read-only, and the destination) and a one-off `admin` service behind a compose profile (superuser password and master key, only while it runs). `./brievie` in the repository root is a thin POSIX-shell wrapper around `docker compose` and `docker run`.

**Tech Stack:** As in 1c (Python 3.13, Django 6.1, PostgreSQL 18 with forced RLS, Procrastinate 3.11, psycopg 3, pytest). New binaries in the image: restic (pinned, from the official image) and `postgresql-client-18` (PGDG). No new Python dependency (HKDF is in `cryptography`).

**Spec:** `docs/superpowers/specs/2026-11-17-beheer-backup-export-updates-design.md` (cited as "D 4.6"); B1 is the row in D 8. Sections 4, 9, 11, 12 and 13 are binding. The independent reader's report (`beheer-lezerscontrole.md`, cited as "R F4") and `onderzoek-backup.md` in the session notes folder hold the code-level facts this plan relies on. Previous plan: `docs/superpowers/plans/2026-11-16-host-fase-1c.md`.

**Not in B1:** the nightly schedule, retention settings in `.env`, the weekly check and `restore-test` (B2); `update` (U1); the web card and API (B3); export and archive folder (E); append-only set-up, key change and SFTP (U4/follow-up). The keeper serves the `backup` queue only; a manually requested back-up applies the retention defaults.

## Global Constraints

- From `CLAUDE.md`: "Code, comments, commits, table and field names in English. User-facing text in Dutch (B1): in the backend always through `gettext`". Dutch is the source language (`gettext("…Dutch…")`, no `.po` files, `docs/DECISIONS.md` 2026-11-13).
- "Every table: UUID `id`, `created_at`, `updated_at`." `BackupRun` subclasses `apps.common.models.BaseModel`; it is server-level, **not** a `WorkspaceModel`, so no RLS (`tests/test_rls_coverage.py` passes unchanged).
- "`mypy --strict` and `ruff check` clean. No `Any`, no bare `except`." `make lint typecheck test` pass at the end of every task.
- "Datetimes are timezone-aware" (`django.utils.timezone.now()`; manifest times ISO 8601 with offset).
- "Thin endpoints: validate, call one function in the app's `services.py`, return." Here: every management command parses arguments, calls one service function, prints its result; logic never lives in a command or in `./brievie`.
- "Every mutating service function is wrapped in `transaction.atomic`." Exception, by the rule "Background jobs … in short transactions (none open during an external call)": a run's status changes are each their own `transaction.atomic()`; no transaction is open while restic, pg_dump or pg_restore run.
- "No document content, tokens, passwords or BSNs in logs." Also: no master key, back-up password, database password, repository credentials or file paths with names in a log line, a `BackupRun.reason`, or an exception message that reaches one. Everything restic prints passes `restic.redact()` first (D 4.5, R F12).
- "No new dependency without a line in `docs/DECISIONS.md`." restic and `postgresql-client-18` each get a line (Task 7).
- "Background jobs: Procrastinate, in an app's `jobs.py`; a job takes ids only." The back-up job takes `run_id` only.
- "Tests first for logic. Conventional commits." Every commit ends with the trailer line `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- From D 11, not left to the builder: **what a back-up contains** (D 4.2: a custom-format `pg_dump` by `brievie_backup` streamed into restic; the document files without `.tmp-*`; Caddy's authority; `deploy/.env` without the master key; the manifest), **the order of D 4.3** (dump first, then files) and **of D 4.6** (steps 1–8; Task 13 pins the shell order), **that the master key is never in a back-up or on the web**, and the checks of the restore (D 4.6 step 8: counts against the manifest, one page per workspace opened with the master key, authority fingerprint equal).
- `pg_dump` is never given `--enable-row-security` (D 4.2). The dump role is `brievie_backup` (`LOGIN`, `BYPASSRLS`, `NOSUPERUSER`, `CONNECT` on `brievie`, member of `pg_read_all_data`); the application roles stay without `BYPASSRLS`.
- The keeper never receives `BRIEVIE_MASTER_KEY`, `POSTGRES_PASSWORD` or `BRIEVIE_OWNER_PASSWORD` (D 4.5).
- Branch `feat/b1-backup` from `main`. Do not push. iCloud conflict copies (" 2", " 3") are never committed.

## Review Focus

1. **The back-up disk is not plugged in** (or the share is not mounted): the folder the keeper sees is an empty directory on the system disk. The run must fail with "destination not ready", never create a repository there. → Task 4 (`test_folder_without_marker_is_refused`, `test_init_on_the_disk_of_the_documents_is_refused`).
2. **A wrong master key (or back-up password) at restore**: one clear Dutch sentence, nothing written to `deploy/`, no volume created. → Task 9 (`test_fetch_with_a_wrong_password_writes_nothing`, `test_wrong_master_key_with_a_separate_password_is_caught_from_the_dump`), Task 12 (`test_restore_with_a_wrong_key_creates_no_volume`).
3. **The keeper is not running, or runs but does not pick up the job**: `./brievie backup` says so and how to start it; it never starts a second back-up container; a queued run that nobody takes ends the wait with a sentence, not a hang. → Task 12 (`test_backup_without_a_running_keeper_says_so_and_runs_nothing`), Task 6 (`test_wait_gives_up_when_nobody_picks_up_the_run`).
4. **Credentials in the repository string, or secrets in what restic prints**: a `rest:https://user:pass@host/` value is refused; the run's reason, the log and `status` never show the password, the back-up password or the repository's credentials. → Task 3 (`test_repository_with_credentials_is_refused`, `test_redact_removes_secrets_and_userinfo`), Task 6 (`test_failed_run_reason_and_log_hold_no_secret`).
5. **Restoring over an installation that holds data**: refused without `--replace`; with it, API and workers stop, a safety back-up is made first, the database is dropped and recreated. → Task 12 (`test_restore_refuses_an_installation_with_data`, `test_replace_stops_backs_up_then_drops`).

Also pinned (not in the five because a test already sits next to the code that owns it): a dump as the wrong role (Task 1 `test_owner_cannot_read_with_row_security_off`, Task 6 `test_run_fails_when_the_dump_role_cannot_bypass_rls`); a host from before B1 without the role (Task 1 `test_ensure_backup_role_is_idempotent`, Task 12 `test_every_command_brings_env_and_role_along`); a back-up from a newer Brievie (Task 9 `test_fetch_refuses_a_backup_with_unknown_migrations`).

## File Structure

```
brievie                                  # Create: POSIX-sh wrapper (start, backup, restore, status, sweep, recovery-sheet)
SELFHOST.md → docs/SELFHOST.md           # Create (English, like README and PRIVACY)
.gitignore                               # + brievie-herstelblad.html, deploy/.restore/, deploy/.env.before-restore-*
.github/workflows/ci.yml                 # backend env + role; stack checks keeper; new job `restore`
.github/scripts/restore-seed.py          # Create: seeds two workspaces, a letter each, a paired device
Makefile                                 # dev: init-env always, then ensure-backup-role
deploy/
  Dockerfile                             # restic + postgresql-client-18; /data/backup-config, /data/authority
  docker-compose.yml                     # postgres env; keeper; admin (profile); volumes
  caddy-publish-root.sh                  # + copies pki/authorities/local to /authority (10001, 0700/0600)
  init-env.sh  .env.example              # + BRIEVIE_BACKUP_DB_PASSWORD, DATABASE_BACKUP_URL, BRIEVIE_BACKUP_PASSWORD
  ensure-backup-role.sh                  # Create: idempotent role via `compose exec postgres psql`
  postgres-init/01-roles.sh              # creates owner/app, then includes the two files below
  postgres-init/database.psql            # Create: CREATE DATABASE + grants (also used by restore --replace)
  postgres-init/backup-role.psql         # Create: idempotent brievie_backup (ignored by the entrypoint: not .sh/.sql)
backend/
  brievie/settings.py                    # INSTALLED_APPS + backup settings
  apps/common/crypto.py                  # + derive_backup_password
  apps/backup/                           # Create: new app
    apps.py  models.py  migrations/0001_initial.py
    errors.py      # BackupError + REASONS
    database.py    # conninfo for backup/superuser roles, role and version checks
    locks.py       # advisory lock
    restic.py      # argv/env builders, run, redact, classify, parse
    destination.py # folder/rest/s3, marker, initialise
    manifest.py    # Manifest v1, build, JSON, unknown_migrations, brievie_version
    envcopy.py     # store_env_copy, restored_env
    services.py    # request_run, perform_run, wait_for_run, status_summary, initialise_destination
    jobs.py        # run_backup, retry_stalled_backup_jobs
    sweep.py  restore.py  verify.py  sheet.py
    templates/backup/recovery_sheet.html
    management/commands/  backup.py backup_init.py backup_status.py store_env_copy.py sweep_storage.py
                          restore_fetch.py restore_database.py restore_files.py verify_restore.py recovery_sheet.py
  tests/
    conftest.py                          # + backup_conninfo fixture
    backup_support.py                    # Create: fake restic / fake pg tools, upload helper
    test_backup_*.py  test_brievie_wrapper.py  test_init_env.py  test_deploy_files.py
docs/  DECISIONS.md  GLOSSARY.md  PRIVACY.md
```

`apps.backup` imports `apps.common`, `apps.devices.pki` (fingerprint), `apps.documents.intake` (`page_aad`, `page_path`); nothing imports `apps.backup` except its own commands and jobs.

---

### Task 1: Back-up secrets and the back-up role

**Files:**
- Modify: `backend/apps/common/crypto.py`, `deploy/init-env.sh`, `deploy/.env.example`, `deploy/postgres-init/01-roles.sh`, `deploy/docker-compose.yml` (postgres `environment` only), `Makefile` (`dev`), `.github/workflows/ci.yml` (job `backend` env), `backend/brievie/settings.py`, `backend/tests/conftest.py`, `docs/DECISIONS.md`
- Create: `deploy/postgres-init/database.psql`, `deploy/postgres-init/backup-role.psql`, `deploy/ensure-backup-role.sh`
- Test: `backend/tests/test_common_crypto.py`, `backend/tests/test_init_env.py`, `backend/tests/test_backup_role.py`

**Interfaces:**
- Produces: `apps.common.crypto.BACKUP_PASSWORD_INFO: bytes = b"brievie/backup-password/v1"` and `derive_backup_password(master_key: bytes) -> str`: `HKDFExpand(algorithm=SHA256(), length=32, info=BACKUP_PASSWORD_INFO).derive(master_key).hex()` (64 lower-case hex). HKDF-Expand only: the master key is already 32 uniformly random bytes (RFC 5869 §3.3), and it makes the value computable with `HMAC-SHA256(key=master_key, info || 0x01)` in shell. **This must never change** (D 13).
- Produces `.env` lines: `BRIEVIE_BACKUP_DB_PASSWORD=<hex 24>`, `DATABASE_BACKUP_URL=postgres://brievie_backup:<same>@localhost:<POSTGRES_PORT>/brievie` (host use: tests), `BRIEVIE_BACKUP_PASSWORD=<derive_backup_password(master key)>`.
- Produces: `settings.DATABASE_BACKUP: dict[str, object] | None` (`env.db("DATABASE_BACKUP_URL")` when set, like `DATABASE_OWNER`).
- Produces: `deploy/ensure-backup-role.sh` (run from the repo root; reads `BRIEVIE_BACKUP_DB_PASSWORD` from `deploy/.env`; exit 1 with a sentence when PostgreSQL is not running).
- Produces: conftest fixture `backup_conninfo() -> str`: libpq conninfo for the **test** database (`connection.settings_dict["NAME"]`) as `brievie_backup`, from `settings.DATABASE_BACKUP`; `pytest.fail("The test run needs DATABASE_BACKUP_URL … run make dev")` when unset (same style as the owner check).

- [ ] **Step 1: Failing tests**

```python
# test_common_crypto.py
def test_backup_password_has_a_fixed_vector():
    assert derive_backup_password(bytes.fromhex("5a" * 32)) == (
        "cc224115e04b5ef8d0fabc864ae85ef455c6ee2a24d4fef9182f6c0884257571")
def test_backup_password_differs_per_master_key():         # "00"*32 -> "43f6f658…" != the vector above

# test_init_env.py
def test_fresh_env_has_backup_lines(deploy):               # three lines, no change-me; URL password == BRIEVIE_BACKUP_DB_PASSWORD; port follows POSTGRES_PORT
def test_shell_derivation_equals_python(deploy):            # BRIEVIE_BACKUP_PASSWORD == derive_backup_password(bytes.fromhex(BRIEVIE_MASTER_KEY))
def test_existing_env_gets_exactly_the_missing_backup_lines(deploy):  # old file + appended lines only; derived from the file's own master key
def test_existing_env_with_all_lines_is_never_modified(deploy)        # replaces test_existing_env_with_a_master_key_is_never_modified
def test_init_env_never_prints_a_secret(deploy):            # stdout contains none of the generated values

# test_backup_role.py  (all @pytest.mark.django_db(transaction=True): a second connection must see the rows)
def test_backup_role_reads_rows_of_two_workspaces(ws_a, ws_b, backup_conninfo):
    # one Folder in each workspace (inside workspace_context); then with psycopg as brievie_backup:
    # SET row_security = off; SELECT count(DISTINCT workspace_id) FROM documents_folder  -> 2
def test_owner_cannot_read_with_row_security_off(ws_a):    # same SELECT as the test role (owner): raises psycopg.errors.InsufficientPrivilege
def test_backup_role_cannot_write(ws_a, backup_conninfo):  # INSERT INTO documents_folder … -> InsufficientPrivilege
def test_backup_role_attributes(backup_conninfo):          # pg_roles: rolbypassrls, not rolsuper, not rolcreaterole, not rolcreatedb; pg_has_role(…, 'pg_read_all_data', 'member')
def test_ensure_backup_role_is_idempotent():               # text test: backup-role.psql uses `\gexec` guard + ALTER ROLE …; 01-roles.sh includes it via ${BASH_SOURCE[0]}
```

- [ ] **Step 2:** `make test` → the new tests FAIL (role missing, function missing).
- [ ] **Step 3: Implement.**
  - `deploy/postgres-init/database.psql`: lines 23–33 of today's `01-roles.sh` moved verbatim (CREATE DATABASE, REVOKE/GRANT, `\connect brievie`, schema grants, default privileges).
  - `deploy/postgres-init/backup-role.psql` (needs psql variable `backup_password`):
    `SELECT 'CREATE ROLE brievie_backup' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'brievie_backup')\gexec`, then `ALTER ROLE brievie_backup LOGIN BYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT PASSWORD :'backup_password';` (`INHERIT`: the membership in `pg_read_all_data` must apply without `SET ROLE`). Then `GRANT CONNECT ON DATABASE brievie TO brievie_backup; GRANT pg_read_all_data TO brievie_backup;`.
  - `01-roles.sh`: requires `BRIEVIE_BACKUP_DB_PASSWORD`; keeps the two `CREATE ROLE`s inline; then `psql … -f "$here/database.psql"` and `psql … -v backup_password="$BRIEVIE_BACKUP_DB_PASSWORD" -f "$here/backup-role.psql"` with `here=$(dirname "${BASH_SOURCE[0]}")` (the entrypoint may source the file, so `$0` is not this script). The entrypoint ignores `*.psql`.
  - `ensure-backup-role.sh`: `docker compose --project-directory deploy -f deploy/docker-compose.yml exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 -q` over the local socket, script on **stdin** (`\set backup_password '<hex>'` then `\i /docker-entrypoint-initdb.d/backup-role.psql`), so the password is in no argv.
  - `init-env.sh`: generate `master` and `backup_db_pw` up front; new `derive()` = `printf 'brievie/backup-password/v1\001' | openssl dgst -sha256 -mac HMAC -macopt "hexkey:$1" | sed 's/^.*= *//'`, else `python3` stdlib `hmac` reading the key from stdin, else the existing error. New cases in the `while` loop for `BRIEVIE_BACKUP_DB_PASSWORD=`, `DATABASE_BACKUP_URL=` (sed `change-me-backup` and the port, as `DATABASE_URL`), `BRIEVIE_BACKUP_PASSWORD=`, `BRIEVIE_MASTER_KEY=` (uses `$master`). The existing-file branch becomes "append each missing line": master key (as now), then the DB password, the URL (port from the file's `POSTGRES_PORT`, default 5432) and the derived password from the file's master key. Header comment updated.
  - `.env.example`: a block "Back-up" with `BRIEVIE_BACKUP_DB_PASSWORD=change-me-backup`, `DATABASE_BACKUP_URL=postgres://brievie_backup:change-me-backup@localhost:5432/brievie`, `BRIEVIE_BACKUP_PASSWORD=change-me-backup-password` (comment: derived from the master key; an organisation sets its own before `./brievie backup --init --separate-password`), and commented `BRIEVIE_BACKUP_DIR=`, `BRIEVIE_BACKUP_REPOSITORY=`, `BRIEVIE_BACKUP_S3_ACCESS_KEY_ID=`, `BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY=`, `BRIEVIE_BACKUP_REST_USERNAME=`, `BRIEVIE_BACKUP_REST_PASSWORD=`, `BRIEVIE_BACKUP_APPEND_ONLY=false`.
  - compose `postgres.environment`: `BRIEVIE_BACKUP_DB_PASSWORD: ${BRIEVIE_BACKUP_DB_PASSWORD:?set BRIEVIE_BACKUP_DB_PASSWORD in deploy/.env (./brievie or sh deploy/init-env.sh adds it)}`.
  - `Makefile` `dev`: `sh deploy/init-env.sh` always (it is idempotent), `$(COMPOSE) up -d --wait postgres`, `sh deploy/ensure-backup-role.sh`.
  - `ci.yml` job `backend` env: `BRIEVIE_BACKUP_DB_PASSWORD: ci-backup`, `DATABASE_BACKUP_URL: postgres://brievie_backup:ci-backup@localhost:5432/brievie`.
  - `DECISIONS.md` new section "2026-11-17: Back-up and restore (B1)", first line: the role `brievie_backup` exists (read-only, `BYPASSRLS`, used by the keeper and the sweep only); this changes the premise of the 1d design (line 119 "deliberately do not have"): the **application's** roles still cannot bypass RLS. Second line: the derived back-up password (label, HKDF-Expand, stable for ever).
- [ ] **Step 4:** `make dev && make test lint typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(backup): a read-only back-up role and the back-up secrets`.

---

### Task 2: The `backup` app, the run record, the lock and database access

**Files:**
- Create: `backend/apps/backup/{__init__,apps,models,errors,database,locks}.py`, `backend/apps/backup/migrations/{__init__,0001_initial}.py`
- Modify: `backend/brievie/settings.py` (`INSTALLED_APPS` += `"apps.backup"`; `DATABASE_SUPERUSER` as `DATABASE_BACKUP` from `DATABASE_SUPERUSER_URL`)
- Test: `backend/tests/test_backup_models.py`, `backend/tests/test_backup_database.py`

**Interfaces:**
- Produces `apps.backup.models.BackupRun(BaseModel)`, table `backup_backuprun`, ordering `["-created_at"]`, index on (`kind`, `status`):
  - `Kind(TextChoices)`: `BACKUP="backup"`, `CHECK="check"` (B2 adds `restore_test`).
  - `Status(TextChoices)`: `QUEUED="queued"`, `RUNNING="running"`, `SUCCEEDED="succeeded"`, `FAILED="failed"`, `INTERRUPTED="interrupted"`. `OPEN = (QUEUED, RUNNING)` as a module constant `OPEN_STATUSES`.
  - `kind` CharField(20); `status` CharField(20, default queued); `requested_by` CharField(10, default `"cli"`; B3 adds `"web"`); `started_at`, `finished_at` DateTimeField(null); `destination_kind` CharField(10, blank: `""|folder|rest|s3`; never a path, D 4.8 "no paths with names of people"); `reason_code` CharField(50, blank); `reason` CharField(300, blank, Dutch, from `REASONS`); `size_bytes` BigIntegerField(null).
- Produces `apps.backup.errors`: `class BackupError(Exception)` with `code: str` and `message -> str` (`str(REASONS[code])`); `REASONS: dict[str, StrPromise]` (Dutch, `gettext_lazy`) with exactly these codes: `not_configured` ("Er is nog geen back-up ingesteld."), `destination_not_ready`, `same_disk`, `already_initialised`, `repository_has_credentials`, `repository_unsupported`, `repository_missing`, `wrong_password`, `repository_locked`, `destination_unreachable`, `destination_full`, `files_incomplete`, `dump_failed`, `backup_role_missing`, `pg_client_too_old`, `no_backup_password`, `busy`, `env_copy_has_secret`, `manifest_unreadable`, `newer_backup`, `run_not_found`, `wrong_master_key`, `restore_failed`, `interrupted_by_restore` ("Afgebroken door herstel."), `internal_error`, `restic_failed`. Sentences other than the two quoted are the builder's (D 11).
- Produces `apps.backup.database`:
  - `backup_conninfo(*, dbname: str | None = None) -> str` and `superuser_conninfo(*, dbname: str | None = None) -> str` (from `settings.DATABASE_BACKUP` / `DATABASE_SUPERUSER`; `BackupError("backup_role_missing")` / `BackupError("restore_failed")` when unset). Built with `psycopg.conninfo.make_conninfo`.
  - `libpq_env(conninfo: str) -> dict[str, str]`: `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` for a child process (passwords never in argv).
  - `require_backup_role(conninfo: str) -> None`: connects; `SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user`; raises `BackupError("backup_role_missing")` on a failed login or when `rolbypassrls` is false.
  - `server_major(conninfo: str) -> int` (`current_setting('server_version_num')::int // 10000`); `pg_dump_major(binary: str = "pg_dump") -> int` (parses `pg_dump (PostgreSQL) 18.1 …`); `require_client_for(conninfo: str) -> None` raises `pg_client_too_old` when client major < server major.
- Produces `apps.backup.locks`: `BACKUP_LOCK_KEY: int = int.from_bytes(b"brvbckup", "big")`; `@contextmanager backup_lock() -> Iterator[None]`: `pg_try_advisory_lock` on Django's default connection (session level, outside any transaction; `RuntimeError` if called inside `atomic`), `BackupError("busy")` when taken, `pg_advisory_unlock` in `finally`.

- [ ] **Step 1: Failing tests**

```python
def test_backup_run_defaults():                              # status queued, requested_by "cli", size None, reason ""
def test_backup_run_is_not_a_workspace_table():              # not issubclass(BackupRun, WorkspaceModel); readable without workspace_context
def test_every_reason_code_has_a_dutch_sentence():           # all codes listed above in REASONS; BackupError("x").message for each is non-empty
@pytest.mark.django_db(transaction=True)
def test_lock_is_exclusive_across_connections():             # inside backup_lock(), a second psycopg connection (owner) gets pg_try_advisory_lock(BACKUP_LOCK_KEY) == False
@pytest.mark.django_db(transaction=True)
def test_lock_taken_elsewhere_raises_busy():                 # second connection holds the key -> BackupError code "busy"
def test_lock_refuses_an_open_transaction():                 # inside transaction.atomic() -> RuntimeError
@pytest.mark.django_db(transaction=True)
def test_require_backup_role_accepts_the_backup_role(backup_conninfo)
@pytest.mark.django_db(transaction=True)
def test_require_backup_role_refuses_the_owner():            # owner conninfo -> BackupError "backup_role_missing"
def test_require_backup_role_refuses_a_failed_login(backup_conninfo)   # wrong password in conninfo -> "backup_role_missing"; message has no password
def test_libpq_env_carries_the_password_not_argv(backup_conninfo)      # PGPASSWORD set; values match the conninfo
def test_pg_dump_major_parses_the_version(tmp_path)          # fake `pg_dump` script printing "pg_dump (PostgreSQL) 18.1 (Debian 18.1-1.pgdg13+1)" -> 18
def test_client_older_than_server_is_refused(monkeypatch)    # server_major -> 18, pg_dump_major -> 17 -> "pg_client_too_old"
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement; `uv run python manage.py makemigrations backup` (owner role, `make migrate` locally).
- [ ] **Step 4:** `make test lint typecheck` → PASS (incl. `test_rls_coverage.py`, `makemigrations --check`).
- [ ] **Step 5:** Commit `feat(backup): run record, advisory lock and access as the back-up role`.

---

### Task 3: The restic wrapper

**Files:**
- Create: `backend/apps/backup/restic.py`, `backend/tests/backup_support.py`
- Modify: `backend/brievie/settings.py` (`BRIEVIE_RESTIC_BINARY: str = env.str(..., default="restic")`)
- Test: `backend/tests/test_backup_restic.py`, `backend/tests/test_backup_restic_integration.py`

**Interfaces:**
- Produces in `apps.backup.restic` (all argv builders are pure and return `list[str]` starting **after** the binary):
  - Constants: `HOST = "brievie"`, `DB_FILENAME = "brievie.pgdump"`, `TMP_EXCLUDE = ".tmp-*"`, `RETENTION = ("--keep-within-daily", "7d", "--keep-within-weekly", "1m", "--keep-within-monthly", "1y")`, `CHECK_SUBSET = "5%"`, `SUPPORTED_SCHEMES = ("rest:", "s3:")`.
  - `@dataclass(frozen=True) class Repository: kind: Literal["folder", "rest", "s3"]; location: str; credentials: Mapping[str, str]` (`location` in restic notation; `credentials` already under restic's own names).
  - `check_repository_value(value: str) -> None`: `BackupError("repository_unsupported")` unless it starts with a supported scheme; `BackupError("repository_has_credentials")` when the part after the scheme matches `^[a-z0-9+.-]+://[^/@]*@` (any userinfo).
  - `tags(run_id: UUID, kind: Literal["db", "files"]) -> list[str]` → `["--tag", f"run:{run_id}", "--tag", f"kind:{kind}"]`.
  - `init_argv()`, `unlock_argv()` (`["unlock"]`: removes stale locks only), `backup_stdin_argv(run_id, command: Sequence[str])` → `["backup", "--json", "--host", HOST, *tags(run_id, "db"), "--stdin-from-command", "--stdin-filename", DB_FILENAME, "--", *command]`, `backup_files_argv(run_id, paths: Sequence[str])` → `["backup", "--json", "--host", HOST, *tags(run_id, "files"), "--exclude", TMP_EXCLUDE, *paths]`, `forget_argv()` → `["forget", "--prune", "--host", HOST, "--group-by", "host,paths", *RETENTION]` (never group by tags: every run has its own `run:` tag and nothing would be pruned), `check_argv()` → `["check", "--read-data-subset", CHECK_SUBSET]`, `snapshots_argv(tag: str)` → `["snapshots", "--json", "--tag", tag]`, `dump_argv(snapshot_id, path)` → `["dump", snapshot_id, path]`, `restore_argv(snapshot_id, target, includes)` → `["restore", snapshot_id, "--target", target, *("--include", p for p in includes)]`.
  - `restic_env(repository: Repository, password: str, *, extra: Mapping[str, str] = {}) -> dict[str, str]`: `PATH`, `HOME`, `RESTIC_REPOSITORY`, `RESTIC_PASSWORD`, the credentials, `extra` (the libpq variables for the dump). Nothing else from `os.environ`.
  - `@dataclass(frozen=True) class ResticResult: returncode: int; stdout: bytes; stderr: str` (`stderr` already redacted).
  - `run(argv: Sequence[str], env: Mapping[str, str], *, secrets: Sequence[str], stdin: int | None = None, stdout: int | IO[bytes] = subprocess.PIPE, timeout: float | None = None) -> ResticResult`: `subprocess.run([settings.BRIEVIE_RESTIC_BINARY, *argv], env=env, …, check=False)`; `stderr` passes `redact`.
  - `redact(text: str, secrets: Iterable[str]) -> str`: every non-empty secret → `***`; any `scheme://user:pass@` / `scheme://user@` userinfo → `scheme://***@`; result cut to 2000 characters.
  - `classify(result: ResticResult) -> str`: exit 10 → `repository_missing`, 11 → `repository_locked`, 12 → `wrong_password`, 3 → `files_incomplete`; exit 1 with "no space left on device" → `destination_full`, with "connection refused" / "no such host" / "i/o timeout" → `destination_unreachable`; otherwise `restic_failed`. `raise_for(result: ResticResult) -> None` raises `BackupError(classify(result))` for a non-zero exit (restic ≥ 0.17 exit codes).
  - `summary_bytes(stdout: bytes) -> int`: `total_bytes_processed` of the last JSON line with `"message_type": "summary"`.
  - `@dataclass(frozen=True) class Snapshot: id: str; time: datetime; tags: tuple[str, ...]; paths: tuple[str, ...]`; `parse_snapshots(stdout: bytes) -> list[Snapshot]` (newest first).
- Produces `tests/backup_support.py`: fixture `fake_restic(tmp_path, settings) -> FakeRestic` that installs a script as `BRIEVIE_RESTIC_BINARY`; the script appends `{"argv": [...], "env_keys": [...]}` to a JSONL file and answers from a table `FakeRestic.respond(prefix: tuple[str, ...], *, stdout: bytes = b"", stderr: str = "", code: int = 0)`; `FakeRestic.calls -> list[list[str]]`. Same mechanism for `fake_tool(name)` (pg_dump, pg_restore) used in Tasks 6, 9, 10. Also `configured_folder` (Task 6) and `owner_conninfo` (Task 10) live here.

- [ ] **Step 1: Failing tests**

```python
def test_dump_is_streamed_with_run_and_kind_tags():          # backup_stdin_argv(rid, ["pg_dump","--format=custom","--no-password"]) == [..exact list..]
def test_dump_command_never_enables_row_security():          # services' dump command (constant DUMP_COMMAND in Task 6) has no "--enable-row-security"
def test_files_backup_excludes_temporary_files():            # "--exclude", ".tmp-*" present; tags kind:files
def test_forget_prunes_with_keep_within_and_never_groups_by_tags()   # exact list; "tags" not in the --group-by value
def test_check_reads_a_sample():                             # ["check","--read-data-subset","5%"]
def test_repository_with_credentials_is_refused():           # "rest:https://u:p@h/r", "rest:https://u@h/r", "s3:https://k:s@h/b" -> repository_has_credentials
def test_repository_without_credentials_is_accepted():       # "rest:https://h:8000/r", "s3:https://s3.example.com/bucket/brievie"
def test_unsupported_repository_is_refused():                # "sftp:u@h:/p", "/mnt/usb", "b2:bucket" -> repository_unsupported
def test_redact_removes_secrets_and_userinfo():
    text = "Fatal: unable to open rest:https://anne:geheim@nas:8000/brievie/ with pw S3CR3T"
    out = redact(text, ["S3CR3T", "geheim"])
    assert "geheim" not in out and "S3CR3T" not in out and "anne" not in out
def test_env_contains_only_what_restic_needs():              # keys == {PATH, HOME, RESTIC_REPOSITORY, RESTIC_PASSWORD, *credentials, *extra}
def test_exit_codes_are_classified(fake_restic):             # 10/11/12/3, "no space left on device", "connection refused", other -> exact codes
def test_run_redacts_stderr(fake_restic):                    # fake prints the password on stderr -> result.stderr has "***"
def test_summary_bytes_reads_the_summary_line():             # two JSON lines (status, summary) -> total_bytes_processed
def test_parse_snapshots_newest_first()

# test_backup_restic_integration.py — skipped unless `restic` (>= 0.17) is on PATH
def test_restic_round_trip(tmp_path):                        # init local repo; backup_stdin of ["sh","-c","printf dump-bytes"]; backup_files of a dir
                                                              # holding a.page and .tmp-x; snapshots by run tag -> 2; dump of DB_FILENAME == b"dump-bytes";
                                                              # .tmp-x absent from `restic ls`; forget_argv runs with exit 0
```

- [ ] **Step 2:** `make test` → FAIL.
- [ ] **Step 3:** Implement `restic.py` and `backup_support.py`.
- [ ] **Step 4:** `make test lint typecheck` → PASS (the integration test is skipped on a machine without restic; CI's `restore` job is the end-to-end proof).
- [ ] **Step 5:** Commit `feat(backup): a restic wrapper with argv lists, redaction and exit codes`.

---

### Task 4: Destinations and `backup --init`

**Files:**
- Create: `backend/apps/backup/destination.py`, `backend/apps/backup/management/{__init__,commands/__init__,commands/backup_init}.py`
- Modify: `backend/apps/backup/services.py` (Create), `backend/brievie/settings.py`
- Test: `backend/tests/test_backup_destination.py`

**Interfaces:**
- Produces settings (all `env.*` with defaults; read lazily by `destination`): `BRIEVIE_BACKUP_PASSWORD: str = ""`, `BRIEVIE_BACKUP_REPOSITORY: str = ""`, `BRIEVIE_BACKUP_DIR_SET: bool = False` (compose sets it from `${BRIEVIE_BACKUP_DIR:+true}`), `BRIEVIE_BACKUP_MOUNT: str = "/backup"`, `BRIEVIE_BACKUP_APPEND_ONLY: bool = False`, `BRIEVIE_BACKUP_S3_ACCESS_KEY_ID`, `BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY`, `BRIEVIE_BACKUP_REST_USERNAME`, `BRIEVIE_BACKUP_REST_PASSWORD` (all `str = ""`), `BRIEVIE_BACKUP_CONFIG_DIR: str = "/data/backup-config"`, `BRIEVIE_AUTHORITY_COPY_DIR: str = "/data/authority"`, `BRIEVIE_HOSTNAME: str = ""`.
- Produces `apps.backup.destination`:
  - `MARKER_NAME = "brievie-backup.json"`, `REPOSITORY_DIR = "restic"`; marker content `{"format": 1, "created_at": "<ISO>", "repository": "restic"}`.
  - `destination_kind() -> str`: `"folder"` when `BRIEVIE_BACKUP_DIR_SET`, else `"rest"`/`"s3"` by the repository's scheme, else `""`. Both set → `BackupError("not_configured")` with the sentence that only one may be set (builder's wording).
  - `configured_repository() -> restic.Repository`: `not_configured` when nothing is set; for a folder `destination_not_ready` when `<mount>/brievie-backup.json` is missing (**never** creates anything); for rest/s3 `check_repository_value` and the credential mapping (`BRIEVIE_BACKUP_S3_*` → `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, `BRIEVIE_BACKUP_REST_*` → `RESTIC_REST_USERNAME`/`RESTIC_REST_PASSWORD`).
  - `backup_password() -> str`: `BackupError("no_backup_password")` when empty or still containing `change-me`.
  - `secrets() -> list[str]`: the back-up password, the credential values, the backup DB password (for `redact`).
  - `initialise(*, allow_same_disk: bool, owner_uid: int | None) -> restic.Repository`: folder: `already_initialised` when the marker exists; `same_disk` when `os.stat(mount).st_dev == os.stat(settings.BRIEVIE_STORAGE_ROOT).st_dev` and not `allow_same_disk`; then `restic init` into `<mount>/restic`, write the marker (0644), and when `owner_uid` is given `os.chown` the marker and the `restic` tree to `owner_uid:owner_uid` (the keeper's uid; the one-off container runs as root). rest/s3: `restic init`; `restic` exit 1 "already exists"/"config file already exists" → `already_initialised`.
- Produces `apps.backup.services.initialise_destination(*, allow_same_disk: bool) -> str` (under `backup_lock()`, returns `destination_kind()`; owner uid `10001` when `os.geteuid() == 0`).
- Produces command `backup_init [--allow-same-disk]` (runs in the one-off `admin` container as root; prints one Dutch sentence; exit 1 with `BackupError.message` on failure).

- [ ] **Step 1: Failing tests**

```python
def test_nothing_configured_is_not_configured(settings)     # BackupError code not_configured; message == "Er is nog geen back-up ingesteld."
def test_folder_without_marker_is_refused(settings, tmp_path, fake_restic):
    # BRIEVIE_BACKUP_DIR_SET=True, mount = empty tmp dir -> destination_not_ready; fake_restic.calls == []; mount still empty
def test_folder_with_marker_points_at_the_restic_dir(settings, tmp_path)  # location == f"{mount}/restic"
def test_init_writes_marker_and_initialises(settings, tmp_path, fake_restic)  # calls == [["init"]] with RESTIC_REPOSITORY=<mount>/restic; marker JSON format 1
def test_init_twice_is_refused(...)                          # already_initialised
def test_init_on_the_disk_of_the_documents_is_refused(settings, tmp_path, fake_restic):
    # mount and BRIEVIE_STORAGE_ROOT both under tmp_path (same st_dev) -> same_disk; no marker; with allow_same_disk=True -> succeeds
def test_rest_credentials_go_into_their_own_variables(settings)  # env has RESTIC_REST_USERNAME/PASSWORD, RESTIC_REPOSITORY has no "@"
def test_folder_and_repository_together_are_refused(settings)
def test_placeholder_backup_password_is_refused(settings)   # "change-me-backup-password" -> no_backup_password
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** `make test lint typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(backup): folder, REST and S3 destinations with an explicit first set-up`.

---

### Task 5: The manifest and the copy of `.env`

**Files:**
- Create: `backend/apps/backup/manifest.py`, `backend/apps/backup/envcopy.py`, `backend/apps/backup/management/commands/store_env_copy.py`
- Test: `backend/tests/test_backup_manifest.py`, `backend/tests/test_backup_envcopy.py`

**Interfaces:**
- Produces `apps.backup.manifest`:
  - `MANIFEST_FORMAT = 1`; file name `MANIFEST_NAME = "manifest.json"` inside `BRIEVIE_BACKUP_CONFIG_DIR`, so it lives in the files snapshot at `/data/backup-config/manifest.json`.
  - `@dataclass(frozen=True) class WorkspaceCounts: documents: int; pages: int; page_files: int`.
  - `@dataclass(frozen=True) class Manifest: format: int; run_id: UUID; created_at: datetime; brievie_version: str; migrations: dict[str, list[str]]; postgres_version: str; hostname: str; authority_sha256: str | None; workspaces: dict[UUID, WorkspaceCounts]`.
  - JSON shape (keys exactly): `{"format": 1, "run_id": "…", "created_at": "2026-11-17T21:03:00+01:00", "brievie_version": "0.1.0", "migrations": {"documents": ["0001_initial", …]}, "postgres_version": "18.1", "hostname": "brievie.local", "authority_sha256": "ab…" | null, "workspaces": {"<uuid>": {"documents": 3, "pages": 5, "page_files": 5}}}`.
  - `brievie_version() -> str`: `[project].version` of `settings.BASE_DIR / "pyproject.toml"` via `tomllib` (the file is in the image; U1 makes the version source official).
  - `build_manifest(*, run_id: UUID, conninfo: str, storage_root: Path, authority_dir: Path) -> Manifest`: counts with **one psycopg connection as the back-up role** (`SET row_security = off`, so a missing bypass fails loudly): documents and pages per workspace from `documents_document` / `documents_documentpage` grouped by `workspace_id`, including workspaces with zero; applied migrations from `django_migrations`; `server_version`. `page_files` counts `<storage_root>/<workspace>/*/*.page`. `authority_sha256` = `apps.devices.pki.certificate_sha256` of `authority_dir/"root.crt"`, `None` when absent. `hostname` = `settings.BRIEVIE_HOSTNAME`.
  - `manifest_to_json(m: Manifest) -> str`; `manifest_from_json(text: str) -> Manifest` (`BackupError("manifest_unreadable")` for bad JSON or a `format` other than 1).
  - `unknown_migrations(m: Manifest) -> list[str]`: `"app.name"` entries of the manifest that are not in `MigrationLoader(None, ignore_no_migrations=True).disk_migrations` — a back-up from a newer schema. (The version is `0.1.0` until U1, so this is the real "newer Brievie" guard.)
- Produces `apps.backup.envcopy`:
  - `EXCLUDED_KEYS = ("BRIEVIE_MASTER_KEY", "POSTGRES_PASSWORD", "BRIEVIE_OWNER_PASSWORD", "DATABASE_OWNER_URL")`; `ENV_COPY_NAME = "env"`.
  - `store_env_copy(text: str) -> None`: `BackupError("env_copy_has_secret")` when any line starts with an excluded key + `=`; otherwise writes `<config dir>/env` atomically (temp file + `os.replace`), mode 0600. Not a database write, so no `atomic`.
- Produces command `store_env_copy` (reads stdin; prints nothing on success). `./brievie` pipes `deploy/.env` through `grep -v` of the excluded keys into it before every back-up.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.django_db(transaction=True)
def test_manifest_counts_every_workspace_as_the_backup_role(ws_a, ws_b, backup_conninfo, tmp_path):
    # one uploaded 2-page letter in ws_a, a 1-page letter in ws_b (tests.documents_support.upload as their admins)
    # -> workspaces[ws_a.id] == WorkspaceCounts(1, 2, 2); workspaces[ws_b.id] == WorkspaceCounts(1, 1, 1)
@pytest.mark.django_db(transaction=True)
def test_manifest_as_the_owner_fails_loudly(ws_a, tmp_path)  # owner conninfo -> psycopg.errors.InsufficientPrivilege, not zero counts
def test_manifest_json_round_trip(tmp_path)                 # manifest_from_json(manifest_to_json(m)) == m; created_at keeps its offset
def test_manifest_of_another_format_is_refused()            # {"format": 2, …} -> manifest_unreadable
def test_unknown_migrations_finds_a_newer_schema()          # migrations {"documents": ["0001_initial", "9999_future"]} -> ["documents.9999_future"]
def test_authority_fingerprint_matches_pki(tmp_path)        # root.crt from tests/test_devices_pki.py's PEM -> same sha256 as certificate_sha256
def test_brievie_version_reads_pyproject()                  # == "0.1.0"
def test_env_copy_refuses_the_master_key(settings, tmp_path)   # text with "BRIEVIE_MASTER_KEY=ab…" -> env_copy_has_secret; no file written
def test_env_copy_is_private(settings, tmp_path)            # stat mode & 0o777 == 0o600; content equal
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(backup): a versioned manifest counted as the back-up role, and the .env copy`.

---

### Task 6: Making a back-up: run, job, wait, status

**Files:**
- Create: `backend/apps/backup/jobs.py`, `backend/apps/backup/management/commands/{backup,backup_status}.py`
- Modify: `backend/apps/backup/services.py`
- Test: `backend/tests/test_backup_services.py`, `backend/tests/test_backup_jobs.py`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces in `apps.backup.services`:
  - `DUMP_COMMAND = ("pg_dump", "--format=custom", "--no-password")` (database, user, password from `libpq_env(backup_conninfo())`).
  - `FILE_PATHS` = `(settings.BRIEVIE_STORAGE_ROOT, settings.BRIEVIE_AUTHORITY_COPY_DIR, settings.BRIEVIE_BACKUP_CONFIG_DIR)`, computed in a function `file_paths() -> list[str]`.
  - `request_run(kind: str, *, requested_by: str = "cli") -> BackupRun`, `transaction.atomic`: returns the existing open run of that kind if there is one; otherwise creates one (`queued`, `destination_kind=destination_kind()`) and defers `jobs.run_backup` with `run_id`.
  - `perform_run(run_id: UUID) -> None` (called by the job; not wrapped as a whole: external calls). Order, pinned (D 4.3):
    1. load the run; return at once unless its status is in `OPEN_STATUSES` (a run closed by a restore, or finished, makes its job a no-op);
    2. mark `running`, `started_at` (own `atomic`);
    3. `with backup_lock():` `repository = configured_repository()`, `password = backup_password()`, `restic unlock`;
    4. kind `check`: `restic check` → done. Kind `backup`:
    5. `require_backup_role(backup_conninfo())`, `require_client_for(…)`;
    6. **dump**: `backup_stdin_argv(run.id, DUMP_COMMAND)` with `extra=libpq_env(…)`;
    7. `build_manifest(...)` → write `manifest.json` (0600) into the config dir (the `env` copy is already there; when it is missing the run continues and the manifest notes nothing — the restore regenerates what is absent);
    8. **files**: `backup_files_argv(run.id, file_paths())`;
    9. unless `BRIEVIE_BACKUP_APPEND_ONLY`: `forget_argv()`;
    10. mark `succeeded`, `finished_at`, `size_bytes` = sum of the two `summary_bytes`.
    On `BackupError(code)`: mark `failed` with `reason_code=code`, `reason=message`; log `"Backup run %s failed: %s"` with id and code, plus the redacted stderr tail at WARNING. Any other exception (`# noqa: BLE001`): `internal_error`, log the type only. Never re-raised (no automatic retry of a failed back-up).
  - `wait_for_run(run_id: UUID, *, timeout: float, pickup_timeout: float = 120.0, poll: float = 2.0, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> BackupRun`: returns when the status leaves `OPEN_STATUSES`; raises `TimeoutError` with `run.status` still `queued` after `pickup_timeout` (nobody picked it up) or still open after `timeout`.
  - `@dataclass(frozen=True) class BackupStatus: brievie_version: str; postgres_version: str; pg_dump_version: str; restic_version: str; destination_kind: str; last_success: BackupRun | None; last_check: BackupRun | None; last_failure: BackupRun | None; open_run: BackupRun | None`; `status_summary() -> BackupStatus` (versions by running the binaries; a missing binary shows `"?"`).
- Produces `apps.backup.jobs`: `QUEUE = "backup"`; `@app.task(name="run_backup", queue=QUEUE, lock="backup") def run_backup(*, run_id: str) -> None` → `perform_run(UUID(run_id))`; `@app.periodic(cron="*/5 * * * *") @app.task(name="retry_stalled_backup_jobs", queue=QUEUE, pass_context=True) async def retry_stalled_backup_jobs(context, timestamp)` — the same body as `apps.pipeline.jobs.retry_stalled_jobs` with `queue=QUEUE` (a retried job resumes its `running` run).
- Produces command `backup [--check] [--timeout SECONDS=21600]` (run with `compose exec keeper`): `request_run` → print "Back-up gestart…" → `wait_for_run` → print the result sentence (size human-readable) and exit 0, or `reason` and exit 1, or the not-picked-up / timeout sentence and exit 2.
- Produces command `backup_status`: prints the `BackupStatus` as Dutch lines (wording the builder's; no repository string, no path).

- [ ] **Step 1: Failing tests**

```python
def test_request_run_reuses_an_open_run()                   # second request_run("backup") returns the same id; one ProcrastinateJob row
def test_request_run_defers_on_the_backup_queue()            # ProcrastinateJob queue_name == "backup", args == {"run_id": str(id)}
@pytest.mark.django_db(transaction=True)
def test_run_dumps_first_then_files_then_forgets(ws_a, configured_folder, fake_restic, fake_tool):
    # fake pg_dump present (version 18), fake restic answers 0 with a summary line
    # -> calls in order: unlock, backup --stdin-from-command…, backup … /data/documents…, forget --prune…
    # -> run.status == "succeeded", size_bytes == sum of the two summaries, manifest.json written
@pytest.mark.django_db(transaction=True)
def test_append_only_never_forgets(settings, …)             # BRIEVIE_BACKUP_APPEND_ONLY=True -> no "forget" in calls
@pytest.mark.django_db(transaction=True)
def test_failed_dump_records_no_files_snapshot(…)           # dump call exits 1 -> no files backup call; status failed, reason_code restic_failed
@pytest.mark.django_db(transaction=True)
def test_failed_run_reason_and_log_hold_no_secret(settings, caplog, fake_restic, …):
    # BRIEVIE_BACKUP_REPOSITORY="rest:https://nas:8000/b", REST_PASSWORD="geheim", backup password "S3CR3T";
    # fake restic prints all three on stderr and exits 1 -> neither "geheim" nor "S3CR3T" nor "nas:8000" in run.reason or caplog.text
@pytest.mark.django_db(transaction=True)
def test_run_fails_when_the_dump_role_cannot_bypass_rls(settings, …)  # DATABASE_BACKUP = owner -> backup_role_missing; no restic backup call
def test_unconfigured_destination_fails_the_run_with_the_sentence()   # reason == "Er is nog geen back-up ingesteld."
def test_closed_run_makes_the_job_a_no_op(fake_restic)      # status interrupted -> perform_run does nothing; calls == []
@pytest.mark.django_db(transaction=True)
def test_lock_taken_fails_the_run_as_busy(…)
def test_wait_returns_the_finished_run()                     # fake clock; status flips to succeeded on 2nd poll
def test_wait_gives_up_when_nobody_picks_up_the_run()        # stays queued past pickup_timeout -> TimeoutError; no sleep beyond it
def test_backup_command_exit_codes(…)                        # call_command with patched perform_run/wait: 0 / 1 (prints reason) / 2
def test_stalled_backup_jobs_are_retried():                  # retry_stalled_backup_jobs calls get_stalled_jobs(queue="backup")
def test_status_lists_versions_and_last_runs(…)              # no repository string, no path in the output
```

(`configured_folder` is a fixture in `backup_support.py`: a tmp mount with a marker, `BRIEVIE_BACKUP_DIR_SET`, a non-placeholder password, tmp config and authority dirs.)

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** `make test lint typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(backup): make and check a back-up through the backup queue`.

---

### Task 7: The image and the compose services

**Files:**
- Modify: `deploy/Dockerfile`, `deploy/docker-compose.yml`, `deploy/caddy-publish-root.sh`, `.github/workflows/ci.yml` (job `stack`: keeper running), `docs/DECISIONS.md`
- Test: `backend/tests/test_deploy_files.py`

**Interfaces:**
- Produces in the image: `/usr/local/bin/restic` (pinned), `pg_dump`/`pg_restore` 18 from PGDG, directories `/data/backup-config` and `/data/authority` owned by `brievie` (10001), next to `/data/documents`.
- Produces compose services and volumes (names exact): service `keeper`, service `admin` (profile `admin`), volumes `caddy-authority`, `backup-config`, `backup-unset`.

- [ ] **Step 1: Verify restic's arm64 availability first** (D 4.1, D 12). Run `docker buildx imagetools inspect restic/restic:0.18.1` and confirm `linux/amd64` and `linux/arm64` (and that the tag exists; take the newest 0.18.x if 0.18.1 is gone, ≥ 0.17.0 is required for `--stdin-from-command`, exit code 12 and `RESTIC_REST_*`). Record the result in the task report.
  - Present: in the runtime stage, `COPY --from=restic/restic:0.18.1 /usr/bin/restic /usr/local/bin/restic`.
  - Absent: a build stage `FROM debian:trixie-slim AS restic` with `ARG TARGETARCH`, `ADD https://github.com/restic/restic/releases/download/v0.18.1/restic_0.18.1_linux_${TARGETARCH}.bz2`, a `sha256sum -c` against pinned per-arch values (from the release's `SHA256SUMS`, recorded in the Dockerfile), `bunzip2`, then `COPY --from=restic`.
- [ ] **Step 2: Failing tests** (text checks, as the existing ones)

```python
def test_restic_is_pinned():                                 # re.search(r"restic[:_/]v?\d+\.\d+\.\d+", DOCKERFILE); "latest" not in it
def test_pg_client_major_matches_the_server():              # majors in compose `postgres:(\d+)`, ci.yml `postgres:(\d+)` (both jobs), Dockerfile `postgresql-client-(\d+)`: all equal
def test_keeper_has_no_master_key_and_no_superuser_password():  # service("keeper") lacks BRIEVIE_MASTER_KEY, POSTGRES_PASSWORD, BRIEVIE_OWNER_PASSWORD, DATABASE_OWNER_URL
def test_keeper_serves_the_backup_queue_only():             # "--queues backup" and not "pipeline" in service("keeper")
def test_keeper_mounts_documents_and_authority_read_only(): # "documents:/data/documents:ro", "caddy-authority:/data/authority:ro"
def test_backup_dir_falls_back_to_an_unset_volume():        # "${BRIEVIE_BACKUP_DIR:-backup-unset}:/backup" and "BRIEVIE_BACKUP_DIR_SET: ${BRIEVIE_BACKUP_DIR:+true}"
def test_admin_is_one_off_behind_a_profile():               # service("admin") has `profiles: ["admin"]`, `restart: "no"`, user "0"
def test_migrate_still_runs_before_every_app_container():   # api, worker, keeper: migrate condition service_completed_successfully; admin has no depends_on
def test_publish_root_copies_the_authority_for_the_keeper(): # caddy-publish-root.sh: the four names root.crt root.key intermediate.crt intermediate.key,
                                                              # "chown 10001:10001", "chmod 0600", "chmod 0700 /authority"; caddy mounts caddy-authority:/authority (rw)
def test_api_never_mounts_the_authority_copy():             # "caddy-authority" not in service("api") or service("worker")
```

- [ ] **Step 3: Implement.**
  - Dockerfile runtime stage, before `useradd`: PGDG key via `ADD --checksum=sha256:<pinned> https://www.postgresql.org/media/keys/ACCC4CF8.asc /usr/share/keyrings/pgdg.asc` (compute and pin the checksum), `deb [signed-by=/usr/share/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt <VERSION_CODENAME>-pgdg main` (codename from `/etc/os-release`), `apt-get install -y --no-install-recommends postgresql-client-18`, clean `/var/lib/apt/lists`. `mkdir -p /data/documents /data/backup-config /data/authority && chown brievie:brievie` all three. `/backup` created and owned by `brievie` (only the unset fallback volume ever lands there).
  - compose `keeper`: `image: brievie/backend:local`, `pull_policy: never`, `restart: unless-stopped`, `depends_on: migrate: service_completed_successfully`, `stop_grace_period: 1m`, `command: ["python", "manage.py", "procrastinate", "worker", "--queues", "backup"]` (no `check_master_key`: it has none). Environment: `DATABASE_URL` (app role, for Procrastinate and `BackupRun`), `DATABASE_BACKUP_URL: postgres://brievie_backup:${BRIEVIE_BACKUP_DB_PASSWORD:?…}@postgres:5432/brievie`, `SECRET_KEY`, `BRIEVIE_HOSTNAME`, `BRIEVIE_BACKUP_PASSWORD: ${BRIEVIE_BACKUP_PASSWORD:-}`, `BRIEVIE_BACKUP_REPOSITORY: ${BRIEVIE_BACKUP_REPOSITORY:-}`, `BRIEVIE_BACKUP_DIR_SET: ${BRIEVIE_BACKUP_DIR:+true}`, `BRIEVIE_BACKUP_APPEND_ONLY: ${BRIEVIE_BACKUP_APPEND_ONLY:-false}`, the four credential variables `:-`. Volumes: `documents:/data/documents:ro`, `caddy-authority:/data/authority:ro`, `backup-config:/data/backup-config`, `${BRIEVIE_BACKUP_DIR:-backup-unset}:/backup`. No port.
  - compose `admin`: same image, `pull_policy: never`, `profiles: ["admin"]`, `restart: "no"`, `user: "0"`, environment = keeper's plus `BRIEVIE_MASTER_KEY: ${BRIEVIE_MASTER_KEY:-}`, `DATABASE_SUPERUSER_URL: postgres://postgres:${POSTGRES_PASSWORD}@postgres:5432/brievie`, `BRIEVIE_BACKUP_DIR_HOST: ${BRIEVIE_BACKUP_DIR:-}` (for the recovery sheet). Volumes: `documents:/data/documents`, `caddy-data:/caddy-data`, `caddy-authority:/data/authority`, `backup-config:/data/backup-config`, `${BRIEVIE_BACKUP_DIR:-backup-unset}:/backup`. No `depends_on` (always run with `--no-deps`).
  - compose `caddy`: `- caddy-authority:/authority`. Top-level volumes: `caddy-authority:`, `backup-config:`, `backup-unset:`.
  - `caddy-publish-root.sh`: at start `mkdir -p /authority && chown 10001:10001 /authority && chmod 0700 /authority`; in the loop, for each of `root.crt root.key intermediate.crt intermediate.key` under `/data/caddy/pki/authorities/local`, when present and different (`cmp -s`): copy to `/authority/<f>.tmp`, `chown 10001:10001`, `chmod 0600`, `mv` into place. Comment: the copy exists only for the back-up; only the keeper and admin mount it.
  - `ci.yml` job `stack`: step "The keeper is running" (`grep -c '^keeper$'` = 1).
  - `DECISIONS.md` lines: **restic** (version, BSD-2, binary copied from the official image or verified release asset; dropped age/borg/tarballs per D 4.1); **postgresql-client-18** from PGDG (pinned to the server major; a test compares them; client and server move together, D 7.6); the keeper/admin split and what each holds.
- [ ] **Step 4:** `make test lint`; then build and start locally: `docker compose --project-directory deploy -f deploy/docker-compose.yml up -d --build --wait` → keeper running; `docker compose … exec keeper restic version` and `… exec keeper pg_dump --version` print the pinned restic and 18.x; `… exec keeper ls -ln /data/authority` shows uid 10001 and mode `-rw-------` once Caddy has served one request. Record in the task report.
- [ ] **Step 5:** Commit `feat(deploy): keeper and admin services, restic and the PostgreSQL 18 client`.

---

### Task 8: The storage sweep

**Files:**
- Create: `backend/apps/backup/sweep.py`, `backend/apps/backup/management/commands/sweep_storage.py`
- Modify: `docs/DECISIONS.md` (close the "orphan files" open item, lines 369–371 and 456)
- Test: `backend/tests/test_backup_sweep.py`

**Interfaces:**
- Produces `apps.backup.sweep`: `ORPHAN_MIN_AGE = timedelta(hours=24)`; `@dataclass(frozen=True) class Orphan: path: str; size: int; modified_at: datetime`; `find_orphans(*, conninfo: str, storage_root: Path, now: datetime) -> list[Orphan]` — known paths are `SELECT storage_path FROM documents_documentpage` read **as the back-up role** with `row_security = off` (D 4.3: the app role would not see every page); candidates are `*.page` and `.tmp-*` files under the root, older than `ORPHAN_MIN_AGE`, not known; `delete_orphans(orphans: Sequence[Orphan], storage_root: Path) -> int` via `FileSystemStorage.delete`; `@dataclass(frozen=True) class SweepResult: orphans: list[Orphan]; deleted: int`; `sweep(*, delete: bool) -> SweepResult` under `backup_lock()`.
- Produces command `sweep_storage [--delete]`: lists count and total size (paths are ids, no names); deletes only with `--delete`. Runs in `admin` as uid 10001 (`./brievie sweep`).

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.django_db(transaction=True)
def test_sweep_keeps_the_pages_of_every_workspace(ws_a, ws_b, backup_conninfo, settings):
    # a letter uploaded in ws_a and one in ws_b; an extra file ws_a/<uuid>/1.page and a .tmp-x, both mtime 2 days ago
    # -> find_orphans paths == {the extra .page, the .tmp-x}; ws_b's page file is not in it
def test_young_orphans_are_left_alone(...)                  # same extra file with mtime 1 hour ago -> not listed
@pytest.mark.django_db(transaction=True)
def test_sweep_lists_but_deletes_only_when_asked(...)        # sweep(delete=False): file still there, deleted == 0; sweep(delete=True): gone, deleted == 1
@pytest.mark.django_db(transaction=True)
def test_sweep_as_the_app_role_is_impossible(...)           # find_orphans with the owner conninfo raises InsufficientPrivilege (never "everything is an orphan")
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(backup): a storage sweep that reads known pages as the back-up role`.

---

### Task 9: Restore, part 1: open the back-up and write `.env`

**Files:**
- Create: `backend/apps/backup/restore.py`, `backend/apps/backup/management/commands/restore_fetch.py`
- Modify: `backend/apps/backup/envcopy.py` (`restored_env`)
- Test: `backend/tests/test_backup_restore_fetch.py`

**Interfaces:**
- Produces `apps.backup.restore`:
  - `@dataclass(frozen=True) class RunSnapshots: run_id: UUID; db: Snapshot; files: Snapshot`.
  - `find_run(repository: Repository, password: str, run: str | None) -> RunSnapshots`: `run` is a run uuid (U1 adds `pre-update-<version>` tags); `None` = the newest run that has both a `kind:db` and a `kind:files` snapshot. `BackupError("run_not_found")` otherwise.
  - `check_master_key_against_dump(*, repository, password, db_snapshot: Snapshot, master_key: bytes) -> None`: `restic dump <db> brievie.pgdump | pg_restore --data-only --table=workspaces_workspacekey --file=-`, parse the `COPY public.workspaces_workspacekey (<columns>) FROM stdin;` block (column order from its header, `bytea` as `\\x<hex>`), and `crypto.open_sealed(master_key, wrapped, aad=workspace_id.bytes)` the first row; `DecryptionError` → `BackupError("wrong_master_key")`. No rows: nothing to check.
  - `fetch(*, run: str | None, master_key_hex: str, separate_password: str | None, backup_dir_host: str | None, out_dir: Path, out_owner: tuple[int, int] | None) -> RunSnapshots`: repository from the environment of the one-off container (folder at `/backup`, which must carry the marker, or `BRIEVIE_BACKUP_REPOSITORY` + credentials); password = `separate_password` or `derive_backup_password(bytes.fromhex(master_key_hex))`; `find_run` (a wrong password ends here: `wrong_password`, nothing written); `restic dump` of `/data/backup-config/manifest.json` → `manifest_from_json`; `unknown_migrations` non-empty → `BackupError("newer_backup")`; `check_master_key_against_dump`; then, and only then, write `out_dir/manifest.json` and `out_dir/env` (0600; `os.chown` to `out_owner` when given).
- Produces `apps.backup.envcopy.restored_env(text: str, *, master_key_hex: str, backup_dir_host: str | None) -> str`: drops any excluded key that is present; sets `BRIEVIE_MASTER_KEY`; generates new `POSTGRES_PASSWORD` and `BRIEVIE_OWNER_PASSWORD` (`secrets.token_hex(24)`, as `init-env.sh`) and `DATABASE_OWNER_URL` (port from `POSTGRES_PORT`, default 5432); sets `BRIEVIE_BACKUP_DIR` to `backup_dir_host` when given; keeps every other line and comment in order.
- Produces command `restore_fetch [--run RUN] --out DIR [--out-owner UID:GID] [--backup-dir-host PATH]`: master key from `BRIEVIE_MASTER_KEY`, a separate password from `BRIEVIE_BACKUP_PASSWORD` when set (both environment, never argv). Prints the run id, its date, the manifest's host name and Brievie version on stdout as `key=value` lines for the wrapper (`run_id=…`, `created_at=…`, `hostname=…`, `brievie_version=…`); exit 2 for `wrong_password`/`wrong_master_key`, 3 for `newer_backup`, 1 otherwise, each with the Dutch sentence on stderr.

- [ ] **Step 1: Failing tests** (fake restic and fake pg_restore from `backup_support.py`)

```python
def test_fetch_writes_env_and_manifest(tmp_path, fake_restic, fake_tool)  # files exist, 0600; env has the typed master key; no POSTGRES_PASSWORD from the copy
def test_fetch_with_a_wrong_password_writes_nothing(tmp_path, fake_restic)  # snapshots exits 12 -> BackupError wrong_password; out_dir empty
def test_wrong_master_key_with_a_separate_password_is_caught_from_the_dump(...):
    # COPY block with a key sealed under "5a"*32; master key "00"*32 + separate password -> wrong_master_key; out_dir empty
def test_fetch_refuses_a_backup_with_unknown_migrations(...)  # manifest migrations include "documents.9999_future" -> newer_backup; out_dir empty
def test_latest_run_needs_both_snapshots(...)                # newest run has only kind:db -> the run before it is chosen
def test_unknown_run_is_refused(...)                         # run_not_found
def test_restored_env_regenerates_the_superuser_and_owner_passwords()   # both present, 48 hex, differ from the input; DATABASE_OWNER_URL carries the new owner password and the port
def test_restored_env_keeps_hostname_and_secret_key()
def test_restored_env_points_at_the_new_backup_dir()
def test_restore_fetch_never_prints_the_key(capsys, ...)     # neither the master key nor the password in stdout/stderr
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(backup): open a back-up with the master key and write the restored .env`.

---

### Task 10: Restore, part 2: database, files, verification

**Files:**
- Create: `backend/apps/backup/verify.py`, `backend/apps/backup/management/commands/{restore_database,restore_files,verify_restore}.py`
- Modify: `backend/apps/backup/restore.py`
- Test: `backend/tests/test_backup_restore.py`, `backend/tests/test_backup_verify.py`

**Interfaces:**
- Produces in `apps.backup.restore`:
  - `PG_RESTORE_COMMAND = ("pg_restore", "--exit-on-error", "--single-transaction", "--no-password")` (database etc. from `libpq_env(superuser_conninfo(dbname=…))`; owners and grants kept: the roles exist from `01-roles.sh`).
  - `restore_database(*, run: str, dbname: str = "brievie") -> int`: `find_run`; `restic dump <db> brievie.pgdump` piped into `pg_restore` (both `Popen`; either non-zero → `BackupError("restore_failed")`; stderr redacted into the log); then `close_interrupted_runs(superuser_conninfo(dbname=dbname))`; returns the number of runs closed.
  - `close_interrupted_runs(conninfo: str) -> int`: `UPDATE backup_backuprun SET status='interrupted', reason_code='interrupted_by_restore', reason=%s, finished_at=now() WHERE status IN ('queued','running')` (reason = `REASONS["interrupted_by_restore"]`). Back-up jobs from the dump are left as they are: `perform_run` is a no-op for a closed run, so they lapse when the keeper meets them (no writes to Procrastinate's tables).
  - `restore_files(*, run: str, authority_target: Path, clear_documents: bool) -> None`: when `clear_documents`, empty `/data/documents`; `restore_argv(files, "/", ["/data/documents", "/data/authority", "/data/backup-config"])` (restic as root restores the owner 10001); then copy `/data/authority/*` into `authority_target` (`/caddy-data/caddy/pki/authorities/local`) as `0:0`, dir 0700, files 0600, and remove `/caddy-data/caddy/certificates/local` so Caddy issues a new leaf from the restored authority. Missing authority in the back-up (public certificate): skip that part.
- Produces `apps.backup.verify`: `@dataclass(frozen=True) class VerifyReport: problems: list[str]; workspaces: int; pages_opened: int`; `verify_restore(*, conninfo: str, storage_root: Path, manifest: Manifest, master_key: bytes, authority_dir: Path | None) -> VerifyReport` — pure psycopg reads (`row_security = off`), no ORM, so B2 can point it at a second database and directory: per workspace the document and page counts equal the manifest's; every `documents_documentpage.storage_path` exists under `storage_root`; for each workspace one page opened (`workspaces_workspacekey` unwrapped with the master key, `crypto.open_sealed` with `intake.page_aad`); `authority_dir/root.crt` fingerprint equals `manifest.authority_sha256` (when not `None`). Each problem is one Dutch sentence with ids, no names.
- Produces commands (run in `admin`): `restore_database [--run RUN] [--dbname brievie]`; `restore_files [--run RUN] [--replace]` (`authority_target` fixed to `/caddy-data/caddy/pki/authorities/local`); `verify_restore --dbname NAME --storage-root PATH --manifest PATH [--authority-dir PATH]` (connection = `superuser_conninfo(dbname=NAME)`, master key from the environment; exit 1 and the problems when any).

- [ ] **Step 1: Failing tests**

```python
def test_dump_is_piped_into_pg_restore_as_the_superuser(fake_restic, fake_tool, settings)  # argv of pg_restore == PG_RESTORE_COMMAND; PGUSER == "postgres" in its env
def test_a_failing_dump_or_restore_stops(fake_restic, fake_tool)   # either exits 1 -> restore_failed
@pytest.mark.django_db(transaction=True)
def test_open_runs_are_closed_by_a_restore(owner_conninfo):  # one queued, one running, one succeeded -> 2 closed, reason "Afgebroken door herstel."
def test_files_are_restored_and_the_authority_placed(tmp_path, fake_restic)  # paths parametrised: authority files land 0600 in a 0700 dir; certificates/local removed
def test_replace_empties_the_documents_first(tmp_path, fake_restic)
@pytest.mark.django_db(transaction=True)
def test_verify_passes_on_a_complete_installation(ws_a, ws_b, backup_conninfo, settings):
    # letters in both workspaces; manifest built by build_manifest -> problems == [], workspaces == 2, pages_opened == 2
@pytest.mark.django_db(transaction=True)
def test_verify_reports_a_missing_page_file(...)            # delete one file -> one problem naming its document id
@pytest.mark.django_db(transaction=True)
def test_verify_reports_a_wrong_master_key(...)             # master key "00"*32 -> problem per workspace
def test_verify_reports_another_authority(...)              # manifest sha differs -> one problem
```

(`owner_conninfo` fixture in `backup_support.py`: the test database as the owner, which owns `backup_backuprun`.)

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(backup): restore the database and the files, and verify the result`.

---

### Task 11: The recovery sheet

**Files:**
- Create: `backend/apps/backup/sheet.py`, `backend/apps/backup/templates/backup/recovery_sheet.html`, `backend/apps/backup/management/commands/recovery_sheet.py`
- Test: `backend/tests/test_backup_sheet.py`

**Interfaces:**
- Produces `apps.backup.sheet`: `group_key(hex_key: str) -> str` (16 groups of 4, lower case, separated by one space); `@dataclass(frozen=True) class SheetData: hostname: str; created_on: date; master_key_groups: str; separate_password: str | None; authority_sha256: str | None; destination: str; restore_steps: list[str]`; `sheet_data() -> SheetData` (master key from `crypto.master_key()`; `separate_password` = `settings.BRIEVIE_BACKUP_PASSWORD` when it differs from `derive_backup_password(master_key())`, else `None`; fingerprint from `pki.ca_certificate_sha256()`; destination = `BRIEVIE_BACKUP_DIR_HOST` for a folder, the repository string for rest/s3 (it holds no credentials, Task 3), or "Nog niet ingesteld"); `render_sheet(data: SheetData) -> str` via `render_to_string("backup/recovery_sheet.html", …)`.
- The template: one self-contained HTML page (inline `<style>`, print styles, A4), Dutch B1 text through `{% translate %}`, title "Herstelblad Brievie", the instruction to print it or keep it somewhere that is not this machine, the four restore steps of D 4.4 (wording the builder's), no QR (D 4.4), no `<script>`, no external URL.
- Produces command `recovery_sheet`: writes the HTML to stdout only; logs nothing. `./brievie recovery-sheet` writes it to `brievie-herstelblad.html` in the installation folder with `umask 077` (mode 600).

- [ ] **Step 1: Failing tests**

```python
def test_key_is_grouped_in_fours():                          # group_key("5a"*32) == " ".join(["5a5a"] * 16)
def test_sheet_holds_name_date_key_fingerprint_destination(settings)  # each value in the HTML; hostname "<b>x" escaped
def test_sheet_has_a_second_line_only_with_a_separate_password(settings)
def test_sheet_is_self_contained():                          # no "<script", no "src=\"http", no "href=\"http"
def test_sheet_command_logs_nothing(caplog, capsys)          # caplog.text == ""; the key appears in stdout only
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS.
- [ ] **Step 5:** Commit `feat(backup): the recovery sheet as one self-contained page`.

---

### Task 12: `./brievie`

**Files:**
- Create: `brievie` (mode 755)
- Modify: `.gitignore` (`brievie-herstelblad.html`, `deploy/.restore/`, `deploy/.env.before-restore-*`)
- Test: `backend/tests/test_brievie_wrapper.py`

**Interfaces:**
- Consumes: the commands of Tasks 4–11, `deploy/init-env.sh`, `deploy/ensure-backup-role.sh`, `deploy/postgres-init/{database,backup-role}.psql`.
- Produces: `./brievie start | backup [--init [--allow-same-disk] [--separate-password]] [--check] | restore [RUN] [--replace [--skip-safety-backup]] [--separate-password] [--keep-hostname] | status | sweep [--delete] | recovery-sheet | help`. Exit 2 with usage for anything else. Non-interactive inputs for scripts and CI: `BRIEVIE_RESTORE_SOURCE` (a folder or a `rest:`/`s3:` value), `BRIEVIE_RESTORE_MASTER_KEY`, `BRIEVIE_RESTORE_BACKUP_PASSWORD`; otherwise asked with `stty -echo` for secrets. Master key input: spaces removed, lower-cased, must be 64 hex.
- Behaviour, pinned:
  - **Every command** first brings the installation along: `sh deploy/init-env.sh` (adds missing lines), and, when the `postgres` service runs, `sh deploy/ensure-backup-role.sh`.
  - `start`: the bring-along entry for an installation from before B1: `$COMPOSE up -d --build --wait`, then `ensure-backup-role.sh`, then the recovery sheet when `brievie-herstelblad.html` does not exist yet.
  - `backup`: refuse with "De back-updienst draait niet. Start Brievie met ./brievie start." (exit 1) unless `$COMPOSE ps --status running --services` lists `keeper` — **no** `run`, never a second container. `--separate-password` (only with `--init`): replace the `BRIEVIE_BACKUP_PASSWORD` line with `openssl rand -hex 32` before init. Then `$COMPOSE up -d --no-deps keeper` (recreates it when `.env` changed, e.g. a new folder), `grep -v -E '^(BRIEVIE_MASTER_KEY|POSTGRES_PASSWORD|BRIEVIE_OWNER_PASSWORD|DATABASE_OWNER_URL)=' deploy/.env | $COMPOSE exec -T keeper python manage.py store_env_copy`; with `--init`: `$COMPOSE run --rm --no-deps admin python manage.py backup_init [--allow-same-disk]`; then `$COMPOSE exec -T keeper python manage.py backup [--check]`, passing its exit code on.
  - `status`: `$COMPOSE exec -T keeper python manage.py backup_status` (keeper not running: the same sentence as `backup`).
  - `sweep`: `$COMPOSE run --rm --no-deps --user 10001 admin python manage.py sweep_storage [--delete]`.
  - `recovery-sheet`: `(umask 077; $COMPOSE run --rm --no-deps -T admin python manage.py recovery_sheet > brievie-herstelblad.html.tmp) && mv` into `brievie-herstelblad.html`; prints the instruction to print it or store it off this machine.
  - `restore`, in the order of D 4.6 (binding):
    1. Build `brievie/backend:local` with `docker build -f deploy/Dockerfile -t brievie/backend:local .` when absent (compose cannot run without `.env`). Ask the source and the master key (and the separate password with `--separate-password`). A folder must hold `brievie-backup.json`. `mkdir -p deploy/.restore` (umask 077). `docker run --rm --user 0 [-v "<folder>:/backup:ro"] -v "$PWD/deploy/.restore:/out" -e BRIEVIE_MASTER_KEY -e BRIEVIE_BACKUP_PASSWORD -e BRIEVIE_BACKUP_DIR_SET -e BRIEVIE_BACKUP_REPOSITORY -e <credentials> -e SECRET_KEY=<the Dockerfile's collectstatic-only value> -e DATABASE_URL=postgres://build@localhost/build brievie/backend:local python manage.py restore_fetch [--run RUN] --out /out --out-owner "$(id -u):$(id -g)" [--backup-dir-host <folder>]` — secrets as `-e NAME` (value from the wrapper's environment), never `-e NAME=value`. A non-zero exit ends here: nothing else is written.
    2. Show date, host name and version; ask "Blijft de servernaam <name>?" (`--keep-hostname` answers yes); a new name replaces the `BRIEVIE_HOSTNAME=` line in `deploy/.restore/env` (temp file + `mv`, no `sed -i`) and the text says the phones will need to pair again.
    3. Data present (`deploy/.env` exists or `docker volume inspect brievie_pgdata` succeeds): without `--replace` refuse ("Deze installatie heeft al gegevens …--replace…", exit 1). With `--replace`: `$COMPOSE stop api worker caddy`; unless `--skip-safety-backup`, `./brievie backup` must succeed (else stop); `$COMPOSE stop keeper`; move the old `.env` to `deploy/.env.before-restore-<UTC timestamp>` (0600).
    4. `mv deploy/.restore/env deploy/.env` (0600) — before PostgreSQL starts for the first time.
    5. `$COMPOSE up -d --wait postgres`. With `--replace`: over the socket (`exec -T postgres psql -U postgres`, script on stdin): `DROP DATABASE brievie WITH (FORCE)`, `ALTER ROLE postgres|brievie_owner|brievie_app PASSWORD` to the new `.env`'s values, `\i /docker-entrypoint-initdb.d/database.psql`, then `ensure-backup-role.sh`. Then `$COMPOSE run --rm --no-deps admin python manage.py restore_database --run <run_id>`.
    6. `$COMPOSE run --rm --no-deps admin python manage.py restore_files --run <run_id> [--replace]` — Caddy has not started.
    7. `$COMPOSE up -d --wait` (migrate brings the schema to this version).
    8. `$COMPOSE run --rm --no-deps admin python manage.py verify_restore --dbname brievie --storage-root /data/documents --manifest /data/backup-config/manifest.json --authority-dir /caddy-data/caddy/pki/authorities/local`; remove `deploy/.restore`; print the result and remind to make a recovery sheet if the host name changed.

- [ ] **Step 1: Failing tests** — a fake `docker` on `PATH` (a script in `tmp_path/bin` that appends `argv` and, for `exec -T … store_env_copy`, its stdin to a log; `ps` answers from `FAKE_DOCKER_RUNNING`; `volume inspect` from `FAKE_DOCKER_VOLUMES`; other calls exit 0); the wrapper and `deploy/` are copied into `tmp_path`.

```python
def test_unknown_command_prints_usage_and_exits_2()
def test_every_command_brings_env_and_role_along(…)         # old .env without backup lines -> lines added; a psql exec with backup-role.psql was made
def test_backup_without_a_running_keeper_says_so_and_runs_nothing(…)  # RUNNING="postgres" -> exit 1, "./brievie start" in stderr; no "exec", no "run" in the log
def test_backup_never_gives_the_keeper_the_master_key(…)    # store_env_copy stdin has no BRIEVIE_MASTER_KEY/POSTGRES_PASSWORD/BRIEVIE_OWNER_PASSWORD;
                                                             # the key's value appears in no argv
def test_init_runs_in_the_one_off_container(…)              # "run --rm --no-deps admin python manage.py backup_init" before "exec -T keeper python manage.py backup"
def test_restore_refuses_an_installation_with_data(…)       # deploy/.env present, no --replace -> exit 1, "--replace" in stderr; no "up", no restore_database
def test_restore_with_a_wrong_key_creates_no_volume(…)      # fake docker run of restore_fetch exits 2 -> no "compose … up", deploy/.env absent
def test_restore_runs_the_steps_in_order(…):
    # empty install, env inputs set -> order of logged calls:
    # docker run … restore_fetch, (mv .env), up -d --wait postgres, run admin restore_database, run admin restore_files,
    # up -d --wait, run admin verify_restore; no "up" that starts caddy (a bare `up -d --wait`) before restore_files
def test_replace_stops_backs_up_then_drops(…)               # stop api worker caddy < exec keeper backup < stop keeper < DROP DATABASE (on stdin) < restore_database
def test_restore_never_puts_secrets_in_argv(…)              # master key and password values in no logged argv; "-e BRIEVIE_MASTER_KEY" present
def test_recovery_sheet_is_written_with_mode_600(…)
```

- [ ] **Step 2:** FAIL. **Step 3:** Implement (POSIX sh, `set -eu`, `COMPOSE="docker compose --project-directory deploy -f deploy/docker-compose.yml"` resolved from the script's own directory; Dutch messages; shell layout the builder's, D 11).
- [ ] **Step 4:** `make test lint typecheck` → PASS. Manual: on a local stack, `BRIEVIE_BACKUP_DIR` set to an empty folder, `./brievie backup` refuses (no marker), `./brievie backup --init --allow-same-disk` then succeeds, `./brievie status` shows it. Record in the task report.
- [ ] **Step 5:** Commit `feat(deploy): ./brievie for back-up, restore, status, sweep and the recovery sheet`.

---

### Task 13: The CI restore job and `SELFHOST.md`

**Files:**
- Create: `.github/scripts/restore-seed.py`, `docs/SELFHOST.md`
- Modify: `.github/workflows/ci.yml` (new job `restore`), `README.md` (link, "Back-up" section pointing at SELFHOST), `docs/PRIVACY.md` (row "Back-ups"), `docs/GLOSSARY.md`, `docs/DECISIONS.md`

**Interfaces:**
- Consumes: everything above.
- Produces `.github/scripts/restore-seed.py` (run with `$COMPOSE exec -T api python manage.py shell < .github/scripts/restore-seed.py`; the api has the master key and the documents): creates one user (`User.objects.create_user(email, password, name=…)`), two workspaces with that user as admin (`create_workspace`), one 1-page letter in each (`create_document_from_upload` inside `workspace_context`, a JPEG-like page of 2000 random bytes, `UploadMetadataIn`), a pairing code and a paired device (`create_pairing_code`, `pair_device`); prints one JSON line `{"token": "…", "pages": [{"workspace": "…", "document": "…", "sha256": "…"}, …]}`.
- Produces job `restore` in `ci.yml` (`needs:` nothing, `timeout-minutes: 30`), steps:
  1. checkout; `sh deploy/init-env.sh`; `mkdir "$RUNNER_TEMP/backup"`; append `BRIEVIE_BACKUP_DIR=$RUNNER_TEMP/backup` to `deploy/.env`.
  2. `./brievie start`; one request through Caddy (`curl -k https://localhost/api/v1/health`), then wait until `curl -k https://localhost/api/v1/server/ca-certificate` answers 200; save it as `$RUNNER_TEMP/old-root.pem`.
  3. Seed into `$RUNNER_TEMP/seed.json` (`::add-mask::` the token).
  4. `./brievie backup --init --allow-same-disk` (the runner's temp folder shares the disk with Docker's volumes: this also proves the guard needs the flag).
  5. Keep the master key in `$RUNNER_TEMP/master-key` (masked); `$COMPOSE down --volumes`; `rm deploy/.env`; `docker volume ls` shows no `brievie_` volume.
  6. `BRIEVIE_RESTORE_SOURCE=$RUNNER_TEMP/backup BRIEVIE_RESTORE_MASTER_KEY=$(cat …) ./brievie restore --keep-hostname` (its step 8 is `verify_restore`).
  7. Assertions, for both entries of `seed.json`: `curl --fail --cacert "$RUNNER_TEMP/old-root.pem" -H "Authorization: Bearer $TOKEN" https://localhost/api/v1/workspaces/$W/documents/$D/pages/1 | sha256sum` equals the seeded `sha256` — this one request proves the sealed page opens, both workspaces are complete, the device token still signs in and the leaf chains to the old authority; plus `sha256` of a fresh `curl -k …/server/ca-certificate` equals that of `old-root.pem`.
  8. On failure `$COMPOSE ps --all` (no logs: they hold the installation code); always `$COMPOSE down --volumes`.
- Produces `docs/SELFHOST.md` (English, like `README.md` and `docs/PRIVACY.md`; the Dutch site text is the owner's, D 7.7), sections: What a back-up holds and what it does not (the master key); Choosing a destination (an external disk or NAS share via `BRIEVIE_BACKUP_DIR`, mounted before Brievie starts, owned by uid 10001 or mounted with `uid=10001` on exFAT/FAT; a REST server or S3-compatible storage via `BRIEVIE_BACKUP_REPOSITORY` with credentials in their own variables; SFTP not yet); The 3-2-1 advice in plain words (one copy off the machine, one out of the house); First set-up (`./brievie backup --init`, the same-disk refusal and `--allow-same-disk`); The recovery sheet (`./brievie recovery-sheet`, print it, keep it apart; two secrets with `--separate-password`); Restoring (`./brievie restore`, `--replace`, another host name means pairing again, the non-interactive variables); Status and the sweep; Back-ups that cannot be overwritten (pointers: `rest-server --append-only`, object lock, `BRIEVIE_BACKUP_APPEND_ONLY=true`, prune from elsewhere; U4 completes it); What B2 adds (nightly schedule, restore test).

- [ ] **Step 1:** Add the job; push the branch to a draft PR only when the owner says so — until then run the job's steps by hand locally (Docker Desktop), in order, and record the outcome in the task report. Expected: step 7 prints two matching hashes; `verify_restore` reports two workspaces, two pages opened.
- [ ] **Step 2:** Write `docs/SELFHOST.md`, the README link, the PRIVACY row ("Back-ups | your destination | encrypted by restic with the back-up password; the database dump is never on disk unencrypted; the master key is not in it"), GLOSSARY rows (herstelblad = recovery sheet, back-up = backup, terugzetten/herstellen = restore, opruimen = sweep, hoofdsleutel = master key, back-updienst = keeper), DECISIONS lines: the env copy leaves out the superuser and owner passwords (restore generates new ones); open runs are closed before the stack starts; SFTP deferred; the follow-up below.
- [ ] **Step 3:** `make test lint typecheck`; `cd web && pnpm test` untouched → PASS.
- [ ] **Step 4:** Commit `ci: restore a back-up onto an empty machine on every change` and `docs: self-hosting with back-up and restore`.

**Follow-up for B2 (recorded in DECISIONS, not built here):** the second CI case of D 4.7 (a back-up whose authority's intermediate is older than a week still signs the phone in) needs a clock moved forward in the Caddy container or a pre-made authority fixture; it belongs with the restore test.

---

## Plan decisions

1. **New app `apps.backup`**, not `apps.common`: it owns a table, jobs, ten commands and a template; `common` stays shared helpers (only `derive_backup_password` goes there, next to the other key functions).
2. **HKDF-Expand only, label `brievie/backup-password/v1`, hex output**: the master key is already uniform; Expand equals one HMAC, so `init-env.sh` computes it with `openssl dgst -mac HMAC` (LibreSSL and OpenSSL 3; checked) or stdlib `python3`, the tools it already needs. Vector pinned in Task 1. Cost: with openssl the key is in that process's argv for milliseconds at install time.
3. **The `.env` copy leaves out `POSTGRES_PASSWORD`, `BRIEVIE_OWNER_PASSWORD` and `DATABASE_OWNER_URL` besides the master key**, and the restore generates new ones: D 4.2 ("`.env` without the master key line") and D 4.5 ("no superuser password" in the keeper) contradict each other; both passwords are used only at database init and by `migrate`, so new ones lose nothing.
4. **The copy reaches the keeper through `exec -T … store_env_copy` on stdin**, filtered by the shell first: mounting `deploy/.env` would hand the keeper the master key.
5. **`BRIEVIE_BACKUP_DIR` unset falls back to a named volume `backup-unset`** that never has a marker: compose always starts, and the unset case gives "Er is nog geen back-up ingesteld". No `rslave` propagation (invalid for a named volume); the marker guards the unplugged disk instead.
6. **Marker `brievie-backup.json` beside a `restic/` repository directory**, written by a root one-off container that chowns it to 10001; `--init` refuses the documents' disk unless `--allow-same-disk`.
7. **The verification and the manifest use raw psycopg with a conninfo**, not a Django alias: pytest-django would create (or mirror) a test database for a second alias and the test would no longer run as the back-up role. B2 passes another `--dbname` and `--storage-root`.
8. **Role tests read with `row_security = off` instead of running `pg_dump`**: the backend CI job has a PostgreSQL 16 client; this proves the same property (bypass or a loud error) without one. The real `pg_dump` is proven in the `restore` job.
9. **Restored back-up jobs are not touched**: `perform_run` is a no-op for a closed run, and runs are closed right after `pg_restore` (step 5, before the stack starts in step 7) — closing them at step 8 would let the keeper act on a restored `queued` run first.
10. **`./brievie start`** is added as the explicit bring-along command (rebuild + start), because rebuilding a running server must not happen silently on the first `./brievie backup`.
11. **One-off service `admin` behind a compose profile** (rather than ad-hoc `docker run -v brievie_…`): the volumes keep compose's labels and the restore test of B2 reuses it.
12. **SFTP is not in B1**: it needs an ssh client and key handling in the image; a NAS share as a folder or a REST server covers the same case now.
13. **Separate password only before `--init`** (`--separate-password`); switching an existing repository (`restic key add/remove`) is left to U4.
14. **`BackupRun.destination_kind` instead of the destination**: D 4.8 forbids paths with names of people; the kind is enough for status.
15. **Retention groups by `host,paths` with a fixed `--host brievie`**: container host names change on every recreate, and grouping by tags would keep every run.
