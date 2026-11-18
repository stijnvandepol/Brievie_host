# Host U1: versies en bijwerken Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Changes in part B1 that this plan must be re-checked against before it is executed

Part B1 was built on `feat/b1-backup` after this plan was written, and many things changed by ruling. The design (`docs/superpowers/specs/2026-11-17-beheer-backup-export-updates-design.md`, sections 4, 7.3 and 14) now says what was built. Re-check every task below against it, at least these:

- **The emergency back-up is B1's safety back-up.** `./brievie backup --safety` makes a run tagged `kind:safety` on both snapshots (`requested_by="restore"`), applies no retention in its own run, is kept by ordinary retention and removed by run tag 30 days from now, and prints its run id (`safety_run_id=` with `BRIEVIE_WRAPPER=1`, "Veiligheidsback-up: <id>" for the user). Task 4's `run_now(label=...)`, `label:` tag, `BackupRun.label` and `backup_now` must be re-decided against it: reuse `--safety` and add the label to it, or justify a second kind of run. Without the tag, ordinary retention would remove the way back after two to seven days.
- **`backup --safety` goes through the running keeper** (`require_keeper`, `compose exec keeper`), and a restore stops api, worker and Caddy first, then makes the safety back-up, then stops the keeper. Step 3 and 4 of the update (stop everything including the keeper, then a one-off `run_now` as uid 10001) must follow that order or change B1 deliberately.
- **A safety back-up needs a configured destination** (`not_configured` otherwise), and the destination rules are strict: a folder needs its marker and `--init`, `destination_kind` refuses a folder and a repository together, `configured_repository` never creates anything. The fallback repository in the volume `update-backup` (Task 4) is new to B1's rules: it must pass design 4.10 (the volume joins the fixed allow-list in code, nothing prunes it but the update, the keeper never mounts it) and must not count as a back-up in `status`.
- **The way back by name**: B1's `restore` accepts only a run number (`valid_run_id`, a UUID) and `find_run` takes a UUID. `./brievie restore pre-update-<version>` needs a label lookup in the wrapper and in `find_run`; the update must print the run number in any case. A restore source may only be a volume of the project that compose declares and that holds none of the installation's own data (`source_volume`); decide how `update-backup` qualifies.
- **The wrapper's rules apply to `update`** (design 7.3 and 4.10): `setup_installation` fixes the project and image once; `claim_installation` and `check_installation_id` prove ownership; `take_lock` (`deploy/.brievie-lock`, `BRIEVIE_LOCK_HELD`) allows one `./brievie` at a time, which the hand-over to the target checkout's wrapper (`_update-finish`) must carry; `refuse_while_halfway` blocks `start`, `backup`, `sweep` and `status` while `deploy/.restore-in-progress` exists and `update` must too, while a restore must refuse when `deploy/.update/in-progress` exists; the exit trap and the unique temporary names (`.$$`) of `edit_env`/`rewrite_env`; no confirmation from piped input (a typed word before stopping the server and switching the checkout); writes only fixed names under `${here:?}`.
- **Image and project naming**: B1's `setup_installation` takes the image from `BRIEVIE_IMAGE` or `deploy/.env`, defaults to `brievie/backend:<project>` and refuses `brievie/backend:local` outside the project `brievie`. Task 6's `image: ${BRIEVIE_IMAGE:-brievie/backend:${BRIEVIE_VERSION:-local}}` and "refuses a custom image" must be reconciled with it.
- **`.env` handling**: `start` pins `BRIEVIE_VERSION` with `edit_env` (key spellings, quoting), not a fixed `.env.update-tmp`. A restore never takes `BRIEVIE_IMAGE`, `COMPOSE_PROJECT_NAME`, the host ports, `COMPOSE_*`, `DOCKER_*` or the installation id from a back-up (`HOST_KEYS`, `pin_restored_identity`, `keep_current`); `BRIEVIE_VERSION` is not in that list, so decide what a restore does with the back-up's value (the plan's decision 4 says it is restored with `.env`). A restore refuses a back-up whose manifest lists migrations the code does not know (`newer_backup`); with real versions that rule and the version in the manifest should agree.
- **`_back_up` and the restic host changed** (counts from an exported snapshot, one run time via `--time`, `_require_env_copy`, `_IncompleteBackupError`, host `brievie-<installation id>`, `forget_run` for failed runs). `run_now` must reuse these, not copy them; the label tag goes through the same `tags()`/`_run_marks()` builders and keeps the tests that no secret is in any argv.
- **The admin container and the guards**: `admin` has the profile `admin`, a default command of `help`, runs as root unless `--user 10001` is given, and destructive commands need `BRIEVIE_IN_CONTAINER=1`; a new volume mounted there joins the fixed allow-list of design 4.10 in the same commit.
- **Restore-in-progress and update-in-progress** share the wrapper's `report_halfway` idea: an update that stops halfway must say where, as B1's restore does, and the `step` values in `deploy/.update/in-progress` need the same promise to later wrappers that the plan already makes for its keys.
- **Keeper timing**: the keeper has `stop_grace_period: 1m`; the periodic stalled-job retry and the restored-queue settling (a restore turns the dump's own `doing` back-up job into `failed`) matter when the update restarts the keeper after a restore back to the old version.
- **CI and docs**: the job `restore` exists in `ci.yml` and had not run on a runner at B1's final commit; the upgrade proof of Task 9 should not assume it is green. `docs/SELFHOST.md` and `docs/DECISIONS.md` ("Back-up and restore (B1)") already hold the B1 text the new "Updating" section must fit.

**Goal:** The server has one version number (0.2.0, in `backend/pyproject.toml`) that the image, the health endpoint, the back-up manifest and the web all learn from; every release carries `CHANGELOG.md` and `deploy/release.json`; the health endpoint reports `server_version` and really checks the database; and `./brievie update [version]` moves an installation that still builds locally from git to a tagged version in the binding order of D 7.3, backing up first and printing the way back at every failure.

**Architecture:** Decisions are pure Python in a new app `apps.updates` (`release.py`: versions, `release.json`, "allowed / refused, go to X first", space) reached through management commands that run in the **installed** image, so the shell stays thin. The pre-update back-up reuses B1's `_back_up` through a new synchronous `services.run_now(label=…)` in the one-off `admin` container (the keeper is stopped by then), tags both snapshots `label:pre-update-<version>`, and without a configured destination writes to a fallback repository in a named volume that `status` does not count. `./brievie update` runs steps 1-4 and the checkout from a copy of the installed wrapper, then hands over to the **target's** wrapper (`./brievie _update-finish`) for migrate, start, health and record; a marker file `deploy/.update/in-progress` carries the state between them and makes an interrupted update resumable. The image tag follows the version: `brievie/backend:${BRIEVIE_VERSION}`, with `BRIEVIE_VERSION` in `deploy/.env` as the pin.

**Tech Stack:** As in B1/B2 (Python 3.13, Django 6.1, django-ninja, PostgreSQL 18, Procrastinate, restic 0.18.1, POSIX `sh`, git, Docker Compose v2). No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-17-beheer-backup-export-updates-design.md` (cited as "D 7.3"); U1 is its row in D 8. Sections 7.2, 7.3, 7.6, 9 ("an update interrupted after the migration; an update across two releases; a refused jump"), 11 and 13 are binding. House style and the wrapper's rules: `docs/superpowers/plans/2026-11-17-host-b1-backup-herstel.md` (Task 12) and `…-host-b2-schema-hersteltest.md`.

**Not in U1:** published images, multi-arch builds, the release workflow, compose on registry images, the installer on a release (U2); the update notice, contract fixtures per release, the app's oldest server, the refusal code, the CI upgrade test (U3); `--from-archive`, append-only destinations (U4); crossing a PostgreSQL major (refused in U1 with a sentence; the dump-and-load path comes later, D 7.6). The web shows no version in U1; it only gets the typed field.

## Global Constraints

- From `CLAUDE.md`: "Code, comments, commits, table and field names in English. User-facing text in Dutch (B1)", in the backend through `gettext`. `mypy --strict` and `ruff check` clean, no `Any`, no bare `except`. Every table: UUID `id`, `created_at`, `updated_at` (`apps.common.models.BaseModel`); `ServerUpdate` is server-level, **not** a `WorkspaceModel` (`tests/test_rls_coverage.py` passes unchanged). Datetimes timezone-aware.
- "API only under `/api/v1`, additive changes only." "A field added to an existing API schema has a default (never newly required; the contract test compares with `backend/tests/fixtures/openapi_1b.json`)." "Every non-2xx body: `{"code": "<snake_case>", "message": "<Dutch sentence>"}` via `apps.common.errors.ApiError`." "Thin endpoints: validate, call one function in the app's `services.py`, return." The same for management commands: parse, call one function, print.
- "Every mutating service function is wrapped in `transaction.atomic`." Exception as in B1: `run_now` changes the run's status in short transactions; none is open while restic or pg_dump run.
- "No document content, tokens, passwords or BSNs in logs." Nor the master key, the back-up password or a repository address in any log line, reason or message (B1's `restic.redact`).
- "No new dependency without a line in `docs/DECISIONS.md`." U1 adds none.
- From D 11, not left to the builder: **the order of D 7.3** (check → fetch → stop api, worker, keeper → back up → migrate, start, check health → record), that the way back is `./brievie restore pre-update-<version>` and is printed by the failure message, that nothing starts an update automatically, and that migrations are never reversed.
- The builder **never creates a git tag, a GitHub release or a package, and never pushes**. Tests that need tags create them in temporary repositories under `tmp_path`; the end-to-end proof (Task 9) creates them only in a throwaway clone inside the build worktree.
- Branch `feat/u1-updates`, cut from `feat/b1-backup` **after B1 Task 12 (`./brievie`) and B2 are committed there** (U1 extends B1's wrapper and comes after B2's migration `0002`). Build worktree: Docker project `brievie-u1`, its own `deploy/.env` with `COMPOSE_PROJECT_NAME=brievie-u1`, `POSTGRES_PORT=5434`, `BRIEVIE_HTTP_PORT=8481`, `BRIEVIE_HTTPS_PORT=8444`, `BRIEVIE_IMAGE=brievie/backend:u1`. Before any command that removes containers or volumes, run `docker compose --project-directory deploy -f deploy/docker-compose.yml config --format json | grep '"name"'` and stop unless it prints `brievie-u1` (Task 9: `brievie-u1e2e`).
- Every commit: conventional message, ending with the trailer line `Co-Authored-By: Claude Code <noreply@anthropic.com>`. Never commit `deploy/.env`, `.superpowers/`, `.e2e/`, or iCloud conflict copies (" 2", " 3").
- Copied verbatim from the B1 standing context (`.superpowers/sdd/2026-11-17-host-b1-backup-herstel/context.md`):
  - "This machine also runs the owner's test server as Docker project `brievie` (containers brievie-*-1, ports 80/443/5432). NEVER run a docker or compose command against it."
  - "No secret is ever a command-line argument of a process you start or script: not the master key, the back-up (restic) password, a database password (use PGPASSWORD / a conninfo passed in the environment or on stdin, never a URL or `-v name=value` in argv), nor storage credentials. Pass them through the child's environment or stdin. `docker run`/`compose run` get secrets as `-e NAME` (value from the caller's environment), never `-e NAME=value`. Every task that starts a process includes a test that intercepts the started command (fake binary on PATH or a patched subprocess call) and asserts the secret values appear in no argv."
  - "HARD RULES after the incident of 17-11 (a test ran an unguarded "empty this directory" on `/` of the owner's Mac). These override anything else in a brief.
    1. Code that deletes, moves, empties or overwrites files or directories is NEVER executed on the Mac itself outside the test's own temporary directory (`tmp_path`) or inside a container. No absolute path in a test for such code except under `tmp_path`.
    2. A test for a refusal ("refuse `/`", "refuse the home directory", "refuse a relative path") never calls the dangerous function with the real path. Build the guard as a PURE function that only judges a path, test that function, and only then put the destructive step behind it. Where a test must go through the caller, replace the destructive primitive (`os.scandir`, `os.unlink`, `os.rmdir`, `shutil.rmtree`, `os.replace`, `Path.unlink`) by a recorder that fails the test when called.
    3. Every destructive function carries its own refusal, independent of its caller, and that refusal exists BEFORE any test touches the function: not `/`, not the user's home directory, not a path at or above a volume's mount point other than the configured one, not a path outside the configured storage root, never following a symbolic link.
    4. Proofs against the real programs (restic, pg_dump, pg_restore, sweeping, restoring, `./brievie`) run ONLY inside containers of the build project `brievie-b1` with throwaway volumes or throwaway databases. Never bind-mount a directory of the Mac into a container other than the build worktree itself (read-only where possible); never the scratch directory, never a home directory.
    5. Shell: no `rm -r`, `rm -f`, `mv` or `>` redirect on a path built from a variable unless the script has just proved the variable is non-empty and the path lies under the script's own directory (`${var:?}` and an explicit prefix check). Prefer fixed relative names.
    6. If you are unsure whether a command could delete or overwrite something outside `tmp_path` or a container: do not run it; report instead."
  - For U1, "the build project `brievie-b1`" in rule 4 reads `brievie-u1` (and `brievie-u1e2e` in Task 9); everything else stands as written.
- The wrapper's own writes use fixed relative names only: `deploy/.env` (rewritten through `deploy/.env.update-tmp` + `mv`), `deploy/.update/in-progress` (through `deploy/.update/in-progress.tmp` + `mv`), `deploy/.update/brievie-running`. The wrapper removes nothing but `deploy/.update/in-progress` (`rm -f` on that fixed name).

## Review Focus

1. **An update interrupted after "stop"** (Ctrl-C, a closed terminal, a power cut, a migration that dies half-way): the next `./brievie update` (or `./brievie start`) must not say "Je hebt al versie X" or start a half-migrated server; it says an update is unfinished and gives both ways (finish it, or the way back). → Task 8 (`test_unfinished_update_is_resumed`, `test_start_refuses_while_an_update_is_unfinished`), Task 7 (`test_ctrl_c_during_the_backup_restarts_the_old_version`).
2. **A version typed wrongly** (`latest`, `0.3`, `v0.3.0-rc.1`, `0.3.0;rm -rf x`, `../x`): refused before any git or docker call uses it. → Task 7 (`test_update_refuses_a_malformed_version`).
3. **`git fetch` fails or would move a tag** (no network, origin gone, a tag replaced upstream): refused, nothing changed, no stale guess at "the newest version". → Task 7 (`test_update_refuses_when_fetch_fails`, `test_update_refuses_a_moved_tag`).
4. **The checkout collides** (an untracked file the target tracks, a file locked by an editor): the server comes back on the old version, HEAD unchanged, "niets bijgewerkt". → Task 8 (`test_checkout_failure_restarts_the_old_version`).
5. **The database is down while the API runs**: `/api/v1/health` answers 503 `database_unavailable` with a Dutch sentence, never a 500, a traceback, or the database's host name; the compose healthcheck turns unhealthy, so `up --wait` in an update fails at "health". → Task 2 (`test_health_is_503_when_the_database_is_unreachable`, `test_health_skips_the_request_transaction`).

Also pinned next to the code that owns it: a dirty tree or local commits (Task 7), a refused jump naming the version to go to first and a PostgreSQL major change (Task 3), the fallback back-up not counting in `status` (Task 4), a back-up found by label (Task 4).

## File Structure

```
CHANGELOG.md                               # Create: Keep a Changelog, Dutch headings, "Voor je bijwerkt"
brievie                                    # Modify: update, _update-finish; start pins BRIEVIE_VERSION; restore by label; status
.gitignore                                 # + deploy/.update/, deploy/.env.update-tmp, /.e2e/
deploy/
  release.json                             # Create: {"format": 1, "version", "min_upgrade_from", "postgres_major"}
  docker-compose.yml                       # image tag from BRIEVIE_VERSION; worker stop_grace_period; update-backup volume (admin only)
  Dockerfile                               # /update-backup owned by 10001
.github/workflows/ci.yml                   # stack: health reports the pyproject version
backend/
  pyproject.toml  uv.lock                  # version 0.2.0
  brievie/settings.py                      # INSTALLED_APPS + apps.updates; BRIEVIE_UPDATE_BACKUP_MOUNT
  brievie/api.py  brievie/urls.py          # health: server_version, 503, non-atomic
  apps/common/version.py                   # Create: server_version()
  apps/common/health.py                    # Create: database_reachable(), mark_non_atomic()
  apps/common/schemas.py                   # HealthOut.server_version = ""
  apps/backup/                             # label on runs and snapshots, run_now, fallback repository, find_run by label
    models.py migrations/0003_backuprun_label.py restic.py destination.py services.py restore.py errors.py manifest.py
    management/commands/backup_now.py      # Create
  apps/updates/                            # Create: new app
    apps.py models.py migrations/0001_initial.py
    release.py                             # pure: Version, Release, parse_release(_stream), decide, space_needed, REASONS
    services.py                            # record_update, last_update, preflight, health_verdict
    management/commands/ update_plan.py update_preflight.py check_health.py record_update.py update_status.py
  tests/
    test_version.py  test_health.py  test_openapi_contract.py  test_deploy_files.py
    test_updates_release.py  test_updates_commands.py  test_backup_update_backup.py
    wrapper_support.py                     # Create (or extend B1's): temp origin + install repos, fake docker
    test_brievie_update.py                 # Create
docs/  DECISIONS.md  SELFHOST.md  GLOSSARY.md  api/openapi.json
README.md  SECURITY.md
web/src/api/schema.d.ts                    # regenerated
```

`apps.updates` imports `apps.common` and `apps.backup.database` (the back-up role's conninfo for the database size); `apps.backup` does not import `apps.updates`.

---

### Task 1: One version source, the change log and `release.json`

**Files:**
- Create: `backend/apps/common/version.py`, `CHANGELOG.md`, `deploy/release.json`
- Modify: `backend/pyproject.toml` (`version = "0.2.0"`), `backend/uv.lock` (the `brievie` package's version line only), `backend/apps/backup/manifest.py` (`brievie_version()` delegates), `docs/DECISIONS.md`
- Test: `backend/tests/test_version.py`

**Interfaces:**
- Produces: `apps.common.version.server_version() -> str` (`functools.cache`; `tomllib` on `Path(settings.BASE_DIR) / "pyproject.toml"`, `["project"]["version"]`). The image already copies `backend/` to `/app`, so the running image knows it without a build argument.
- Produces: `apps.backup.manifest.brievie_version() -> str` returns `server_version()` (kept as a name: B1 callers stay).
- Produces: `deploy/release.json`, exactly:
  ```json
  {"format": 1, "version": "0.2.0", "min_upgrade_from": "0.2.0", "postgres_major": 18}
  ```
  `min_upgrade_from` = the oldest installed version from which `./brievie update` may go straight to this one; equal to `version` here because nothing older has the updater (Plan decision 3).
- Produces: `CHANGELOG.md`: title, one line on the form (Keep a Changelog, semantic versions), optional `## [Onuitgebracht]`, then `## [0.2.0] - 2026-11-17` with `### Voor je bijwerkt` (the one-time manual path from an installation before versions: `git fetch --tags`, `git checkout v0.2.0`, `./brievie start`), `### Toegevoegd` (back-up and restore, the recovery sheet, versions, `./brievie update`). Headings in Dutch: `Voor je bijwerkt`, `Toegevoegd`, `Gewijzigd`, `Opgelost`, `Verwijderd`, `Beveiliging`.

- [ ] **Step 1: Write the failing tests** (`test_version.py`; `ROOT = Path(__file__).resolve().parents[2]`)

```python
def test_server_version_reads_pyproject():
    assert server_version() == tomllib.loads((ROOT / "backend/pyproject.toml").read_text())["project"]["version"]
def test_version_is_semantic():                        # re.fullmatch(r"\d+\.\d+\.\d+", server_version())
def test_changelog_top_entry_is_the_version():
    headings = re.findall(r"^## \[([^\]]+)\]", (ROOT / "CHANGELOG.md").read_text(), re.M)
    released = [h for h in headings if h != "Onuitgebracht"]
    assert released[0] == server_version()
def test_changelog_entries_have_a_date():              # every "## [x.y.z]" line matches r"^## \[\d+\.\d+\.\d+\] - \d{4}-\d{2}-\d{2}$"
def test_release_json_matches_the_version():           # json.loads(deploy/release.json) == {"format": 1, "version": server_version(), ...}; parse_release in Task 3 re-checks the shape
def test_release_json_postgres_major_matches_compose(): # == the single major of r"(?<!@)\bpostgres:(\d+)" in docker-compose.yml
def test_web_package_is_not_a_version_source():        # json.loads(web/package.json)["version"] == "0.0.0"
def test_manifest_records_the_server_version():        # manifest.brievie_version() == server_version()
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_version.py -q` → FAIL (no module `apps.common.version`, no `CHANGELOG.md`).
- [ ] **Step 3: Implement** the interfaces; set `version = "0.2.0"`, run `cd backend && uv lock` and check `git diff backend/uv.lock` changes only the `brievie` version line (anything else: stop and report). `DECISIONS.md`, new section "2026-11-17: Versions and updates (U1)", line 1: the version lives only in `backend/pyproject.toml`; the image, health, manifest and change log follow it; `web/package.json` stays `0.0.0` and is not a version; the API's `api_version` (1.0.0) is the contract's version and moves separately; the first release is 0.2.0 because every installation from before versions reports 0.1.0.
- [ ] **Step 4: Run** `make test lint typecheck` → PASS.
- [ ] **Step 5: Commit** `feat: one version source, a change log and release.json`

---

### Task 2: `server_version` and a health check that checks the database

**Files:**
- Create: `backend/apps/common/health.py`
- Modify: `backend/apps/common/schemas.py`, `backend/brievie/api.py`, `backend/brievie/urls.py`, `backend/tests/test_openapi_contract.py` (`NO_ERROR_RESPONSES = {"getSetupStatus"}`), `docs/api/openapi.json` (`make openapi`), `web/src/api/schema.d.ts` (`pnpm gen:api`), `.github/workflows/ci.yml` (job `stack`)
- Test: `backend/tests/test_health.py`, `backend/tests/test_deploy_files.py`

**Interfaces:**
- Consumes: `server_version()` (Task 1).
- Produces: `HealthOut.server_version: str = ""` (additive, with a default: not in `required`).
- Produces: `apps.common.health.database_reachable() -> bool`: `SELECT 1` on `django.db.connection` (the app role); `DatabaseError` → logs `"Health: database unreachable (%s)"` with the exception's type name only, returns False.
- Produces: `apps.common.health.mark_non_atomic(urls: tuple[list[URLPattern | URLResolver], str, str], route: str) -> tuple[...]`: sets `callback._non_atomic_requests = {DEFAULT_DB_ALIAS}` on the pattern whose route equals `route`; raises `LookupError` when there is none. Needed because `ATOMIC_REQUESTS = True` opens the request transaction (and the connection) before the view runs, which would turn an unreachable database into Django's 500. `urls.py`: `path("api/v1/", mark_non_atomic(api.urls, "health"))`.
- Produces: `getHealth` `response={200: HealthOut, 503: ErrorOut}`; 503 is `ApiError(503, "database_unavailable", _("De database is niet bereikbaar. Probeer het later opnieuw."))`. Nothing else in the body: no host, no exception text.

- [ ] **Step 1: Write the failing tests**

```python
def test_health_returns_status_and_versions(client):
    assert client.get("/api/v1/health").json() == {"status": "ok", "api_version": "1.0.0", "server_version": server_version()}
def test_health_is_503_when_the_database_is_unreachable(client, monkeypatch):
    monkeypatch.setattr("apps.common.health.database_reachable", lambda: False)
    r = client.get("/api/v1/health")
    assert r.status_code == 503 and r.json()["code"] == "database_unavailable" and set(r.json()) == {"code", "message"}
def test_database_reachable_is_false_on_a_database_error(monkeypatch, caplog):  # connection.cursor patched to raise OperationalError("host=db.secret …"); False; "db.secret" not in caplog.text
def test_health_skips_the_request_transaction():
    assert resolve("/api/v1/health").func._non_atomic_requests == {"default"}
    assert not hasattr(resolve("/api/v1/workspaces").func, "_non_atomic_requests")
def test_mark_non_atomic_refuses_an_unknown_route():   # LookupError
# test_openapi_contract.py
def test_health_documents_503_and_server_version_is_optional():  # "503" in getHealth responses; "server_version" not in HealthOut.required
# test_deploy_files.py
def test_api_healthcheck_calls_the_health_endpoint():  # "/api/v1/health" in service("api") healthcheck (urlopen raises on 503, so the check fails when the database does)
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_health.py tests/test_openapi_contract.py tests/test_deploy_files.py -q` → FAIL.
- [ ] **Step 3: Implement**; the view calls `database_reachable()` and builds `HealthOut`, nothing else. Then `make openapi`, `cd web && pnpm gen:api`. CI job `stack`, after "The API answers through Caddy": step "The health endpoint reports the version": `curl … /api/v1/health | python3 -c 'import json,sys,tomllib; assert json.load(sys.stdin)["server_version"] == tomllib.load(open("backend/pyproject.toml","rb"))["project"]["version"]'`.
- [ ] **Step 4: Run** `make test lint typecheck` and `cd web && pnpm typecheck && pnpm test` → PASS; `git diff --stat docs/api web/src/api` shows only the health changes.
- [ ] **Step 5: Commit** `feat(api): health reports the server version and checks the database`

---

### Task 3: The release rules (pure) and `update_plan`

**Files:**
- Create: `backend/apps/updates/{__init__,apps}.py`, `backend/apps/updates/release.py`, `backend/apps/updates/management/commands/update_plan.py`
- Modify: `backend/brievie/settings.py` (`"apps.updates"` in `INSTALLED_APPS`)
- Test: `backend/tests/test_updates_release.py`, `backend/tests/test_updates_commands.py`

**Interfaces:**
- Produces in `apps.updates.release` (no Django import except `gettext_lazy` for `REASONS`):
  - `@dataclass(frozen=True, order=True) class Version: major: int; minor: int; patch: int` with `@classmethod parse(cls, text: str) -> "Version"` (accepts `X.Y.Z` or `vX.Y.Z`, each part `0|[1-9]\d{0,3}`, nothing else; else `ReleaseError("version_invalid")`) and `__str__` → `"X.Y.Z"`.
  - `PRE_VERSIONS = Version(0, 1, 0)`; `RELEASE_FORMAT = 1`; `BUILD_RESERVE_BYTES = 4 * 1024**3`.
  - `class ReleaseError(Exception)` with `.code: str` (like `BackupError`); `REASONS: dict[str, Promise]`.
  - `@dataclass(frozen=True) class Release: version: Version; min_upgrade_from: Version; postgres_major: int`.
  - `parse_release(text: str) -> Release`: a JSON object with exactly the keys `format`, `version`, `min_upgrade_from`, `postgres_major`; `format` an `int` (not bool) equal to 1, else `release_format_unknown`; `postgres_major` an int 10-99; `min_upgrade_from <= version`; anything else `release_invalid`.
  - `ReleaseEntry = Release | ReleaseError`; `parse_release_stream(text: str) -> dict[Version, ReleaseEntry]`: records separated by header lines `--- vX.Y.Z` (what the wrapper prints per tag, then `git show vX.Y.Z:deploy/release.json`); a record whose release's version differs from its header becomes `ReleaseError("release_mismatch")`; a malformed header line is skipped.
  - `@dataclass(frozen=True) class Decision: outcome: Literal["allowed", "already", "refused"]; code: str = ""; go_first: Version | None = None`.
  - `decide(*, installed: Version, target: Version, releases: Mapping[Version, ReleaseEntry], postgres_major: int) -> Decision`, rules in this order: `installed <= PRE_VERSIONS` → refused `before_versions`; `target == installed` → already; `target < installed` → refused `downgrade`; target not in `releases` → refused `release_missing`; its entry a `ReleaseError` → refused with that code; `postgres_major` differs → refused `postgres_major_changed`; `min_upgrade_from <= installed` → allowed; else the highest `Release` R with `R.min_upgrade_from <= installed < R.version < target` and the same `postgres_major` → refused `go_first`, `go_first=R.version`; else refused `no_path`.
  - `space_needed(*, documents_bytes: int, database_bytes: int, fallback: bool) -> int` = `BUILD_RESERVE_BYTES + (documents_bytes + database_bytes if fallback else 0)`.
  - `refusal_message(decision: Decision, *, installed: Version, target: Version, postgres_major: int) -> str` (Dutch, B1). Pinned sentences: `before_versions`: "Deze installatie is van vóór de versienummers. Werk één keer met de hand bij: git fetch --tags, git checkout v{target}, ./brievie start. Daarna werkt ./brievie update."; `go_first`: "Van versie {installed} kun je niet in één keer naar {target}. Werk eerst bij naar {go_first}: ./brievie update {go_first}"; `postgres_major_changed`: "Versie {target} gebruikt een andere hoofdversie van PostgreSQL dan deze server ({postgres_major}). Die overstap kan ./brievie update nog niet maken. Lees ‘Voor je bijwerkt’ in CHANGELOG.md."; `downgrade`: "Een oudere versie installeren kan niet. Terug ga je met een herstel van de back-up van vóór het bijwerken."; the others one sentence each, all ending in "Er is niets veranderd." where the wrapper prints them before step 3.
- Produces command `python manage.py update_plan --installed X.Y.Z --target X.Y.Z --postgres-major N` (stream on stdin): prints `allowed` or `already` (exit 0); a refusal → `CommandError(refusal_message(...), returncode=1)`; a malformed argument → returncode 2. It needs no database (runs under `docker run --network none`).

- [ ] **Step 1: Write the failing tests**

```python
R = lambda v, m, pg=18: Release(Version.parse(v), Version.parse(m), pg)
def test_version_order_is_numeric():                   # Version.parse("0.10.0") > Version.parse("0.9.9"); str(Version.parse("v1.2.3")) == "1.2.3"
@pytest.mark.parametrize("text", ["latest", "0.3", "v0.3.0-rc.1", "0.3.0;rm -rf x", "../x", " 0.3.0", "01.2.3", ""])
def test_malformed_versions_are_refused(text):         # ReleaseError code "version_invalid"
def test_parse_release_accepts_the_committed_file():   # parse_release((ROOT/"deploy/release.json").read_text()).version == Version.parse(server_version())
@pytest.mark.parametrize("data,code", [({"format": 2, ...}, "release_format_unknown"), ({"format": True, ...}, "release_format_unknown"),
    (missing key, "release_invalid"), (extra key, "release_invalid"), (min > version, "release_invalid"), (postgres_major "18", "release_invalid")])
def test_parse_release_refuses(data, code)
def test_stream_marks_a_mismatched_header():           # "--- v0.3.0\n{…"version": "0.3.1"…}" -> {Version(0,3,0): ReleaseError("release_mismatch")}
def test_direct_jump_is_allowed():                     # installed 0.2.0, target 0.3.0 (min 0.2.0) -> allowed
def test_same_version_is_already():
def test_downgrade_is_refused():
def test_before_versions_is_refused():                 # installed 0.1.0 -> refused before_versions, whatever the releases say
def test_refused_jump_names_the_furthest_step():       # releases 0.3.0(min 0.2.0), 0.3.1(min 0.2.0), 0.4.0(min 0.3.0); installed 0.2.0, target 0.4.0 -> go_first 0.3.1
def test_no_path_when_nothing_is_reachable():          # 0.5.0(min 0.4.0) only, installed 0.2.0 -> no_path
def test_postgres_major_change_is_refused():           # target postgres_major 19, server 18 -> postgres_major_changed (also when min allows it)
def test_missing_or_broken_target_release_is_refused():# not in releases -> release_missing; ReleaseError entry -> its code
def test_space_needed():                               # fallback adds documents + database; without it only the reserve
# test_updates_commands.py
def test_update_plan_prints_allowed(capsys)            # call_command with stdin stream
def test_update_plan_refusal_is_dutch_and_exit_1()     # CommandError.returncode == 1, "Werk eerst bij naar 0.3.1" in message
def test_update_plan_bad_argument_is_exit_2()
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_updates_release.py tests/test_updates_commands.py -q` → FAIL.
- [ ] **Step 3: Implement** the interfaces.
- [ ] **Step 4: Run** `make test lint typecheck` → PASS.
- [ ] **Step 5: Commit** `feat(updates): release rules and the update plan`

---

### Task 4: The pre-update back-up: label, fallback repository, `backup_now`, restore by label

**Files:**
- Create: `backend/apps/backup/migrations/0003_backuprun_label.py` (the next free number after B2), `backend/apps/backup/management/commands/backup_now.py`
- Modify: `backend/apps/backup/models.py`, `restic.py`, `destination.py`, `services.py`, `restore.py`, `errors.py`, `backend/brievie/settings.py` (`BRIEVIE_UPDATE_BACKUP_MOUNT: str = env.str(..., default="/update-backup")`)
- Test: `backend/tests/test_backup_update_backup.py`

**Interfaces:**
- Consumes (B1/B2): `services._back_up`, `services._finish`, `services._record_failure`, `destination.configured_repository()`, `destination.destination_kind()`, `destination._run_init`, `restic.forget_argv(...)`, `restore.find_run(repository, password, run, *, extra_secrets=())`, `BackupRun`, `backup_lock()`, B2's `apps.backup.safepaths` guards.
- Produces: `BackupRun.label = models.CharField(max_length=32, blank=True)`.
- Produces in `restic`: `LABEL_PATTERN = re.compile(r"pre-update-(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})")` (fullmatch); `check_label(label: str) -> None` (`BackupError("label_invalid")`); `tags(run_id, kind, label: str = "")` adds `--tag label:<label>` when set; `backup_stdin_argv(..., label: str = "")`, `backup_files_argv(..., label: str = "")` pass it on; `fallback_forget_argv() -> list[str]` = `["forget", "--prune", "--host", HOST, "--group-by", "host,paths", "--keep-last", "1"]` (keeps the newest db and files snapshot: the newest run).
- Produces in `destination`: `UPDATE_FALLBACK_KIND = "update"`; `check_fallback_mount(path: Path) -> Path` (pure; refuses relative, `/`, a home directory, any path through a symbolic link, and anything but exactly `settings.BRIEVIE_UPDATE_BACKUP_MOUNT`; `BackupError("update_backup_unavailable")`); `fallback_repository() -> restic.Repository`: behind that guard, writes the B1 marker layout (`brievie-backup.json` + `restic/`) when absent and runs `restic init` (tolerating "already exists"), so a restore can open it exactly like a folder destination.
- Produces in `services`: `run_now(*, label: str) -> BackupRun`: `check_label`; creates `BackupRun(kind=BACKUP, status=RUNNING, requested_by="update", label=label, destination_kind=<destination_kind() or UPDATE_FALLBACK_KIND>, started_at=now)`; then the same body as `perform_run` (B1 order: dump first, then files; manifest; never raises; failure recorded with its reason) through `_back_up(..., repository=<configured or fallback>, label=label, forget=<B2 retention argv or fallback_forget_argv()>)`. `_back_up` gains those three keyword parameters with defaults that keep B1/B2 behaviour. Holds `backup_lock()`; no job, no queue.
- Produces: `status_summary()` and every query it makes exclude `destination_kind=UPDATE_FALLBACK_KIND`: a fallback run never counts as a back-up, failure or interruption (D 4.8).
- Produces: `find_run(...)` accepts `run` as a run id **or** a label (`LABEL_PATTERN`): among complete runs it keeps those whose db snapshot carries `label:<run>`, newest first; none → `run_not_found`. `restore_fetch --run pre-update-0.3.0` then works unchanged.
- Produces command `python manage.py backup_now --label pre-update-X.Y.Z`: prints `run_id=<uuid>` and `kind=<folder|rest|s3|update>` on two lines (exit 0); a failed run → `CommandError(run.reason, returncode=1)`. Run by the wrapper as `$COMPOSE run --rm --no-deps -T --user 10001 admin python manage.py backup_now --label …`.
- REASONS: `label_invalid`, `update_backup_unavailable` ("De noodback-up voor het bijwerken kon niet worden klaargezet.").

- [ ] **Step 1: Write the failing tests** (fake restic and fake `pg_dump` from `tests/backup_support.py`; folders only under `tmp_path`; `settings.BRIEVIE_UPDATE_BACKUP_MOUNT = str(tmp_path / "update-backup")` and the guard's accepted path set the same way)

```python
def test_tags_carry_the_label():                       # "--tag", "label:pre-update-0.3.0" in backup_stdin_argv(run_id, cmd, label=…) and backup_files_argv(...)
@pytest.mark.parametrize("label", ["", "pre-update-", "pre-update-latest", "pre-update-0.3", "x pre-update-0.3.0"])
def test_bad_labels_are_refused(label)
def test_fallback_forget_keeps_only_the_newest_run():  # exact argv; no "--unsafe-allow-remove-all"
@pytest.mark.parametrize("path", ["/", "relative/dir", str(Path.home()), "/backup"])
def test_fallback_mount_guard_refuses(path)            # judged only: check_fallback_mount(Path(path)) raises; nothing created
def test_fallback_mount_guard_refuses_a_symlink(tmp_path)
def test_run_now_without_destination_uses_the_fallback(…):
    # no destination configured -> marker + restic init under tmp_path/update-backup; run.destination_kind == "update";
    # both backup argv carry label; the forget call is fallback_forget_argv(); run.status == "succeeded"
def test_run_now_with_a_destination_uses_it_and_counts(…)   # configured folder; destination_kind "folder"; status_summary().last_success == run
def test_fallback_run_does_not_count_in_status(…)      # succeeded fallback run -> last_success None; failed one -> last_failure None
def test_run_now_failure_is_recorded_with_its_reason(…) # fake restic exit 1 on the dump -> status failed, reason Dutch, no secret in reason/log
def test_run_now_puts_no_secret_in_argv(…)             # backup password, database password in no fake restic/pg_dump argv
def test_find_run_by_label_picks_the_newest_labelled_run(…)  # snapshots json: two runs with the label, one without -> newest labelled
def test_find_run_by_unknown_label_is_run_not_found(…)
def test_backup_now_prints_the_run_id(…)               # call_command output lines "run_id=<uuid>", "kind=update"
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_backup_update_backup.py -q` → FAIL.
- [ ] **Step 3: Implement**; `uv run python manage.py makemigrations backup -n backuprun_label`.
- [ ] **Step 4: Run** `make test lint typecheck` → PASS (B1/B2 back-up tests unchanged and green).
- [ ] **Step 5: Commit** `feat(backup): labelled back-ups before an update, with a local fallback`

---

### Task 5: The update's own commands: preflight, health check, record, status

**Files:**
- Create: `backend/apps/updates/models.py`, `backend/apps/updates/migrations/0001_initial.py`, `backend/apps/updates/services.py`, `backend/apps/updates/management/commands/{update_preflight,check_health,record_update,update_status}.py`
- Test: `backend/tests/test_updates_commands.py`

**Interfaces:**
- Consumes: `space_needed`, `Version` (Task 3); `destination.destination_kind()`, `settings.BRIEVIE_UPDATE_BACKUP_MOUNT`, `settings.BRIEVIE_STORAGE_ROOT`, `apps.backup.database.backup_conninfo(dbname=…)`.
- Produces: `class ServerUpdate(BaseModel): from_version = CharField(max_length=20); to_version = CharField(max_length=20); backup_run_id = UUIDField(null=True); finished_at = DateTimeField()`; `Meta.ordering = ["-finished_at"]`. Server-level history; the pin of what runs is `BRIEVIE_VERSION` in `deploy/.env` (Plan decision 7).
- Produces in `apps.updates.services`:
  - `@transaction.atomic record_update(*, from_version: str, to_version: str, backup_run_id: UUID | None) -> ServerUpdate` (both versions through `Version.parse`).
  - `last_update() -> ServerUpdate | None`.
  - `@dataclass(frozen=True) class Preflight: free_bytes: int; needed_bytes: int; fallback: bool` and `preflight() -> Preflight`: `fallback = destination_kind() == ""`; free space by `os.statvfs(BRIEVIE_UPDATE_BACKUP_MOUNT)` (the Docker data root's filesystem: volumes and images share it in the default set-up); documents by summing `st_size` over a walk of the storage root that follows no links; database by `SELECT pg_database_size(current_database())` as the back-up role.
  - `health_verdict(status: int, body: bytes, expected: str) -> str`: `""` when status 200 and `server_version == expected`; else `"unhealthy"` or `"wrong_version"`.
- Produces commands (each one service call):
  - `update_preflight`: exit 0 printing nothing; not enough → `CommandError("Er is te weinig vrije ruimte om bij te werken: nodig {needed}, vrij {free}. Er is niets veranderd.", returncode=1)` (sizes through `filesizeformat`).
  - `check_health --expect-version X.Y.Z`: GET `http://127.0.0.1:8000/api/v1/health` (Host header as in the compose healthcheck), up to 12 tries 5 s apart, then `health_verdict`; non-empty → returncode 1, "De server antwoordt niet goed na het bijwerken."
  - `record_update --from X --to Y [--backup-run UUID]`: prints "Vastgelegd: {from} → {to}".
  - `update_status`: "Laatst bijgewerkt: {from} → {to} op {dd-mm-jjjj uu:mm}" or "Nog nooit bijgewerkt met ./brievie update".

- [ ] **Step 1: Write the failing tests**

```python
def test_record_update_stores_versions_and_time(db)    # finished_at aware; last_update() is it
def test_record_update_refuses_a_malformed_version(db)
def test_preflight_with_fallback_needs_data_plus_reserve(…)  # statvfs and the walk patched; tmp_path storage; needed == space_needed(...)
def test_preflight_with_a_destination_needs_the_reserve_only(…)
def test_preflight_command_refuses_with_a_dutch_sentence(…)  # returncode 1, "te weinig vrije ruimte"
@pytest.mark.parametrize("status,body,expected,verdict", [(200, b'{"server_version":"0.3.0"}', "0.3.0", ""),
    (200, b'{"server_version":"0.2.0"}', "0.3.0", "wrong_version"), (503, b"{}", "0.3.0", "unhealthy"), (200, b"not json", "0.3.0", "unhealthy")])
def test_health_verdict(status, body, expected, verdict)
def test_update_status_lines(db, capsys)               # both sentences
def test_server_update_is_not_a_workspace_table()      # not a WorkspaceModel subclass
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_updates_commands.py -q` → FAIL.
- [ ] **Step 3: Implement**; `uv run python manage.py makemigrations updates`.
- [ ] **Step 4: Run** `make test lint typecheck` → PASS (incl. `makemigrations --check`).
- [ ] **Step 5: Commit** `feat(updates): preflight, health check and the update record`

---

### Task 6: Compose and image for updates

**Files:**
- Modify: `deploy/docker-compose.yml`, `deploy/Dockerfile`, `.gitignore`
- Test: `backend/tests/test_deploy_files.py`

**Interfaces:**
- Produces: every service that runs the backend image uses `image: ${BRIEVIE_IMAGE:-brievie/backend:${BRIEVIE_VERSION:-local}}` (`BRIEVIE_IMAGE` stays the override for build worktrees; an installation sets only `BRIEVIE_VERSION`, written by `./brievie start` and `./brievie update`).
- Produces: `worker` gets `stop_grace_period: 1m` with B1's comment (the keeper's): a job cut off is picked up again by the retry of stalled jobs (D 7.3 step 3).
- Produces: volume `update-backup`, mounted **only** in `admin` at `/update-backup`; `keeper`, `api`, `worker` never mount it. Dockerfile: `/update-backup` in the `mkdir -p … && chown brievie:brievie …` line.
- Produces `.gitignore`: `deploy/.update/`, `deploy/.env.update-tmp`, `/.e2e/`.

- [ ] **Step 1: Write the failing tests** (replace `test_every_service_takes_the_backend_image_from_one_variable`)

```python
IMAGE = "image: ${BRIEVIE_IMAGE:-brievie/backend:${BRIEVIE_VERSION:-local}}"
def test_every_service_takes_the_backend_image_from_the_version():   # api, migrate, worker, keeper, admin; no other "brievie/backend:" left
def test_worker_gets_a_minute_to_stop():               # "stop_grace_period: 1m" in service("worker") and service("keeper")
def test_only_admin_mounts_the_update_backup():        # "update-backup:/update-backup" in admin; absent from keeper, api, worker
def test_image_has_the_update_backup_directory():      # "/update-backup" in the Dockerfile's mkdir and chown line
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_deploy_files.py -q` → FAIL.
- [ ] **Step 3: Implement.** Then, in the build project `brievie-u1` only: `docker compose --project-directory deploy -f deploy/docker-compose.yml config --format json | grep -E '"name"|"image"'` prints `brievie-u1` and the `BRIEVIE_IMAGE` value; with `BRIEVIE_IMAGE` removed from the environment for one `config` call and `BRIEVIE_VERSION=9.9.9` set, every backend image is `brievie/backend:9.9.9` (proves nested interpolation on this Compose).
- [ ] **Step 4: Run** `make test lint typecheck` → PASS.
- [ ] **Step 5: Commit** `feat(deploy): image tag follows the version; worker stop period; update back-up volume`

---

### Task 7: `./brievie update`, steps 1-4: check, fetch, stop, back up

**Files:**
- Modify: `brievie`, `backend/tests/backup_support.py` (`FakeBinary.respond(..., run: str = "", stdin_to: str = "")`: a shell snippet run before answering, and a file that receives the call's stdin)
- Create: `backend/tests/wrapper_support.py`, `backend/tests/test_brievie_update.py`

**Interfaces:**
- Consumes: commands `update_plan` (Task 3), `backup_now`, `store_env_copy` (Task 4, B1), `update_preflight` (Task 5); B1's wrapper (`COMPOSE`, bring-along, the `.env` filter for the env copy).
- Produces (wrapper):
  - The root directory honours `BRIEVIE_ROOT` when set (the running copy needs it).
  - `checkout_version()` (shell): `version = "…"` from `backend/pyproject.toml` in the working tree. `./brievie start` writes `BRIEVIE_VERSION=<checkout_version>` into `deploy/.env` (replace the line or append; via `deploy/.env.update-tmp` + `mv`, mode 600) before `up --build`; it refuses while `deploy/.update/in-progress` exists (Task 8 message).
  - `./brievie update [VERSION]`, exit codes: 0 done or already, 1 refused or failed with the old version running again, 2 usage, 3 failed after the checkout (way back printed). First it copies itself to `deploy/.update/brievie-running` (umask 077) and `exec`s that copy with `BRIEVIE_ROOT` set, so the checkout can replace `brievie` while the update runs.
  - **Step 1, check** (nothing may change; each refusal ends with "Er is niets veranderd."): VERSION, when given, against `^v?(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$` before any use; `docker` and `git` present; `deploy/.update/in-progress` absent (else Task 8); `BRIEVIE_IMAGE` not set in the environment or `deploy/.env` ("Deze installatie gebruikt een eigen image (BRIEVIE_IMAGE). Werk dan met de hand bij."); `BRIEVIE_VERSION` in `deploy/.env` = installed version, absent → print the `before_versions` sentence of Task 3 (the same Dutch text, written in the wrapper because an image from before versions has no `update_plan`; `{target}` = the newest tag after the fetch, or `0.2.0` when the fetch fails) and exit 1; `checkout_version` equals it; `git status --porcelain --untracked-files=no` empty ("Er zijn lokale wijzigingen in deze map …"); `git rev-list HEAD --not --remotes --tags` empty ("Deze map heeft eigen commits …"); postgres running and image `brievie/backend:<installed>` present (`docker image inspect`); `git fetch --tags origin` (no `--force`: a moved tag fails it); target = VERSION or the highest `vX.Y.Z` tag; the release stream (`--- vX.Y.Z` + `git show vX.Y.Z:deploy/release.json` per tag above the installed version, tags without the file skipped) piped to `docker run --rm -i --network none -e SECRET_KEY=<the Dockerfile's collectstatic-only value> -e DATABASE_URL=postgres://build@localhost/build brievie/backend:<installed> python manage.py update_plan --installed … --target … --postgres-major <$COMPOSE exec -T postgres sh -c 'cat "$PGDATA/PG_VERSION"'>`; `already` → "Je hebt al versie X." exit 0; then `$COMPOSE run --rm --no-deps -T --user 10001 admin python manage.py update_preflight`.
  - **Step 2, fetch**: `git archive --format=tar "v$target" | docker build --file deploy/Dockerfile --tag "brievie/backend:$target" -`. Failure → "Het bouwen van versie X is mislukt. Er is niets veranderd; Brievie draait gewoon door." exit 1.
  - Writes `deploy/.update/in-progress` (key=value lines: `from`, `to`, `previous_ref` = `git rev-parse HEAD`, `step=stopped`) just before step 3. From here a `trap` on INT, TERM and EXIT (cleared at the hand-over) restarts the old version: `$COMPOSE up -d --wait --no-build`, removes the marker, prints "Bijwerken afgebroken. Er is niets bijgewerkt; Brievie draait weer op versie X." exit 1.
  - **Step 3, stop**: `$COMPOSE stop api worker keeper` (caddy and postgres keep running).
  - **Step 4, back up**: the filtered `.env` (B1's filter) on stdin of `$COMPOSE run --rm --no-deps -T --user 10001 admin python manage.py store_env_copy`; then `$COMPOSE run --rm --no-deps -T --user 10001 admin python manage.py backup_now --label pre-update-$target`; its `run_id=` line goes into the marker (`run_id`, `step=backed-up`). Failure → the trap's restart, message "De back-up vóór het bijwerken is mislukt: <reason>. Er is niets bijgewerkt; Brievie draait weer op versie X."
  - Then Task 8's checkout and hand-over.
- Produces `tests/wrapper_support.py`: fixture `update_install(tmp_path) -> UpdateInstall` with a bare `origin` repo and a clone `install` under `tmp_path`, commits tagged `v0.2.0` and `v0.3.0` (each with `brievie` copied from the repository, `backend/pyproject.toml`, `deploy/release.json`, `deploy/docker-compose.yml`, `deploy/init-env.sh`, `deploy/.env.example`, `deploy/ensure-backup-role.sh`, `deploy/postgres-init/*`), the clone checked out at `v0.2.0` with a `deploy/.env` holding `BRIEVIE_VERSION=0.2.0` and every B1 line; a fake `docker` (`FakeBinary`) first on `PATH` answering: `compose … ps` (postgres, api, keeper running), `exec -T postgres sh -c …` → `18`, `image inspect` → 0, `run … update_plan` → `allowed`, `run … backup_now` → `run_id=<uuid>\nkind=update`; helpers `run_wrapper(*args, env=…) -> CompletedProcess`, `order_of(calls, *needles) -> list[int]`, `tracked_state() -> (HEAD, sha256 of deploy/.env)`. Uses B1's wrapper fixture instead where one exists.

- [ ] **Step 1: Write the failing tests** (`test_brievie_update.py`, real `git`, all paths under `tmp_path`)

```python
def test_update_refuses_a_malformed_version(update_install)       # each of "latest", "0.3", "v0.3.0-rc.1", "0.3.0;rm -rf x", "../x": exit 1,
                                                                    # no fake docker call, origin's refs not fetched, tracked_state unchanged
def test_update_with_two_versions_is_usage(update_install)          # "./brievie update 0.3.0 0.4.0" -> exit 2
def test_update_refuses_a_dirty_tree(update_install)                # edit a tracked file -> exit 1, "lokale wijzigingen", no docker build, no stop
def test_update_refuses_local_commits(update_install)               # commit on top of v0.2.0 -> exit 1, "eigen commits"
def test_update_from_before_versions_gives_the_manual_path(…)       # no BRIEVIE_VERSION line -> exit 1, "git checkout v0.3.0" in stderr, no docker run
def test_update_refuses_a_custom_image(…)                           # BRIEVIE_IMAGE=x in .env -> exit 1
def test_update_refuses_when_fetch_fails(…)                         # origin renamed away -> exit 1, nothing changed
def test_update_refuses_a_moved_tag(…)                              # v0.3.0 re-pointed in origin -> exit 1, nothing changed
def test_update_to_the_installed_version_says_so(…)                 # fake update_plan -> "already": exit 0, "Je hebt al versie 0.2.0", no build
def test_refused_jump_is_passed_on(…)                               # fake update_plan exit 1 with "Werk eerst bij naar 0.2.5" -> same text, exit 1, no build, no stop
def test_update_without_version_takes_the_newest_tag(…)             # extra tags v0.10.0 and v0.4.0-rc.1 -> update_plan argv has "--target", "0.10.0"
def test_release_stream_reaches_update_plan(…)                      # update_plan stdin holds "--- v0.3.0" and that tag's release.json; argv has "--postgres-major", "18"
def test_nothing_changes_before_stop(…)                             # fake docker build exit 1 -> exit 1; tracked_state unchanged; no "stop", no "up" call; no marker
def test_steps_1_to_4_run_in_order(…)                               # order_of: update_plan < update_preflight < build < "stop api worker keeper" < store_env_copy < backup_now
def test_backup_failure_restarts_the_old_version(…)                 # backup_now exit 1 -> exit 1; "up -d --wait --no-build" after it; HEAD still v0.2.0; marker gone
def test_ctrl_c_during_the_backup_restarts_the_old_version(…)       # backup_now's `run` snippet: kill -INT "$PPID" -> same as above
def test_env_copy_has_no_master_key_and_no_secret_in_argv(…)        # store_env_copy stdin lacks BRIEVIE_MASTER_KEY/POSTGRES_PASSWORD/BRIEVIE_OWNER_PASSWORD;
                                                                    # their values in no logged argv
def test_start_pins_the_checkout_version(…)                         # ./brievie start -> .env has exactly one BRIEVIE_VERSION=0.2.0, mode 600
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_brievie_update.py -q` → FAIL.
- [ ] **Step 3: Implement** (POSIX `sh`, `set -eu`, Dutch messages, B1's style; layout the builder's, D 11).
- [ ] **Step 4: Run** `make test lint typecheck` → PASS (B1's wrapper tests unchanged and green).
- [ ] **Step 5: Commit** `feat(deploy): ./brievie update checks, builds, stops and backs up`

---

### Task 8: `./brievie update`, steps 5-6: checkout, hand-over, migrate, start, health, record, and the way back

**Files:**
- Modify: `brievie`
- Test: `backend/tests/test_brievie_update.py`

**Interfaces:**
- Consumes: Task 7's marker and trap; commands `check_health`, `record_update`, `update_status` (Task 5); B1's `restore`.
- Produces (wrapper):
  - **Checkout**: `git -c advice.detachedHead=false checkout --quiet "v$target"`; failure → the trap's restart, "Er is niets bijgewerkt", exit 1. Success → marker `step=checked-out`, the trap cleared, then `exec sh "$ROOT/brievie" _update-finish` (the **target's** file).
  - **`./brievie _update-finish`** (internal; its only input is the marker, whose keys `from`, `to`, `run_id`, `previous_ref`, `step` every later wrapper must keep accepting): bring-along of the new checkout (B1: `init-env.sh`, `ensure-backup-role.sh`); `BRIEVIE_VERSION=$to` into `deploy/.env` (temp + `mv`); `$COMPOSE run --rm migrate`; `$COMPOSE up -d --wait --no-build`; `$COMPOSE exec -T api python manage.py check_health --expect-version "$to"`; `$COMPOSE exec -T api python manage.py record_update --from "$from" --to "$to" --backup-run "$run_id"` (a failure here only warns: "Bijgewerkt, maar het vastleggen lukte niet."); `rm -f deploy/.update/in-progress`; prints "Bijgewerkt naar versie {to}." and where the back-up of the old version is (`pre-update-{to}`; for `kind=update`: "een noodback-up op deze machine; hij telt niet als back-up").
  - **The way back**, printed (exit 3) when migrate, start or health fails; the server stays stopped (`$COMPOSE stop api worker keeper`), the marker stays. `<ref>` = `v$from` when that tag resolves to `previous_ref`, else the first 12 characters of `previous_ref`:
    ```
    Het bijwerken naar versie {to} is niet gelukt. Brievie is gestopt.
    Terug naar versie {from} (houd je herstelblad bij de hand):
      git checkout {ref}
      ./brievie restore pre-update-{to} --replace --skip-safety-backup
    Of probeer het opnieuw: ./brievie update {to}
    ```
  - **An unfinished update** (marker present) when `./brievie update [VERSION]` starts: with `step=checked-out`, HEAD at `v$to` and VERSION absent or equal to `to` → resume `_update-finish` (migrations are idempotent); otherwise print "Een eerdere update naar {to} is niet afgemaakt." plus the way back, exit 1. With `step` `stopped` or `backed-up` (the trap did not run: a killed terminal) → restart the old version as the trap would, remove the marker, then go on with the requested update from step 1. `./brievie start` with a marker → the same "niet afgemaakt" text, exit 1.
  - **`./brievie restore pre-update-X.Y.Z`**: when `deploy/.env` sets neither `BRIEVIE_BACKUP_DIR` nor `BRIEVIE_BACKUP_REPOSITORY`, the source is the fallback volume: B1's `docker run` of `restore_fetch` gets `-v "${COMPOSE_PROJECT_NAME:-brievie}_update-backup:/backup:ro" -e BRIEVIE_BACKUP_DIR_SET=true` instead of asking. B1's image for that `docker run` becomes `brievie/backend:<checkout_version>` (built when absent). After a successful restore whose label equals the marker's `pre-update-{to}`, the marker is removed.
  - **`./brievie status`** also runs `$COMPOSE exec -T keeper python manage.py update_status`.

- [ ] **Step 1: Write the failing tests**

```python
def test_full_update_runs_every_step_in_order(update_install):
    # order_of: build < stop < backup_now < (HEAD == v0.3.0) < "run --rm migrate" < "up -d --wait --no-build" < check_health < record_update
    # exit 0; .env BRIEVIE_VERSION=0.3.0 (one line, mode 600); marker gone; "Bijgewerkt naar versie 0.3.0."
def test_the_target_wrapper_finishes_the_update(…)        # v0.3.0's brievie has "Bijgewerkt naar versie" replaced by "NIEUW-BIJGEWERKT naar versie": output holds the new text
def test_the_running_file_is_a_copy(…)                    # deploy/.update/brievie-running exists and equals v0.2.0's brievie
def test_checkout_failure_restarts_the_old_version(…)     # untracked file at a path v0.3.0 tracks -> exit 1; HEAD v0.2.0; "up -d --wait --no-build"; .env still 0.2.0
def test_migrate_failure_prints_the_way_back(…):          # fake "run --rm migrate" exit 1 -> exit 3;
    # stdout lines "  git checkout v0.2.0" and "  ./brievie restore pre-update-0.3.0 --replace --skip-safety-backup"; no "up -d --wait" after migrate
def test_health_failure_prints_the_way_back(…)            # check_health exit 1 -> exit 3, same two lines, "stop api worker keeper" logged after it
def test_way_back_names_a_commit_when_no_tag_fits(…)      # installed from an untagged origin/main commit -> "git checkout <12 hex>"
def test_record_failure_only_warns(…)                     # record_update exit 1 -> exit 0, warning, marker gone
def test_unfinished_update_is_resumed(…)                  # marker step=checked-out at v0.3.0 -> "./brievie update" runs _update-finish only: no build, no backup_now
def test_unfinished_update_before_checkout_restarts_first(…)  # marker step=backed-up at v0.2.0 -> "up -d --wait --no-build" before the new step 1
def test_start_refuses_while_an_update_is_unfinished(…)
def test_finish_interface_reads_only_the_marker(…)        # "_update-finish" without a marker -> exit 2 with usage; with one -> runs
def test_restore_by_label_uses_the_fallback_volume(…)     # no destination in .env -> restore_fetch docker run has "-v", "brievie_update-backup:/backup:ro" (project from .env)
def test_restore_by_label_clears_the_marker(…)            # successful fake restore of pre-update-0.3.0 -> marker gone
def test_status_shows_the_last_update(…)                  # "update_status" exec after "backup_status"
```

- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_brievie_update.py -q` → FAIL (new tests).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `make test lint typecheck` → PASS.
- [ ] **Step 5: Commit** `feat(deploy): ./brievie update migrates, starts, checks and prints the way back`

---

### Task 9: The real update in containers, and the texts

**Files:**
- Modify: `README.md` (section "Updating": `./brievie update`, pointer to SELFHOST), `docs/SELFHOST.md` (new section "Updating"), `SECURITY.md` ("Supported versions": fixes go into the latest release; `./brievie update` gets it; versions are tags `vX.Y.Z`, listed in `CHANGELOG.md`), `docs/DECISIONS.md`, `docs/GLOSSARY.md`, `CHANGELOG.md` (only if the proof changes something user-visible)
- No new test file: this task proves the whole against the real programs.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Safety checks before anything runs.** In the build worktree: `/.e2e/` is in `.gitignore`; `docker image inspect brievie/backend:0.2.0 brievie/backend:0.2.1 brievie/backend:0.2.2` fails for all three (if one exists: stop and report; the proof never overwrites an image it did not make); no Docker project named `brievie-u1e2e` exists (`docker compose ls --all`).
- [ ] **Step 2: Build the throwaway installation** inside the build worktree (the only Mac directory a container may bind-mount, hard rule 4): `git clone --no-hardlinks . .e2e/install`; in the clone only: tag `HEAD` as `v0.2.0`; commit "0.2.1" (pyproject, `uv.lock` line, `CHANGELOG.md`, `release.json` `{"format": 1, "version": "0.2.1", "min_upgrade_from": "0.2.0", "postgres_major": 18}`, a no-op migration in `apps.updates`) tagged `v0.2.1`; commit "0.2.2" (same files, plus a migration that raises) tagged `v0.2.2`; `git checkout v0.2.0`. `deploy/.env` by `sh deploy/init-env.sh`, then `COMPOSE_PROJECT_NAME=brievie-u1e2e`, `POSTGRES_PORT=5435`, `BRIEVIE_HTTP_PORT=8482`, `BRIEVIE_HTTPS_PORT=8445`, no `BRIEVIE_IMAGE`, no back-up destination. From here every command runs in `.e2e/install`, and before each `down`/`restore --replace` the `config --format json | grep '"name"'` check must print `brievie-u1e2e`.
- [ ] **Step 3: Start and seed**: `./brievie start`; health shows `server_version` 0.2.0; seed with B1's `.github/scripts/restore-seed.py` (token and page hashes kept in a file under `.e2e/`, never printed).
- [ ] **Step 4: Update 0.2.0 → 0.2.1**: `./brievie update 0.2.1`. Expected: exit 0; the output names `pre-update-0.2.1` as an emergency copy; health 0.2.1 through Caddy; the seeded page's hash matches with the device token; `./brievie status` still says "Laatste geslaagde back-up: nog nooit" (the fallback does not count) and "Laatst bijgewerkt: 0.2.0 → 0.2.1".
- [ ] **Step 5: A failing update and the way back**: `./brievie update 0.2.2` → exit 3 with the two way-back lines (the raising migration); run them literally (`BRIEVIE_RESTORE_MASTER_KEY` read from this throwaway installation's `.env`, never in argv). Expected: the restore finds `pre-update-0.2.2` in the fallback volume, `verify_restore` passes, health 0.2.1, the seeded page opens, the marker is gone, and `./brievie update 0.2.1` answers "Je hebt al versie 0.2.1."
- [ ] **Step 6: An interrupted update resumes**: after the name check, `down --volumes`, `git checkout v0.2.0`, set `BRIEVIE_VERSION=0.2.0`, repeat Step 3; run `./brievie update 0.2.1` and stop the terminal process (`kill -KILL`, so no trap runs) once the log shows `run --rm migrate`. Expected: `./brievie start` refuses with "niet afgemaakt"; `./brievie update` resumes `_update-finish` and ends healthy on 0.2.1.
- [ ] **Step 7: Tear down**: name check, `docker compose --project-directory deploy -f deploy/docker-compose.yml down --volumes` in `.e2e/install`; `docker image rm brievie/backend:0.2.0 brievie/backend:0.2.1 brievie/backend:0.2.2` (only these: made in Step 2-5); `rm -rf .e2e` from the build worktree root (fixed relative name). Record each step's outcome in the task report.
- [ ] **Step 8: Texts.** `docs/SELFHOST.md` section "Updating" (English): versions and `CHANGELOG.md` ("Voor je bijwerkt" first); `./brievie update` and `./brievie update X.Y.Z`; the order and that the server is away for the length of a back-up; the pre-update back-up (to the destination when one is set, else an emergency copy in the Docker volume `<project>_update-backup` that holds only the newest one, is about one compressed copy of the data, does **not** count as a back-up and lies on the same machine: remove it with `docker volume rm <project>_update-backup` when no update is unfinished); refused jumps and what to type; the way back and that migrations are not reversed; an unfinished update; PostgreSQL major versions are not crossed yet; an installation from before versions moves once by hand (`git fetch --tags`, `git checkout v0.2.0`, `./brievie start`); updates never start by themselves. `DECISIONS.md` lines (Plan decisions 1-12 below, one each). `GLOSSARY.md`: bijwerken = update, versie = version, noodback-up = update fallback back-up, wijzigingslog = change log. The site texts (D 7.7) are the owner's; nothing in `Brievie_site` changes.
- [ ] **Step 9: Run** `make test lint typecheck`, `cd web && pnpm test` → PASS.
- [ ] **Step 10: Commit** `docs: updating with ./brievie update`

---

## Plan decisions

1. **Version source `backend/pyproject.toml`, read at run time by `server_version()`**: the image already holds `/app/pyproject.toml`, so the image, health and manifest need no build argument; the web learns it from `getHealth` and `web/package.json` stays `0.0.0` by test.
2. **First version 0.2.0**: every installation from before versions reports 0.1.0 (pyproject has said so since the start), so the first release must differ from it; a minor bump in 0.x says "new features, pre-1.0".
3. **`min_upgrade_from` of 0.2.0 is 0.2.0, and 0.1.0 is "before versions"**: neither the old wrapper nor the old image has `update`, a labelled back-up or a restore by label, so the first move is one documented manual step (`git fetch --tags && git checkout v0.2.0 && ./brievie start`); the pure function refuses 0.1.0 with that text.
4. **"Installed version" = `BRIEVIE_VERSION` in `deploy/.env`, which pins the image tag `brievie/backend:<version>`**, written by `start` and `update` and restored with `.env` by a restore; this is U2's `BRIEVIE_VERSION` early, so the way back (checkout + restore of the old `.env`) brings the old image back with it.
5. **"Fetch" builds the target image from `git archive v<target> | docker build -`**: no working-tree change and no stopped server while it builds, like pulling an image in U2; a build failure leaves everything as it was.
6. **The release rules run in the installed image (`update_plan`, `--network none`)**, the release stream comes from `git show` of every tag; the old code refuses a `release.json` format it does not know, which is how a later release says "this updater cannot do it".
7. **Record = a server table `ServerUpdate` (history, in every back-up, readable by U3's web) plus the `.env` pin (what runs)**; a file alone would not travel with a restore, a table alone cannot choose the image before the database is up.
8. **The pre-update back-up runs in the one-off `admin` container as uid 10001 with the installed image** (`run_now`, synchronous, no queue): the keeper is stopped by step 3, and the code that matches the data's schema makes the back-up that the same version restores.
9. **Tag `label:pre-update-X.Y.Z` on both snapshots plus `BackupRun.label`; `find_run` accepts a label**: `./brievie restore pre-update-<version>` works with B1's restore steps unchanged.
10. **The fallback is a named volume `update-backup` (admin only) in B1's folder layout, keeping only the newest run**, not a host folder: no uid 10001 ownership problem on the host, nothing the host user must remove by hand as root, and a restore opens it like a folder destination. It is removed only by hand; it does not count in `status`. This deviates from D 7.3's "in the installation folder" in place, not in meaning: it is on the same machine, next to the key.
11. **Steps 1-4 run from a copy of the installed wrapper; after the checkout the target's wrapper finishes (`_update-finish`) from a marker file**: `git checkout` can replace `brievie` safely, new `.env` lines arrive through the new `init-env.sh`, and the marker makes an update interrupted after the migration resumable (D 9).
12. **Health: `SELECT 1` as the app role, 503 `database_unavailable`, the route made non-atomic**: with `ATOMIC_REQUESTS` the connection is opened before the view, so without that the answer would be a 500; the compose healthcheck already calls the endpoint and now fails with the database, which makes `up --wait` in an update a real check.
13. **Free space: a 4 GiB build reserve, plus documents and database size when the fallback is used**, measured on the Docker data root's filesystem from the admin container; a full remote destination shows up as B1's `destination_full` at step 4, with the old version restarted.
14. **PostgreSQL major: compared with the data directory's `PG_VERSION`**, the format that actually matters; any difference is refused in U1 with a sentence.

## Assumptions about B1 code that was still being written

- `./brievie` (B1 Task 12) exists as POSIX `sh` with one `COMPOSE` variable, a root directory computed once from the script's own path (U1 makes it honour `BRIEVIE_ROOT`), the bring-along at the start of every command, and the commands and flags of B1 Task 12 (`start`, `backup`, `restore [RUN] [--replace] [--skip-safety-backup]`, `status`, `sweep`, `recovery-sheet`); `restore` passes `RUN` to `restore_fetch --run` untouched and restores `deploy/.env` from the back-up.
- B1's restore runs `restore_fetch` in `brievie/backend:local` (built when absent); U1 changes that to `brievie/backend:<checkout_version>`. If B1 already uses `${BRIEVIE_IMAGE…}`, U1 keeps its form and only adds the version default.
- The `.env` filter for the env copy (`grep -v -E '^(BRIEVIE_MASTER_KEY|POSTGRES_PASSWORD|BRIEVIE_OWNER_PASSWORD|DATABASE_OWNER_URL)='`) is a reusable shell function or one line Task 7 can call; otherwise Task 7 extracts it without changing it.
- B1's wrapper tests build their fake `docker` on `tests/backup_support.FakeBinary` and copy the wrapper and `deploy/` into `tmp_path`; Task 7 reuses that fixture when it exists and otherwise creates `tests/wrapper_support.py`. `FakeBinary` gains `run=` and `stdin_to=` without changing existing calls.
- B2 is merged before U1 starts: `forget_argv(retention)` replaces `RETENTION`, `_back_up` reads the retention from settings, migration `0002_backuprun_restore_test` exists (so U1's is `0003`), and `apps.backup.safepaths` provides path guards (`require_emptiable` and friends) that `check_fallback_mount` may call; if `safepaths` is not there yet, `check_fallback_mount` carries its own refusals as specified.
- `services._back_up(run, failures, *, resumed, clean_up)`, `_finish`, `_record_failure`, `destination._run_init` and `restore._snapshots_of_kind` keep the shapes read on 2026-11-17 (`services.py`, `destination.py`, `restore.py` at `483a2d6`).
- The app role may insert into a new server-level table through the default privileges set in `deploy/postgres-init` (as `BackupRun` is written by the keeper today).
- Compose v2 on the owner's machines resolves nested defaults (`${A:-x:${B:-local}}`); Task 6 Step 3 proves it before anything depends on it.
