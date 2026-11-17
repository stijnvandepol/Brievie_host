# Host B2: schema en hersteltest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The keeper makes a back-up every night and checks the repository every week by itself, with retention set in `deploy/.env` and validated so that a typo can never prune everything; `./brievie restore-test` restores the newest back-up into a temporary database and directory beside the live ones, runs the restore checks against them, removes both and records "Hersteltest geslaagd"; CI also proves that a back-up whose authority's intermediate has expired still signs the phone in.

**Architecture:** Pure policy code (`apps.backup.policy`: retention durations, cron schedules, "when was the last scheduled moment") feeds two Procrastinate periodic tasks with a **fixed** 15-minute tick that run in the keeper and ask `services.request_scheduled` whether a back-up or check is due; the due decision uses the keeper's own settings and catches up once after downtime. The restore test is a new module `apps.backup.restoretest`, run as a management command in the one-off `admin` container (superuser and master key), that reuses B1's `restore.find_run`, `restore.restore_database(dbname=…)` and `verify.verify_restore(conninfo=…, storage_root=…)` unchanged, plus one new guarded function `restore.restore_files_into(target=…)`; every destructive step (drop database, remove directory) sits behind a pure guard that exists and is tested first.

**Tech Stack:** As B1 (Python 3.13, Django 6.1, PostgreSQL 18, Procrastinate 3.11, psycopg 3, restic 0.18.1, pytest). `croniter` (7.0.0, already installed as Procrastinate's dependency) becomes a direct dependency.

**Spec:** `docs/superpowers/specs/2026-11-17-beheer-backup-export-updates-design.md` (cited as "D 4.5"); B2 is its row in D 8: "the nightly schedule, retention, the weekly check, `restore-test`", plus the second CI case of D 4.7. Code base: branch `feat/b1-backup` (B1 plan `docs/superpowers/plans/2026-11-17-host-b1-backup-herstel.md`, cited as "B1 Task n"). Sections 4.5, 4.7, 4.9, 9, 11 and 13 are binding.

**Not in B2:** the web card, its warnings, `getBackupStatus`/`listBackupRuns` and "Nu back-up maken" (B3; B2 only makes the warning *codes* that B3 shows); the restore drill in an audit log (D 4.9; there is no server-level audit log yet: B3 or phase 1d); `update` and the local fallback back-up (U1); export and archive folder (E); append-only set-up, SFTP, key change (U4). The server never schedules the restore test itself (D 13: it needs the superuser, so it is not a keeper job); a NAS's task scheduler or cron runs `./brievie restore-test`.

## Global Constraints

- From `CLAUDE.md`: "Code, comments, commits, table and field names in English. User-facing text in Dutch (B1): in the backend always through `gettext`". Dutch is the source language (`gettext("…Dutch…")`, no `.po` files).
- "`mypy --strict` and `ruff check` clean. No `Any`, no bare `except`." `make lint typecheck test` pass at the end of every task.
- "Datetimes are timezone-aware" (`django.utils.timezone.now()`; schedules are evaluated in `settings.TIME_ZONE`, `Europe/Amsterdam`).
- "Every mutating service function is wrapped in `transaction.atomic`." Exception, as in B1: a run's status changes are each their own short `atomic()`; no transaction is open while restic, pg_restore or `CREATE/DROP DATABASE` run.
- "No document content, tokens, passwords or BSNs in logs." Also: no master key, back-up password, database password, repository string, scratch path or file path with a name in a log line, a `BackupRun.reason`, or an exception message that reaches one. Everything restic prints passes `restic.redact()` (B1).
- "No new dependency without a line in `docs/DECISIONS.md`." `croniter` as a direct dependency gets a line (Task 2).
- "Background jobs: Procrastinate, in an app's `jobs.py`; a job takes ids only." The scheduled tasks take only Procrastinate's `timestamp`.
- "A field added to an existing API schema has a default." No API change in B2.
- "Tests first for logic. Conventional commits." Every commit ends with the trailer line `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- From D 11, not left to the builder: **the checks of the restore test** are those of D 4.6 step 8 (counts against the manifest, one page per workspace opened with the master key, the authority's fingerprint), run by B1's `verify_restore` unchanged; the restore test never touches the live database, the live documents or Caddy's volumes.
- The keeper never receives `BRIEVIE_MASTER_KEY`, `POSTGRES_PASSWORD` or `BRIEVIE_OWNER_PASSWORD` (D 4.5). The restore test runs only in the one-off `admin` container.
- Work only in the B1/B2 build worktree, Docker project `brievie-b1` (its `deploy/.env` sets `COMPOSE_PROJECT_NAME=brievie-b1`, `POSTGRES_PORT=5433`, `BRIEVIE_HTTP_PORT=8480`, `BRIEVIE_HTTPS_PORT=8443`, `BRIEVIE_IMAGE=brievie/backend:b1`). **Never touch the Docker project `brievie`** (the owner's test server). Before any command that removes containers or volumes, run `docker compose --project-directory deploy -f deploy/docker-compose.yml config --format json | grep '"name"'` and stop unless it prints `brievie-b1`.
- Verbatim from the B1 standing context (line 12), binding here: "No secret is ever a command-line argument of a process you start or script: not the master key, the back-up (restic) password, a database password (use PGPASSWORD / a conninfo passed in the environment or on stdin, never a URL or `-v name=value` in argv), nor storage credentials. Pass them through the child's environment or stdin. `docker run`/`compose run` get secrets as `-e NAME` (value from the caller's environment), never `-e NAME=value`. Every task that starts a process includes a test that intercepts the started command (fake binary on PATH or a patched subprocess call) and asserts the secret values appear in no argv."
- Verbatim from the B1 standing context, **HARD RULES after the incident of 17-11 (a test ran an unguarded "empty this directory" on `/` of the owner's Mac). These override anything else in a brief.**
  1. Code that deletes, moves, empties or overwrites files or directories is NEVER executed on the Mac itself outside the test's own temporary directory (`tmp_path`) or inside a container. No absolute path in a test for such code except under `tmp_path`.
  2. A test for a refusal ("refuse `/`", "refuse the home directory", "refuse a relative path") never calls the dangerous function with the real path. Build the guard as a PURE function that only judges a path, test that function, and only then put the destructive step behind it. Where a test must go through the caller, replace the destructive primitive (`os.scandir`, `os.unlink`, `os.rmdir`, `shutil.rmtree`, `os.replace`, `Path.unlink`) by a recorder that fails the test when called.
  3. Every destructive function carries its own refusal, independent of its caller, and that refusal exists BEFORE any test touches the function: not `/`, not the user's home directory, not a path at or above a volume's mount point other than the configured one, not a path outside the configured storage root, never following a symbolic link.
  4. Proofs against the real programs (restic, pg_dump, pg_restore, sweeping, restoring, `./brievie`) run ONLY inside containers of the build project `brievie-b1` with throwaway volumes or throwaway databases. Never bind-mount a directory of the Mac into a container other than the build worktree itself (read-only where possible); never the scratch directory, never a home directory.
  5. Shell: no `rm -r`, `rm -f`, `mv` or `>` redirect on a path built from a variable unless the script has just proved the variable is non-empty and the path lies under the script's own directory (`${var:?}` and an explicit prefix check). Prefer fixed relative names.
  6. If you are unsure whether a command could delete or overwrite something outside `tmp_path` or a container: do not run it; report instead.
- B2 adds, in the spirit of rule 3 for databases: `DROP DATABASE` is issued only by `restoretest.drop_scratch_database`, which first calls the pure guard `require_scratch_database` itself; no other code in the repository drops a database except B1's restore `--replace` shell step.

## Review Focus

1. **A typo in a retention value** (`7 d`, `7days`, `0d`, `1w`, empty, `-1d`): never a `forget` with it, never "keep nothing"; the back-up is still made, the run says why nothing was pruned, `status` warns. → Task 1 (`test_retention_typos_are_refused`, `test_invalid_retention_backs_up_but_never_prunes`).
2. **The server was off or asleep at 03:00** (a NAS in standby, a laptop, a keeper restart): one catch-up back-up when it comes back, not a burst and not nothing until the next night; a manual back-up just before or during the moment does not cause a second one. → Task 2 (`test_a_missed_moment_is_caught_up_once`, `test_a_run_finished_after_the_moment_counts`).
3. **No destination configured** (every fresh server): no failed run every night, but `status` says "Er is nog geen back-up ingesteld." → Task 2 (`test_nothing_is_requested_without_a_destination`), Task 5 (`test_status_warns_when_nothing_is_configured`).
4. **A restore test that is killed halfway** (Ctrl-C, `docker stop`, power cut): the next test removes the leftover scratch database and closes the stale run; the scratch directory dies with the `--rm` container; the live database is never named. → Task 3 (`test_only_generated_scratch_names_pass`), Task 4 (`test_leftovers_of_a_killed_test_are_removed_first`, `test_cleanup_runs_when_the_restore_fails`).
5. **Not enough disk space for the second copy**: refused before anything is created, with how much is needed and how much is free. → Task 4 (`test_not_enough_space_refuses_before_creating_anything`).

Also pinned: a restore test overlapping the nightly back-up blocks neither (Task 1 `test_a_locked_prune_keeps_the_backup_succeeded`, Task 4 `test_restore_test_never_takes_the_backup_lock`); an invalid cron never takes the server down (Task 2 `test_an_invalid_schedule_falls_back_to_the_default`).

## File Structure

```
brievie                                          # + restore-test (Task 6)
.github/workflows/ci.yml                         # job `restore`: + restore-test, + case 2 (Task 7)
.github/scripts/expire-intermediate.py           # Create: an expired intermediate signed by the real root (Task 7)
deploy/docker-compose.yml  deploy/.env.example   # keeper env: schedule and retention (Tasks 1, 2)
backend/pyproject.toml  backend/uv.lock          # croniter direct (Task 2)
backend/brievie/settings.py                      # BRIEVIE_BACKUP_SCHEDULE, _CHECK_SCHEDULE, _KEEP_* (Tasks 1, 2)
backend/apps/backup/
  policy.py            # Create: Retention, parse_duration, Schedule, parse_schedule, last_moment, next_moment
  restic.py            # forget_argv(retention); stats_argv, restore_size
  services.py          # retention from settings; tolerated locked prune; request_scheduled; status additions
  jobs.py              # scheduled_backup, scheduled_check
  models.py  migrations/0002_backuprun_restore_test.py   # Kind.RESTORE_TEST
  errors.py            # new reason codes
  locks.py             # restore_test_lock
  safepaths.py         # require_scratch_dir
  restore.py           # read_manifest (extracted from fetch), restore_files_into, root-only authority
  restoretest.py       # Create: guards, scratch database, run_restore_test
  management/commands/restore_test.py            # Create
  management/commands/backup_status.py           # schedule, retention, restore test, warnings
backend/tests/
  test_backup_policy.py  test_backup_schedule.py  test_backup_restoretest.py  test_backup_status.py   # Create
  test_backup_restic.py  test_backup_services.py  test_backup_safepaths.py  test_backup_restore.py
  test_brievie_wrapper.py  test_deploy_files.py  test_backup_models.py                            # Modify
docs/  DECISIONS.md  SELFHOST.md  GLOSSARY.md
```

---

### Task 1: Retention as settings, validated

**Files:**
- Create: `backend/apps/backup/policy.py`
- Modify: `backend/apps/backup/restic.py` (`RETENTION` → `forget_argv(retention)`), `backend/apps/backup/services.py` (`_back_up`), `backend/apps/backup/errors.py`, `backend/brievie/settings.py`, `deploy/docker-compose.yml` (keeper `environment`), `deploy/.env.example`, `docs/DECISIONS.md`
- Test: `backend/tests/test_backup_policy.py`, `backend/tests/test_backup_restic.py`, `backend/tests/test_backup_services.py`, `backend/tests/test_deploy_files.py`

**Interfaces:**
- Produces in `apps.backup.policy` (imports only `errors`, `django.conf.settings`, stdlib; pure functions take strings):
  - `@dataclass(frozen=True) class Retention: daily: str; weekly: str; monthly: str`; `DEFAULT_RETENTION = Retention("7d", "1m", "1y")`.
  - `parse_duration(value: str) -> str`: strips surrounding whitespace; accepts only restic's duration grammar `^(\d+y)?(\d+m)?(\d+d)?(\d+h)?$` (lower case, in that order, at least one part) with a total greater than zero; returns the stripped value; anything else raises `BackupError("retention_invalid")`.
  - `parse_retention(daily: str, weekly: str, monthly: str) -> Retention` (each through `parse_duration`).
  - `retention_from_settings() -> Retention` from `BRIEVIE_BACKUP_KEEP_DAILY/_WEEKLY/_MONTHLY`.
- Produces in `apps.backup.restic`: `forget_argv(retention: Retention) -> list[str]` = `["forget", "--prune", "--host", HOST, "--group-by", "host,paths", "--keep-within-daily", d, "--keep-within-weekly", w, "--keep-within-monthly", m]`; it re-parses all three itself (its own refusal: no caller can hand it a keep-nothing policy). `RETENTION` is removed.
- Produces settings (raw strings, validated at use, never at import): `BRIEVIE_BACKUP_KEEP_DAILY: str = "7d"`, `BRIEVIE_BACKUP_KEEP_WEEKLY: str = "1m"`, `BRIEVIE_BACKUP_KEEP_MONTHLY: str = "1y"`.
- Produces `REASONS["retention_invalid"]` (Dutch, the builder's wording; it names the three variables and says that nothing was removed).
- Changes in `services._back_up`: the retention is read **before** the dump (`retention_from_settings()`, a `BackupError` kept as `None`); the back-up is made either way. After the files snapshot: append-only → no forget (unchanged); `None` → no forget, the run ends `failed` with `reason_code="retention_invalid"` and `size_bytes` (B1's `_IncompleteBackupError` becomes `_KeptBackupError(code: str, size_bytes: int | None)`, used for `files_incomplete` and `retention_invalid`); otherwise `forget_argv(retention)`, where a result that `classify`s as `repository_locked` (exit 11: a restore test or another reader holds the repository) is logged as a warning and leaves the run `succeeded`; the next night prunes.

- [ ] **Step 1: Failing tests**

```python
# test_backup_policy.py
@pytest.mark.parametrize("value", ["7d", "1m", "1y", "2y6m", "1y2m3d4h", " 7d "])
def test_restic_durations_are_accepted(value): assert parse_duration(value) == value.strip()
@pytest.mark.parametrize("value", ["", " ", "7", "7 d", "7days", "1w", "0d", "0y0m0d0h", "-1d", "7D", "d7", "1d1y", "7d;rm", "∞"])
def test_retention_typos_are_refused(value):       # BackupError code "retention_invalid"
def test_default_retention_is_seven_days_a_month_a_year(): assert DEFAULT_RETENTION == Retention("7d", "1m", "1y")
def test_retention_comes_from_settings(settings)   # KEEP_DAILY="14d" -> Retention("14d", "1m", "1y")

# test_backup_restic.py (replaces test_forget_prunes_with_keep_within_and_never_groups_by_tags)
def test_forget_uses_the_given_retention():
    assert forget_argv(Retention("14d", "2m", "3y")) == ["forget", "--prune", "--host", "brievie",
        "--group-by", "host,paths", "--keep-within-daily", "14d", "--keep-within-weekly", "2m",
        "--keep-within-monthly", "3y"]
def test_forget_refuses_a_keep_nothing_retention(): # Retention("0d", "1m", "1y") -> retention_invalid; never "--unsafe-allow-remove-all"

# test_backup_services.py
@pytest.mark.django_db(transaction=True)
def test_invalid_retention_backs_up_but_never_prunes(settings, configured_folder, fake_restic, fake_tool):
    # KEEP_WEEKLY="1w" -> both backup calls made; no call starting with ["forget", "--prune"];
    # run.status == "failed", reason_code == "retention_invalid", size_bytes == sum of the summaries
@pytest.mark.django_db(transaction=True)
def test_configured_retention_reaches_forget(settings, …)        # KEEP_DAILY="3d" -> forget call contains "--keep-within-daily", "3d"
@pytest.mark.django_db(transaction=True)
def test_a_locked_prune_keeps_the_backup_succeeded(…)            # fake restic: forget exits 11 -> status "succeeded"; a WARNING mentions the run id, not the repository

# test_deploy_files.py
def test_keeper_gets_the_retention_settings():   # service("keeper") env has BRIEVIE_BACKUP_KEEP_DAILY: ${BRIEVIE_BACKUP_KEEP_DAILY:-7d} (and _WEEKLY 1m, _MONTHLY 1y)
```

- [ ] **Step 2:** `cd backend && uv run pytest tests/test_backup_policy.py tests/test_backup_restic.py tests/test_backup_services.py tests/test_deploy_files.py -q` → the new tests FAIL (no `policy` module, `forget_argv` takes no argument).
- [ ] **Step 3: Implement** the interfaces above. `.env.example`, block "Back-up": commented `# BRIEVIE_BACKUP_KEEP_DAILY=7d`, `…_WEEKLY=1m`, `…_MONTHLY=1y` with one comment line: restic durations (`y`, `m`, `d`, `h`, e.g. `2y6m`); an invalid value means nothing is pruned and `./brievie status` warns. Compose keeper environment: the three with `:-` defaults. `DECISIONS.md`, new section "2026-11-17: Schedule and restore test (B2)", line 1: retention values are validated against restic's duration grammar and must be positive; an invalid value never prunes and fails the run visibly after the back-up is made; a prune that meets a locked repository is skipped until the next run.
- [ ] **Step 4:** `make test lint typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(backup): retention from deploy/.env, refused when it could keep nothing`.

---

### Task 2: The nightly back-up and the weekly check

**Files:**
- Modify: `backend/apps/backup/policy.py`, `backend/apps/backup/services.py`, `backend/apps/backup/jobs.py`, `backend/apps/backup/errors.py`, `backend/brievie/settings.py`, `backend/pyproject.toml` + `backend/uv.lock` (`croniter>=7.0.0` in `[project].dependencies`), `deploy/docker-compose.yml` (keeper env), `deploy/.env.example`, `docs/DECISIONS.md`
- Test: `backend/tests/test_backup_policy.py`, `backend/tests/test_backup_schedule.py`, `backend/tests/test_deploy_files.py`

**Interfaces:**
- Produces in `apps.backup.policy`:
  - `DEFAULT_BACKUP_SCHEDULE = "0 3 * * *"`, `DEFAULT_CHECK_SCHEDULE = "0 4 * * 0"` (Sunday 04:00, an hour after the back-up).
  - `@dataclass(frozen=True) class Schedule: cron: str; valid: bool` (`valid=False`: the configured value was refused and `cron` is the default).
  - `parse_schedule(value: str, *, default: str) -> Schedule`: accepted when, after stripping, it has exactly five space-separated fields, the minute field is one integer 0–59 (at most hourly: the tick could not honour more), and `croniter.is_valid` accepts it; an empty value gives `Schedule(default, True)`; anything else `Schedule(default, False)`. Never raises: a typo must not stop the back-up or any process.
  - `backup_schedule() -> Schedule` (`BRIEVIE_BACKUP_SCHEDULE`), `check_schedule() -> Schedule` (`BRIEVIE_BACKUP_CHECK_SCHEDULE`).
  - `last_moment(cron: str, now: datetime) -> datetime` and `next_moment(cron: str, now: datetime) -> datetime`: `croniter(cron, timezone.localtime(now))` `get_prev`/`get_next(datetime)`, aware, in `settings.TIME_ZONE`. `last_moment` of a `now` that is exactly a moment returns `now`.
- Produces in `apps.backup.services`: `request_scheduled(kind: str, *, now: datetime) -> BackupRun | None`, `@transaction.atomic`, takes `_REQUEST_LOCK_KEY` (as `request_run`), then:
  1. `destination_kind() == ""` → `None` (nothing configured: no run, no failure; a `BackupError` from `destination_kind` falls through to step 3 so a broken setting fails visibly once per moment);
  2. `moment = last_moment(schedule.cron, now)` with the kind's schedule; not due → `None`. **Due** means: no `BackupRun` of this kind is open, and none has `created_at >= moment` or `finished_at >= moment`;
  3. `request_run(kind, requested_by="schedule")`.
- Produces in `apps.backup.jobs`:
  - `SCHEDULE_TICK = "*/15 * * * *"`.
  - `@app.periodic(cron=SCHEDULE_TICK) @app.task(name="scheduled_backup", queue=QUEUE, queueing_lock="scheduled_backup") def scheduled_backup(timestamp: int) -> None` → `services.request_scheduled(BackupRun.Kind.BACKUP, now=timezone.now())`.
  - Same for `scheduled_check` (`queueing_lock="scheduled_check"`, `Kind.CHECK`).
  - The tick is fixed in code; the configured cron is read only inside the job, i.e. in the keeper. Every worker (also the pipeline `worker`, which has no back-up settings) defers periodic tasks; Procrastinate's periodic-defer table makes one defer per tick, and the queueing lock keeps at most one waiting when no keeper runs.
- Produces settings `BRIEVIE_BACKUP_SCHEDULE: str = ""` and `BRIEVIE_BACKUP_CHECK_SCHEDULE: str = ""` (empty = default); `REASONS["schedule_invalid"]` (Dutch: the schedule in `deploy/.env` is not valid, the default every night at 03:00 / every Sunday at 04:00 is used; builder's wording).
- `BackupRun.requested_by` takes `"schedule"` (8 characters, fits `max_length=10`).

- [ ] **Step 1: Failing tests**

```python
# test_backup_policy.py
@pytest.mark.parametrize("value", ["0 3 * * *", "30 1 * * 1-5", "0 2 1 * *", " 15 4 * * 0 "])
def test_valid_schedules_are_kept(value): assert parse_schedule(value, default="0 3 * * *") == Schedule(value.strip(), True)
@pytest.mark.parametrize("value", ["* * * * *", "*/5 3 * * *", "0 3 * *", "0 3 * * * *", "0 25 * * *", "nightly", "@daily", "0 3 * * mon;x"])
def test_an_invalid_schedule_falls_back_to_the_default(value):
    assert parse_schedule(value, default="0 3 * * *") == Schedule("0 3 * * *", False)
def test_an_empty_schedule_is_the_default()        # Schedule("0 3 * * *", True)
def test_last_moment_is_local_time(settings)       # now = 2026-11-17 02:10Z (03:10 Amsterdam) -> 2026-11-17 03:00+01:00
def test_last_moment_on_the_moment_is_now()
def test_last_moment_across_daylight_saving()      # now = 2027-03-28 05:00 Amsterdam -> 2027-03-28 03:00+02:00
def test_next_moment_of_the_weekly_check()         # Tue 2026-11-17 -> Sun 2026-11-22 04:00+01:00

# test_backup_schedule.py  (ProcrastinateJob rows show what was deferred)
def test_nothing_is_requested_without_a_destination(settings)        # destination unset, 03:10 -> None; BackupRun count 0; no job
def test_a_due_backup_is_requested_by_the_schedule(configured_folder)  # 03:10, no runs -> run kind backup, requested_by "schedule"
def test_the_next_tick_requests_nothing(configured_folder)           # 03:10 then 03:25 -> one run
def test_an_open_run_blocks_the_schedule(configured_folder)          # manual run queued at 02:55 -> None at 03:10
def test_a_run_finished_after_the_moment_counts(configured_folder)   # manual run created 02:50, finished 03:05 -> None at 03:10
def test_a_missed_moment_is_caught_up_once(configured_folder)        # last run two days ago, now 14:00 -> one run; again at 14:15 -> None
def test_a_failed_scheduled_run_is_not_retried_before_the_next_moment(configured_folder)  # failed at 03:01 -> None at 03:15 and at 23:00; due at 03:00 next day
def test_the_weekly_check_uses_its_own_schedule(configured_folder)   # Sun 04:10 -> kind check; Mon 04:10 after it -> None
def test_an_invalid_schedule_still_backs_up_at_the_default(settings, configured_folder)  # SCHEDULE="* * * * *", 03:10 -> run requested
def test_a_broken_destination_setting_fails_visibly(settings)        # folder and repository both set -> a run is requested (it fails with destination_conflict)
def test_ticks_are_periodic_with_a_queueing_lock():
    # jobs.scheduled_backup / scheduled_check: registered in app.periodic_registry with cron "*/15 * * * *",
    # queue "backup", queueing_lock == task name

# test_deploy_files.py
def test_keeper_gets_the_schedules()   # BRIEVIE_BACKUP_SCHEDULE: ${BRIEVIE_BACKUP_SCHEDULE:-} and _CHECK_SCHEDULE in service("keeper")
```

- [ ] **Step 2:** `cd backend && uv run pytest tests/test_backup_policy.py tests/test_backup_schedule.py tests/test_deploy_files.py -q` → FAIL.
- [ ] **Step 3: Implement.** `uv add 'croniter>=7.0.0'` (lock stays at 7.0.0). If `mypy --strict` reports `import-untyped` for croniter, add `types-croniter` to the dev group in the same commit (the DECISIONS line covers both). `.env.example`: commented `# BRIEVIE_BACKUP_SCHEDULE=0 3 * * *` and `# BRIEVIE_BACKUP_CHECK_SCHEDULE=0 4 * * 0`, comment: cron, server time zone, minute must be one number, checked every quarter of an hour, a back-up missed while the server was off is made once when it is back. DECISIONS line 2: the fixed 15-minute tick and why (below, Plan decisions 1–2); line 3: **croniter 7.0.0** (MIT), already installed through Procrastinate, now imported directly for the "last scheduled moment"; rejected: a hand-written cron parser.
- [ ] **Step 4:** `make test lint typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(backup): a nightly back-up and a weekly check by the keeper`.

---

### Task 3: The restore-test kind and its guards

**Files:**
- Create: `backend/apps/backup/restoretest.py` (guards and scratch-database functions only), `backend/apps/backup/migrations/0002_backuprun_restore_test.py`
- Modify: `backend/apps/backup/models.py`, `backend/apps/backup/safepaths.py`, `backend/apps/backup/locks.py`, `backend/apps/backup/errors.py`, `backend/apps/backup/restic.py`
- Test: `backend/tests/test_backup_restoretest.py`, `backend/tests/test_backup_safepaths.py`, `backend/tests/test_backup_models.py`, `backend/tests/test_backup_restic.py`

**Interfaces:**
- Produces `BackupRun.Kind.RESTORE_TEST = "restore_test"` and migration `0002` (choices only; `makemigrations --check` clean).
- Produces in `apps.backup.restoretest`:
  - `SCRATCH_DB_PREFIX = "brievie_restoretest_"`, `SCRATCH_DIR_PREFIX = "brievie-restoretest-"`, `SCRATCH_ROOT = Path("/var/tmp")` (inside the admin container's own layer, never a volume: `run --rm` removes whatever a killed test leaves there).
  - `new_scratch_token() -> str` = `secrets.token_hex(6)`; names are always `SCRATCH_DB_PREFIX + token` and `SCRATCH_DIR_PREFIX + token`.
  - `require_scratch_database(name: str, *, live: Sequence[str]) -> str` (pure): accepted only when `re.fullmatch(r"brievie_restoretest_[0-9a-f]{12}", name)` and `name` is not in `live`; else `BackupError("unsafe_database")`. `live` = the default database's `NAME`, the superuser conninfo's database, `"brievie"`, `"postgres"`, `"template0"`, `"template1"` (helper `live_database_names() -> list[str]`).
  - `create_scratch_database(name: str) -> None`: guard first; superuser on database `postgres`, autocommit, `CREATE DATABASE {name} OWNER brievie_owner TEMPLATE template0` via `psycopg.sql.Identifier` (the owner mirrors `database.psql`, so the dump's ownership statements apply).
  - `drop_scratch_database(name: str) -> None`: guard first; `DROP DATABASE IF EXISTS {name} WITH (FORCE)`.
  - `leftover_scratch_databases() -> list[str]`: `SELECT datname FROM pg_database WHERE datname LIKE 'brievie\_restoretest\_%'`, each filtered through the guard (a name that does not pass is never returned).
  - psycopg errors become `BackupError("restore_failed")` without the driver's message (it can quote the conninfo).
- Produces in `apps.backup.safepaths`: `require_scratch_dir(directory: Path, *, root: Path, live: Sequence[Path | str]) -> Path` (judges only): the name matches `brievie-restoretest-[0-9a-f]{12}`; `directory.parent.resolve() == root.resolve()`; not a link and not reached through one (`_through_link`); passes `require_emptiable(directory, allowed=[directory])` (the system, home and depth refusals); and is neither at, under nor above any `live` path (compared resolved). Else `BackupError("unsafe_path")`.
- Produces in `apps.backup.locks`: `RESTORE_TEST_LOCK_KEY: int = int.from_bytes(b"brvrtest", "big")`; `@contextmanager restore_test_lock() -> Iterator[None]` — same shape as `backup_lock` (default connection, outside a transaction) with `BackupError("restore_test_busy")`; `backup_lock` keeps its behaviour (a shared private helper for both is allowed).
- Produces in `apps.backup.restic`: `stats_argv(snapshot_id: str) -> list[str]` = `["stats", "--json", "--mode", "restore-size", snapshot_id]`; `restore_size(stdout: bytes) -> int` (`total_size`; `BackupError("restic_failed")` when absent).
- Produces `REASONS` (Dutch, builder's wording): `unsafe_database` ("Brievie weigert deze database te wissen, omdat dat niet veilig is."), `restore_test_busy`, `restore_test_no_space`, `nothing_to_test` ("Er is nog geen back-up om te testen."), `restore_test_problems`, `restore_test_never`, `restore_test_old`.

- [ ] **Step 1: Failing tests** (pure guards first; no destructive call in this task's tests except the one throwaway-database test)

```python
# test_backup_restoretest.py
def test_only_generated_scratch_names_pass():
    name = SCRATCH_DB_PREFIX + new_scratch_token()
    assert require_scratch_database(name, live=["brievie"]) == name
@pytest.mark.parametrize("name", ["brievie", "postgres", "template1", "brievie_restoretest_", "brievie_restoretest_XYZ",
    "brievie_restoretest_0123456789ab; DROP", "brievie_restoretest_0123456789abc", "x_brievie_restoretest_0123456789ab"])
def test_anything_else_is_never_a_scratch_database(name):   # BackupError "unsafe_database"
def test_the_live_database_is_refused_even_with_the_prefix():  # live=["brievie_restoretest_0123456789ab"] -> unsafe_database
def test_drop_refuses_before_connecting(monkeypatch):        # psycopg.connect patched to fail the test; drop_scratch_database("brievie") -> unsafe_database
def test_leftovers_are_only_names_that_pass_the_guard(monkeypatch)  # fake rows ["brievie_restoretest_0123456789ab", "brievie_restoretest_evil"] -> first only
@pytest.mark.django_db(transaction=True)
def test_scratch_database_is_created_and_dropped(settings, owner_conninfo):
    # throwaway database in the build project's PostgreSQL container; DATABASE_SUPERUSER = the owner's config (it has CREATEDB)
    # create -> listed by leftover_scratch_databases(); drop -> not listed; the test database is untouched

# test_backup_safepaths.py
def test_a_generated_scratch_dir_is_accepted(tmp_path)     # tmp_path / "brievie-restoretest-0123456789ab", root=tmp_path, live=[tmp_path / "documents"]
@pytest.mark.parametrize("name", ["brievie-restoretest-", "documents", "brievie-restoretest-0123456789ab/..", "brievie-restoretest-ZZZ"])
def test_other_names_are_not_scratch_dirs(tmp_path, name)  # unsafe_path
def test_a_scratch_dir_outside_the_root_is_refused(tmp_path)        # parent is tmp_path/"other"
def test_a_scratch_dir_through_a_link_is_refused(tmp_path)          # root reached through a symlink
def test_a_scratch_dir_over_a_live_path_is_refused(tmp_path)        # live=[the scratch dir / "data/documents"] and live=[its parent] -> unsafe_path
@pytest.mark.parametrize("root", ["/", "/var", str(Path.home())])
def test_system_roots_never_hold_a_scratch_dir(root)               # judged only: directory = Path(root) / "brievie-restoretest-0123456789ab"

# test_backup_models.py
def test_restore_test_is_a_kind(): assert BackupRun.Kind.RESTORE_TEST == "restore_test"
# test_backup_restic.py
def test_stats_reads_the_restore_size()     # stats_argv("abc") exact list; restore_size(b'{"total_size": 123, "total_file_count": 2}') == 123
@pytest.mark.django_db(transaction=True)
def test_restore_test_lock_is_exclusive_but_not_the_backup_lock()  # inside restore_test_lock(): backup_lock() can be taken; a second restore_test_lock() -> restore_test_busy
```

- [ ] **Step 2:** `cd backend && uv run pytest tests/test_backup_restoretest.py tests/test_backup_safepaths.py tests/test_backup_models.py tests/test_backup_restic.py -q` → FAIL.
- [ ] **Step 3: Implement**; `uv run python manage.py makemigrations backup -n backuprun_restore_test`.
- [ ] **Step 4:** `make test lint typecheck` → PASS (incl. `makemigrations --check`).
- [ ] **Step 5:** Commit `feat(backup): the restore-test kind and guards for its scratch database and directory`.

---

### Task 4: The restore test

**Files:**
- Modify: `backend/apps/backup/restoretest.py`, `backend/apps/backup/restore.py`
- Create: `backend/apps/backup/management/commands/restore_test.py`
- Test: `backend/tests/test_backup_restoretest.py`, `backend/tests/test_backup_restore_fetch.py` (still green after the extraction)

**Interfaces:**
- Consumes: Task 3; B1 `restore.find_run(repository, password, run) -> RunSnapshots`, `restore.restore_database(*, run: str | None, dbname: str) -> int`, `verify.verify_restore(*, conninfo, storage_root, manifest, master_key, authority_dir) -> VerifyReport`, `restore.parse_master_key`, `services.file_paths()`, `destination.configured_repository()`, `destination.backup_password()`, `database.superuser_conninfo(dbname=…)`.
- Produces in `apps.backup.restore`:
  - `read_manifest(repository: Repository, password: str, found: RunSnapshots, *, extra_secrets: Sequence[str] = ()) -> tuple[Manifest, str]` — the manifest step of `fetch` moved out verbatim (dump, decode, `manifest_from_json`, run-id match); `fetch` calls it. Returns the parsed manifest and its text.
  - `restore_files_into(*, found: RunSnapshots, repository: Repository, password: str, target: Path, scratch_root: Path) -> None`: first `safepaths.require_scratch_dir(target, root=scratch_root, live=[*file_paths(), "/caddy-data", settings.BRIEVIE_BACKUP_MOUNT])`; then `restic.restore_argv(found.files.id, str(target), file_paths())` through `_restic_output`. Never takes `backup_lock`, never places an authority, never empties anything. B1's `restore_files` is unchanged.
  - `_RESTORE_CODES` gains `repository_locked` (a restore test that meets a prune says so).
- Produces in `apps.backup.restoretest`:
  - `DB_SPACE_FACTOR = 3`, `SPACE_MARGIN = 256 * 1024 * 1024`; `required_space(*, files_bytes: int, dump_bytes: int) -> int` = `files_bytes + DB_SPACE_FACTOR * dump_bytes + SPACE_MARGIN` (pure).
  - `class NotEnoughSpaceError(BackupError)`: code `restore_test_no_space`, attributes `needed: int`, `free: int`.
  - `@dataclass(frozen=True) class RestoreTestReport: run_id: UUID; tested_run_id: UUID; backup_created_at: datetime; report: VerifyReport`.
  - `run_restore_test(*, scratch_root: Path = SCRATCH_ROOT) -> RestoreTestReport`. Order, pinned:
    1. `with restore_test_lock():` (never `backup_lock`: a nightly back-up may run beside it; restic's own shared repository lock keeps prune from removing data this test reads, Task 1 makes that prune wait a night);
    2. close open `restore_test` runs as `interrupted` with `reason_code="abandoned"` (the lock proves nobody runs them); `drop_scratch_database` for each `leftover_scratch_databases()`;
    3. create the run (`kind=restore_test`, `status=running`, `started_at`, `requested_by="cli"`, `destination_kind`), own `atomic`;
    4. repository, password; `find_run(…, None)` (`run_not_found` → `nothing_to_test`); `read_manifest`; `unknown_migrations` non-empty → `newer_backup`;
    5. space: `restore_size` of `stats_argv(found.files.id)` and of `stats_argv(found.db.id)`; `shutil.disk_usage(scratch_root).free < required_space(…)` → `NotEnoughSpaceError` — before anything is created;
    6. `token = new_scratch_token()`; `name`, `directory = scratch_root / (SCRATCH_DIR_PREFIX + token)`; `require_scratch_dir` and `require_scratch_database` before use; `directory.mkdir(mode=0o700)`;
    7. `try:` `create_scratch_database(name)`; `restore_database(run=str(found.run_id), dbname=name)`; `restore_files_into(…, target=directory, scratch_root=scratch_root)`; `verify_restore(conninfo=superuser_conninfo(dbname=name), storage_root=directory / Path(settings.BRIEVIE_STORAGE_ROOT).relative_to("/"), manifest=manifest, master_key=parse_master_key(settings.BRIEVIE_MASTER_KEY), authority_dir=directory / Path(settings.BRIEVIE_AUTHORITY_COPY_DIR).relative_to("/"))`; `finally:` `drop_scratch_database(name)` and `_remove_scratch_dir(directory, root=scratch_root)` (guard again, then `shutil.rmtree`), each in its own `try` so one failing clean-up never skips the other; a failed clean-up is logged with the run id and the error's type only;
    8. finish the run: no problems → `succeeded`; problems → `failed`, `restore_test_problems`; a `BackupError` → `failed` with its code, re-raised; any other exception → `internal_error`, re-raised as `BackupError("internal_error")`.
- Produces command `restore_test` (in `admin`, root): installs a SIGTERM handler that raises `SystemExit(143)` so step 7's `finally` runs on `docker stop`; calls `run_restore_test()`; prints (Dutch, builder's wording, all through gettext): success "Hersteltest geslaagd op <datum tijd>: back-up van <datum>, <n> werkruimtes, <m> pagina's geopend. De tijdelijke kopie is weer verwijderd." (exit 0); problems: each problem on stderr, then a count sentence (exit 1); `NotEnoughSpaceError`: the reason plus "Nodig: ongeveer <needed>, vrij: <free>." with `filesizeformat` (exit 2); `not_configured`, `destination_not_ready`, `no_backup_password`, `wrong_password`, `nothing_to_test`, `restore_test_busy`: the reason (exit 2); any other `BackupError`: the reason (exit 1). Never prints the master key, the scratch path or the scratch database name.

- [ ] **Step 1: Failing tests** (fake restic from `backup_support.py`; `create_scratch_database`, `drop_scratch_database`, `leftover_scratch_databases`, `restore.restore_database` and `verify.verify_restore` monkeypatched with recorders; `scratch_root=tmp_path`; `shutil.disk_usage` patched)

```python
@pytest.mark.django_db(transaction=True)
def test_a_passing_test_restores_into_scratch_and_cleans_up(configured_folder, fake_restic, recorders, tmp_path):
    # restore_database called with dbname matching SCRATCH_DB_PREFIX + 12 hex, never "brievie";
    # restic restore argv == ["restore", <files id>, "--target", <tmp_path/brievie-restoretest-…>, "--include", "/data/documents", …];
    # verify_restore got storage_root == <scratch>/data/documents and authority_dir == <scratch>/data/authority;
    # drop called with the same name; scratch dir gone; run kind restore_test, status succeeded
@pytest.mark.django_db(transaction=True)
def test_problems_fail_the_run_and_still_clean_up(…)        # verify returns 1 problem -> status failed, reason_code restore_test_problems; drop called; dir gone
@pytest.mark.django_db(transaction=True)
def test_cleanup_runs_when_the_restore_fails(…)             # restore_database raises BackupError("restore_failed") -> drop called; dir gone; run failed "restore_failed"
@pytest.mark.django_db(transaction=True)
def test_a_failing_drop_still_removes_the_directory(…)      # drop raises -> dir gone; log names the run id, not the database name
@pytest.mark.django_db(transaction=True)
def test_not_enough_space_refuses_before_creating_anything(…):
    # stats -> files 10 GB, dump 1 GB; free 5 GB -> NotEnoughSpaceError(needed == required_space(...), free == 5 GB);
    # create never called; tmp_path has no brievie-restoretest-* entry; run failed "restore_test_no_space"
@pytest.mark.django_db(transaction=True)
def test_leftovers_of_a_killed_test_are_removed_first(…)    # an open restore_test run + leftover ["brievie_restoretest_0123456789ab"] -> run interrupted "abandoned"; drop called for it before create
@pytest.mark.django_db(transaction=True)
def test_nothing_to_test_without_a_complete_backup(…)       # snapshots only kind:db -> BackupError "nothing_to_test"; create never called
@pytest.mark.django_db(transaction=True)
def test_restore_test_never_takes_the_backup_lock(…)        # a second connection holds BACKUP_LOCK_KEY -> the test still passes
def test_restore_files_into_refuses_a_live_target(fake_restic, tmp_path):
    # target = tmp_path / "documents" (not a scratch name) -> unsafe_path; fake_restic.calls == []
def test_required_space_counts_three_times_the_dump(): assert required_space(files_bytes=10, dump_bytes=5) == 25 + SPACE_MARGIN
def test_no_secret_is_in_any_argv_of_a_restore_test(…)      # master key, back-up password, superuser password in no recorded argv (fake restic, fake pg_restore)
def test_command_exit_codes(monkeypatch, capsys):           # report ok -> 0 and "Hersteltest geslaagd"; problems -> 1; NotEnoughSpaceError -> 2 with both sizes; nothing_to_test -> 2
def test_command_never_prints_the_key_or_scratch_names(…)   # neither the master key nor "brievie_restoretest_" nor the scratch path in stdout/stderr
```

(`recorders` is a fixture in this test file that installs the five monkeypatched functions and exposes their calls.)

- [ ] **Step 2:** `cd backend && uv run pytest tests/test_backup_restoretest.py tests/test_backup_restore_fetch.py -q` → new tests FAIL, `fetch` tests PASS.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `make test lint typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(backup): restore test into a scratch database and directory, always cleaned up`.

---

### Task 5: Status: schedule, retention, restore test and warnings

**Files:**
- Modify: `backend/apps/backup/services.py` (`BackupStatus`, `status_summary`), `backend/apps/backup/management/commands/backup_status.py`, `backend/apps/backup/policy.py`
- Test: `backend/tests/test_backup_status.py`

**Interfaces:**
- Produces in `apps.backup.policy`: `RESTORE_TEST_MAX_AGE = timedelta(days=90)`; `status_warnings(*, destination_kind: str, has_success: bool, last_restore_test_at: datetime | None, retention_valid: bool, schedules_valid: bool, now: datetime) -> list[str]` (pure) returning codes in this order: `not_configured` (kind `""`), `schedule_invalid`, `retention_invalid`, `restore_test_never` (configured, a success exists, never tested), `restore_test_old` (`now - last_restore_test_at > RESTORE_TEST_MAX_AGE`). B3 shows the same codes in the web.
- Produces `BackupStatus` fields (appended after B1's): `last_restore_test: BackupRun | None` (newest `restore_test` that `succeeded`), `backup_schedule: Schedule`, `check_schedule: Schedule`, `next_backup: datetime | None` and `next_check: datetime | None` (`None` when not configured), `retention: Retention | None` (`None` when invalid), `warnings: list[str]`.
- `backup_status` prints after B1's lines (Dutch, builder's wording): "Schema back-up: 0 3 * * * (volgende: 18-11-2026 03:00)", "Schema controle: …", "Bewaren: elke back-up 7d, per week 1m, per maand 1y" (or the invalid sentence), "Laatste hersteltest: 17-11-2026 21:03 (geslaagd)" or "nog nooit", then one "Let op: <REASONS[code]>" line per warning. `_open_run_line` names a running restore test "hersteltest".

- [ ] **Step 1: Failing tests**

```python
def test_status_warns_when_nothing_is_configured()           # status_warnings(destination_kind="", …) == ["not_configured"]; message == "Er is nog geen back-up ingesteld."
def test_untested_backups_are_warned_about()                 # kind "folder", has_success, never tested -> ["restore_test_never"]
def test_a_restore_test_older_than_90_days_is_warned_about() # 91 days -> ["restore_test_old"]; 89 days -> []
def test_no_restore_test_warning_before_the_first_backup()   # has_success False -> []
def test_invalid_settings_are_warned_about()                 # retention_valid False, schedules_valid False -> ["schedule_invalid", "retention_invalid"]
@pytest.mark.django_db
def test_status_shows_schedule_retention_and_last_restore_test(settings, configured_folder, capsys):
    # a succeeded restore_test run; call_command("backup_status") -> "Schema back-up: 0 3 * * *", "volgende:", "Laatste hersteltest:", no "Let op"
@pytest.mark.django_db
def test_status_prints_one_line_per_warning(settings, capsys) # nothing configured -> "Let op: Er is nog geen back-up ingesteld."
@pytest.mark.django_db
def test_a_running_restore_test_is_named(capsys)             # open restore_test run -> "hersteltest" in the output
```

- [ ] **Step 2:** `cd backend && uv run pytest tests/test_backup_status.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `make test lint typecheck` → PASS.
- [ ] **Step 5:** Commit `feat(backup): status shows the schedule, retention, the last restore test and its warnings`.

---

### Task 6: `./brievie restore-test`

**Files:**
- Modify: `brievie`
- Test: `backend/tests/test_brievie_wrapper.py`

**Interfaces:**
- Consumes: the command `restore_test` (Task 4), B1's wrapper (bring-along step, `$COMPOSE`, the running-services check, the fake-docker harness `installed` / `FAKE_DOCKER_RUNNING` / `FAKE_DOCKER_FAIL`).
- Produces `./brievie restore-test` (and a line in `help`/usage): brings the installation along (as every command); refuses with "Brievie draait niet. Start Brievie met ./brievie start." (exit 1) unless `$COMPOSE ps --status running --services` lists `postgres`; prints "Hersteltest: de nieuwste back-up wordt teruggezet in een tijdelijke database en map. Brievie blijft gewoon werken. Dit kan even duren."; then `$COMPOSE run --rm --no-deps -T admin python manage.py restore_test` and exits with its exit code. No `-e` options: the admin service has its environment from compose. Nothing else is written on the host.

- [ ] **Step 1: Failing tests**

```python
def test_restore_test_runs_in_the_one_off_container(installed)   # log has "run --rm --no-deps -T admin python manage.py restore_test"; no "exec", no "stop", no "down"
def test_restore_test_without_postgres_says_so(installed)        # FAKE_DOCKER_RUNNING="" -> exit 1, "./brievie start" in stderr; no "run" in the log
def test_restore_test_passes_the_exit_code_on(installed)         # fake run exits 2 -> wrapper exits 2
def test_restore_test_never_puts_secrets_in_argv(installed)      # master key, back-up and database passwords of deploy/.env in no logged argv
def test_usage_lists_restore_test(installed)
```

- [ ] **Step 2:** `cd backend && uv run pytest tests/test_brievie_wrapper.py -q` → FAIL.
- [ ] **Step 3: Implement** (POSIX sh, as B1's wrapper; no `rm`/`mv`).
- [ ] **Step 4:** `make test lint typecheck` → PASS. Manual proof in the build project `brievie-b1` only (project-name check first): stack up with a folder destination initialised (`--allow-same-disk`), one back-up made, `./brievie restore-test` → "Hersteltest geslaagd …", then `docker compose --project-directory deploy -f deploy/docker-compose.yml exec -T postgres psql -U postgres -d postgres -Atc "SELECT count(*) FROM pg_database WHERE datname LIKE 'brievie\_restoretest\_%'"` prints `0`; `./brievie status` shows "Laatste hersteltest". Record in the task report.
- [ ] **Step 5:** Commit `feat(deploy): ./brievie restore-test`.

---

### Task 7: CI (restore test and an expired intermediate), root-only authority, docs

**Files:**
- Create: `.github/scripts/expire-intermediate.py`
- Modify: `.github/workflows/ci.yml` (job `restore`), `backend/apps/backup/restore.py` (`_place_authority`: root only), `docs/SELFHOST.md`, `docs/GLOSSARY.md`, `docs/DECISIONS.md`
- Test: `backend/tests/test_backup_restore.py`

**Interfaces:**
- Changes B1's `_AUTHORITY_FILES` used by `_place_authority` to `("root.crt", "root.key")`: a restore places Caddy's root and its key only; any `intermediate.*` already in the target is removed (as every name not placed is today), so Caddy creates a fresh intermediate from the restored root on its first start. Phones trust the root (its fingerprint is the manifest's), not the intermediate. The files snapshot still contains the intermediate (the keeper's copy is unchanged).
- Produces `.github/scripts/expire-intermediate.py` (run as `$COMPOSE run --rm --no-deps -T admin python - < .github/scripts/expire-intermediate.py`; stdlib and `cryptography` only): reads `/data/authority/root.crt` and `root.key`; writes `/data/authority/intermediate.crt` and `intermediate.key` (P-256 key, subject CN `Caddy Local Authority - ECC Intermediate`, issuer = the root's subject, `BasicConstraints(ca=True, path_length=0)`, key usage cert-sign and CRL-sign, `not_valid_before = now - 14 days`, `not_valid_after = now - 7 days`, signed by the root key with SHA-256), temp file + `os.replace`, owner 10001:10001, mode 0600; asserts `not_valid_after < now` and prints only `intermediate expired: <ISO date>`.
- Extends the CI job `restore` of B1 Task 13 (after its step 7b), `timeout-minutes: 45`:
  9. `./brievie restore-test` → exit 0 and stdout contains `Hersteltest geslaagd`; then the `pg_database` count of `brievie\_restoretest\_%` (as in Task 6) is `0`; `./brievie status` contains `Laatste hersteltest:` and no `Let op` line about the restore test.
  10. Case 2 (D 4.7): `$COMPOSE stop caddy` (so `caddy-publish-root.sh` cannot overwrite the copy); run `expire-intermediate.py`; `./brievie backup` (the keeper needs no Caddy); `$COMPOSE down --volumes`; `rm deploy/.env`; no `${COMPOSE_PROJECT_NAME}_` volume left.
  11. Restore again exactly as step 6 (`BRIEVIE_RESTORE_SOURCE`, `BRIEVIE_RESTORE_MASTER_KEY`, `--keep-hostname`).
  12. For both entries of `seed.json`: `curl --fail --cacert "$RUNNER_TEMP/old-root.pem" -H "Authorization: Bearer $TOKEN" …/pages/1 | sha256sum` equals the seeded hash (the device still signs in over a chain that validates against the old root, so Caddy serves a valid intermediate); and the served intermediate (second certificate of the chain) is not expired: `echo | openssl s_client -connect localhost:443 -servername localhost -showcerts 2>/dev/null | awk '/BEGIN CERT/{n++} n==2{print} /END CERT/ && n==2{exit}' | openssl x509 -noout -checkend 0` exits 0.
  13. Failure: `$COMPOSE ps --all`; always `$COMPOSE down --volumes` (unchanged).
- `docs/SELFHOST.md`, section "What B2 adds" becomes real sections: Schedule (defaults, the two variables, catch-up, a 15-minute tick, time zone); Retention (the three variables, restic durations, an invalid value prunes nothing and warns); The restore test (`./brievie restore-test`, what it does and never touches, free space ≈ the files plus three times the database dump, run it from the NAS task scheduler or cron, e.g. monthly; the status warns after 90 days); a back-up older than a week still pairs nothing anew (the root is restored, Caddy makes a new intermediate).
- `docs/GLOSSARY.md`: hersteltest = restore test, schema = schedule, bewaartermijn = retention.
- `docs/DECISIONS.md` (B2 section) lines 4–6: the restore test's isolation (scratch names generated and guarded, `/var/tmp` in the one-off container, no `backup_lock`, restic's shared lock, prune waits a night); root-only authority restore; the expired-intermediate CI case uses an intermediate forged from the real root rather than a moved clock (Plan decision 7).

- [ ] **Step 1: Failing test**

```python
def test_only_the_root_of_the_authority_is_placed(tmp_path, fake_restic)
    # restored copy holds root.crt, root.key, intermediate.crt, intermediate.key; the target held an old intermediate.crt
    # -> target contains exactly {"root.crt", "root.key"}, 0600, dir 0700; certificates/local removed
```
  (update B1's `test_files_are_restored_and_the_authority_placed` parametrisation to the two names.)
- [ ] **Step 2:** `cd backend && uv run pytest tests/test_backup_restore.py -q` → FAIL.
- [ ] **Step 3:** Implement the `_place_authority` change, the script, the CI steps and the docs.
- [ ] **Step 4:** `make test lint typecheck` → PASS. Run the new CI steps by hand in the build project `brievie-b1` (Docker Desktop, `https://localhost:8443`, project-name check before every `down --volumes`), in order, after B1 Task 13's steps; record both hashes of step 12 and the printed expiry date of step 10 in the task report. Do not push unless the owner says so.
- [ ] **Step 5:** Commit `ci: restore test and a back-up with an expired intermediate` and `docs: schedule, retention and the restore test`.

---

## Plan decisions

1. **A fixed 15-minute tick plus an "is it due" check, not `@app.periodic(cron=settings…)`**: every worker defers every periodic task with *its own* environment (the pipeline worker has no back-up settings and would defer at the default time), an invalid cron at import would crash every process that autodiscovers `jobs.py`, and the due check catches up once after the server was off.
2. **Due = no run of that kind open, created or finished since the last scheduled moment**: a manual back-up around 03:00 or a failed nightly run never causes a second one before the next moment; a failure is not retried every 15 minutes.
3. **No destination: the tick does nothing**; a broken destination setting still requests a run so the failure is visible once per moment; `status` carries `not_configured`.
4. **An invalid schedule falls back to the default and warns; an invalid retention backs up, never prunes and fails the run**: both fail in the safe direction (more back-ups, more kept), and both are loud; the run fails like B1's `files_incomplete` (made, but something is wrong) so it is fixed rather than ignored.
5. **`forget_argv` re-validates its retention**: the keep-nothing refusal lives in the function that builds the destructive argv, not only in its caller (hard rule 3).
6. **Restore test without `backup_lock`, on restic's shared lock**: a restore test can take hours and must not cancel a nightly back-up; restic's lock keeps prune from deleting data being read, and a prune that meets it is skipped until the next night (back-up still succeeded).
7. **Expired-intermediate CI case by forging an expired intermediate from the real root, not by moving the clock**: containers share the host kernel's clock, Caddy is a Go binary that ignores `libfaketime`, and moving the runner's clock disturbs Docker and NTP; forging from the real root keeps the phone's trust anchor, which is what the case must prove.
8. **Restore places the root only**: an intermediate in a back-up is expired after a week (Caddy's default lifetime); Caddy makes a new one from the restored root at start. This makes case 2 pass by construction instead of depending on when Caddy's maintenance renews it.
9. **Scratch space in `/var/tmp` of the one-off container, the scratch database by `CREATE DATABASE` beside the live one**: not a volume of the installation, removed with `--rm` even if the test is killed; the free-space check reads the container's disk (Docker's disk, where the PostgreSQL volume also lives by default) and asks for the files plus three times the dump plus 256 MiB.
10. **Reuse, not copy**: `restore_database(dbname=…)` and `verify_restore(conninfo=…, storage_root=…)` are B1's, unchanged; the only new restore primitive is `restore_files_into`, which cannot empty, place an authority or write outside a guarded scratch directory.
11. **Leftovers are cleaned at the start of the next test, under its own advisory lock**: SIGTERM runs the `finally`, but a power cut does not; the lock proves no other test owns them.
12. **`requested_by="schedule"` and kind `restore_test` on the existing `BackupRun`**: B3's card reads one table; the restore test's run is written by the admin container through the app role, like `backup_init`.
13. **Out to B3**: the web card and its warnings (B2 makes the warning codes), the API, "Nu back-up maken", the recovery-sheet confirmation, and the restore drill in an audit log (D 4.9).

## Assumptions about B1 code that was still being written

- `./brievie` does not exist yet in the worktree; Task 6 assumes B1 Task 12's shape: `COMPOSE` resolved from the script's directory, a bring-along step every command runs, a running-services check through `$COMPOSE ps --status running --services`, Dutch messages on stderr, and the fake-docker harness in `test_brievie_wrapper.py` (`installed`, `FAKE_DOCKER_LOG`, `FAKE_DOCKER_RUNNING`, `FAKE_DOCKER_FAIL`, `FAKE_DOCKER_FAIL_CODE`), which exists already.
- `safepaths.py` is as found: `require_emptiable(directory, *, allowed)`, `require_inside`, private `_through_link`, refusals as `BackupError("unsafe_path")` with the sentence the tests pin; B1 adds `unsafe_path` to `REASONS`. B1 may move `restore._emptiable` onto it; Task 7's `_place_authority` change applies on top of whatever B1 leaves.
- The CI job `restore` is not in `ci.yml` yet; Task 7 extends B1 Task 13's steps 1–8 and its files (`$RUNNER_TEMP/seed.json`, `old-root.pem`, `master-key`, `BRIEVIE_RESTORE_SOURCE`, `BRIEVIE_RESTORE_MASTER_KEY`, `--keep-hostname`, `https://localhost`).
- Signatures used as read on 2026-11-17: `restore.find_run(repository, password, run, *, extra_secrets=())`, `restore._restic_output(repository, password, argv, extra_secrets=())`, `restore.restore_database(*, run, dbname="brievie") -> int` (takes `database_lock` on the given database and closes open runs there), `verify.verify_restore(*, conninfo, storage_root, manifest, master_key, authority_dir)`, `services.file_paths()`, `services._IncompleteBackupError`, `jobs.run_backup(*, run_id, clean_up_run_ids=None)`, `backup_support.fake_restic/fake_tool/configured_folder/owner_conninfo`.
- The admin container runs as root with `BRIEVIE_MASTER_KEY`, `BRIEVIE_BACKUP_PASSWORD` and `DATABASE_SUPERUSER_URL` (database `brievie`); PostgreSQL's maintenance database `postgres` exists; the test owner role has `CREATEDB` (conftest says it creates the test database).
- restic 0.18 takes a shared lock for `snapshots`, `dump`, `stats` and `restore`, and an exclusive one for `forget --prune` (exit 11 when it cannot); not verified for `check`, so a weekly check that meets a restore test simply fails visibly with "vergrendeld".
- Caddy, on start with a root but no intermediate in `pki/authorities/local`, generates a new intermediate from the root; Task 7's CI case is the proof.
