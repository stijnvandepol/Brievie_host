# Host "Installeren met alleen een compose-bestand" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user installs and updates Brievie with one pasted `docker-compose.yml` (Portainer, Synology Container Manager, Dockge, Unraid's compose plugin, or `docker compose up -d`): no git checkout, no `./brievie`. Secrets are generated on first start and kept in volumes, the safety of `./brievie update` runs inside the first container that starts, and the backups, the recovery sheet and their status no longer need a terminal. The wrapper install keeps working on the same volumes.

**Architecture:** Images come from ghcr: the existing `brievie-backend:<version>` and a new `brievie-proxy:<version>` (Caddy with `deploy/Caddyfile` and `caddy-publish-root.sh` baked in). Two one-shot services replace the wrapper's start: `init-secrets` (no network; makes the secrets in a vault volume and copies each role's files into its own volume; refuses when data exists without keys) and `init` (`manage.py prepare_server`: roles and database, the version gate built on the existing pure `apps.updates.release.decide`, a read-only freeze of the database, the emergency back-up, migrations, `InstalledVersion`). Every secret variable `X` also reads `X_FILE` (the variable wins, so a wrapper install is unchanged). PostgreSQL moves to an `internal` network. `deploy/compose/docker-compose.yml` is a second definition next to `deploy/docker-compose.yml`; a parity test keeps them equal until the wrapper consumes the new file (phase 2, not here). The recovery sheet, a nightly back-up and a back-up status panel get a web path.

**Tech Stack:** Python 3.13, Django 6.1, Django Ninja, PostgreSQL 18, Procrastinate, restic; Caddy 2.11; Docker Compose v2; GitHub Actions; React 19, TanStack Router/Query, Lingui, Vitest, Biome. Site: static HTML, `tools/releases/generate.py`. No new Python or npm dependency.

**Spec:** `docs/superpowers/specs/2026-11-23-installatie-met-compose-design.md`, cited "S 4.6" etc. Neighbour: `docs/superpowers/specs/2026-11-21-externe-toegang-design.md` (branch `docs/external-access-design`, "EA").

## Global Constraints

From `CLAUDE.md` (quoted where exact):

- "No new dependency without a line in `docs/DECISIONS.md`." The new image `brievie-proxy` and the `_FILE` convention each get a DECISIONS line (T13); the freeze (T6) too.
- "Every table: UUID `id`, `created_at`, `updated_at`." `InstalledVersion` is a server-wide table (no `workspace_id`); `brievie_app` may read it and `INSERT/UPDATE` it only through `apps.updates.services` (the migration grants it as for `ServerUpdate`).
- "`mypy --strict` and `ruff check` clean. No `Any`, no bare `except`." "Money is `Decimal`; datetimes are timezone-aware."
- "API only under `/api/v1`, additive changes only. Every endpoint: typed schemas and an explicit camelCase `operation_id`." "Every non-2xx body: `{"code": "<snake_case>", "message": "<Dutch sentence>"}`." "Nullable fields in API schemas are primitives only; the contract test forbids `anyOf`." "A field added to an existing API schema has a default."
- "Endpoints that administer people ... use `auth=session_auth`: a device token gets 401. A response that carries a secret once sets `Cache-Control: no-store`." The recovery sheet (T9) carries the master key: `session_auth`, server admin, `reauthenticate` within 15 minutes, `no-store`.
- "`apps.audit.services.record()` is the only writer of audit rows. It takes only a code of the closed list": add `recovery_sheet_viewed` with no values.
- "No document content, tokens, passwords or BSNs in logs." No secret is a process argument or an environment value of any container; `prepare_server` and `init_secrets` print only Dutch sentences. The one exception stays: the installation code, once.
- "Text goes to an external AI service only with a recorded consent" and "`apps.pipeline.settings_service`" are untouched.
- "No Redis, no Celery, no Node runtime container. Background jobs: Procrastinate, in an app's `jobs.py`; a job takes ids only and enters `workspace_context` itself, in short transactions (none open during an external call)." The nightly back-up (T10) is a Procrastinate periodic task on the `backup` queue.
- "Only `apps/documents/page_files.py` removes a letter's page files ... its tests make and remove files only under `tmp_path`." Every new test that writes or removes files does so only under `tmp_path`.
- "The rules engine stays pure"; `apps.updates.release` and the new `apps.updates.gate` stay pure: no Django import except lazy Dutch texts, no database, no files.
- User-facing text Dutch B1 through `gettext` / Lingui `t`, English beside it (`make messages`, `pnpm extract`, terms from `docs/GLOSSARY.md`).
- "Tests first for logic. Conventional commits." Every commit ends with `Co-Authored-By: Claude <noreply@anthropic.com>`.

From the environment:

- **Backend tests run in CI only.** Never run `docker`, `docker compose`, `./brievie`, `make dev` or `make test` on this Mac, and never touch the PostgreSQL there. A backend "run" step means: commit, push, read the `backend` job and the new CI jobs of T12. `make lint`, `make typecheck`, `pnpm lint`, `pnpm test` and `pnpm build` run locally.
- **Never delete or restore anything outside `tmp_path`** (wipe code rules in the owner's memory). The CI jobs that run `down --volumes` keep the existing guard "only on a GitHub-hosted runner" (`RUNNER_ENVIRONMENT = github-hosted`).
- **Coordination with EA.** EA changes `deploy/Caddyfile`, `deploy/docker-compose.yml` (a `tunnel` profile, `cloudflared`, new variables) and `apps/access/addresses.py`. T8 copies whatever EA has landed; whichever lands second rebases. `compose-parity` (T8) fails if the two compose files drift.
- **Release.** A tag `vX.Y.Z` still needs `backend/pyproject.toml`, `deploy/release.json` and the newest `CHANGELOG.md` entry to agree; this plan adds the compose file's default version and `BRIEVIE_COMPOSE_REVISION` to that check (T7). Do not add a key to `deploy/release.json`: installed images parse the next tag's file and refuse unknown keys (`_RELEASE_KEYS` in `apps/updates/release.py`).
- **Not tested on real hardware:** Synology, Unraid, Portainer, Dockge, Podman, Windows, a Raspberry Pi. Every doc and site line that mentions them says "Niet getest" until someone runs the steps.

## Review Focus

1. **A new master key beside old documents.** `init_secrets` must never generate when data exists and the vault is empty (S 4.4); read every branch.
2. **Secret separation.** The keeper volume holds no `master_key`; the api volume holds no owner or superuser password; no secret appears in `docker compose config`, `docker inspect`, any log or argv (the CI step searches for it).
3. **The version gate refuses before it changes anything**, and a refusal leaves the old containers running (verify the claim in CI; if compose recreates dependents anyway, S 4.6 and the page change).
4. **The freeze** (`default_transaction_read_only`) is lifted on every failure path that leaves the schema untouched, and stays on, with the way back printed, when a migration ran.
5. **Wrapper installs stay as they were**: `X` wins over `X_FILE`, the wrapper's compose file is byte-identical except where EA changes it, `./brievie` tests and the `stack`, `update`, `restore` jobs still pass untouched.
6. **The recovery sheet endpoint**: session only, server admin, reauthentication, `no-store`, one audit row, no secret in any log.

## File Map

Backend: `backend/apps/common/secrets.py` (new, pure), `backend/brievie/settings.py`, `backend/apps/updates/gate.py` (new, pure), `backend/apps/updates/models.py`, `services.py`, `migrations/`, `backend/apps/installer/` (new app: `init_secrets.py`, `database.py`, `prepare.py`, `management/commands/init_secrets.py`, `management/commands/prepare_server.py`), `backend/apps/backup/jobs.py`, `backend/apps/backup/api.py` (new), `backend/apps/backup/sheet.py`, `backend/apps/audit/codes.py`.
Deploy: `deploy/Dockerfile`, `deploy/Dockerfile.proxy` (new), `deploy/compose/docker-compose.yml` (new), `deploy/compose/ci.override.yml`, `deploy/compose/.env.example`, `deploy/Caddyfile` (unchanged by this plan).
CI: `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `.github/scripts/release-check.py`, `.github/scripts/update-releases.py`.
Web: `web/src/routes/recovery-sheet.tsx`, `web/src/routes/backup.tsx`, `web/src/routes/setup-summary.tsx`, `web/src/api/`, both catalogues.
Docs: `docs/SELFHOST.md`, `README.md`, `docs/DECISIONS.md`, `CHANGELOG.md`, `docs/SPEC.md`, `docs/PRIVACY.md`.

---

## Tasks

### T1: `X_FILE` for secrets (pure reader and settings)

**Files:** `backend/apps/common/secrets.py` (new), `backend/tests/test_common_secrets.py` (new), `backend/brievie/settings.py`, `backend/tests/test_settings_secrets.py` (new).

**Interfaces:**
- `read_secret(name: str, environ: Mapping[str, str], *, default: str = "") -> str`: the value of `name` when it is set and not empty; else the stripped first line of the file named by `name + "_FILE"`; else `default`. A file that is a symbolic link, is larger than 4 KiB, is not valid UTF-8, or is readable by group or others when `environ` has `BRIEVIE_STRICT_SECRET_FILES=true` raises `SecretFileError(code)` (closed codes: `unreadable`, `link`, `too_large`, `not_text`, `too_open`). Pure: takes the mapping and a reader callable; no Django import.
- `settings.py`: `SECRET_KEY`, `BRIEVIE_MASTER_KEY`, `BRIEVIE_BACKUP_PASSWORD` and the four database URLs go through it. `DATABASE_URL`, `DATABASE_OWNER_URL`, `DATABASE_BACKUP_URL`, `DATABASE_SUPERUSER_URL` may carry no password, and `<NAME>_PASSWORD_FILE` supplies it (`with_password(url, password)` quotes it for a URL; the passwords are hex, but the function must be right for any text).

- [ ] Write the failing tests: env wins over file; file used when env is empty or unset; a trailing newline is stripped; a missing file raises `unreadable` and the message never contains the file's content; a link, a 5 KiB file and a binary file are refused; `with_password` keeps a URL that already has a password; `SECRET_KEY_FILE` works with the existing placeholder refusal (a value containing `change-me` is still refused).
- [ ] Run (push, read the `backend` job): fail for the right reason.
- [ ] Implement; keep `settings.py` readable (one helper call per setting).
- [ ] `make lint`, `make typecheck`; push; green.
- [ ] Commit: `feat(common): read secrets from X_FILE next to X`.

### T2: The image carries `release.json` and the compose revision; pure gate

**Files:** `deploy/Dockerfile`, `backend/apps/updates/gate.py` (new), `backend/tests/test_updates_gate.py` (new), `backend/tests/test_deploy_dockerfile.py` (new or extended).

**Interfaces:**
- `deploy/Dockerfile`: `COPY deploy/release.json /app/release.json` (the same file the tag holds) and `ENV BRIEVIE_COMPOSE_REVISION=1`. Neither changes the build for the wrapper.
- `gate.decide_start(*, image: Version, installed: Version | None, release: Release, database_major: int | None, unknown_migrations: bool, compose_revision: int, image_compose_revision: int, adopt_version: Version | None, has_data: bool, free_bytes: int, needed_bytes: int) -> GateDecision`. `GateDecision` is a frozen dataclass: `outcome: Literal["fresh", "same", "update", "refused"]`, `code: str`, `go_first: Version | None`. It reuses `release.decide` for the jump and `release.REASONS` for sentences; new codes (all with Dutch sentences in `REASONS` and English in the catalogue): `downgrade`, `unknown_migrations`, `postgres_major_differs`, `compose_too_old`, `compose_too_new`, `no_space`, `adopt_needed`, `adopt_differs`.
- Rule order is S 4.6: downgrade or unknown migrations, jump, PostgreSQL major, compose revision, space. `installed is None and has_data` needs `adopt_version == image`, else `adopt_needed`/`adopt_differs`. `installed is None and not has_data` is `fresh`.

- [ ] Failing tests, one per rule and per boundary: equal versions give `same`; a lower image refuses `downgrade`; a database with migrations the image does not know refuses even when versions are equal; `min_upgrade_from` boundary (equal allowed, one below refused with `go_first`); `postgres_major` 18 vs 17; revision 1 vs 2 in both directions; space exactly equal to the need passes; the first refusing rule wins when two apply; no data and no installed version is `fresh`.
- [ ] A text test: the Dockerfile contains the `release.json` copy and an `ENV BRIEVIE_COMPOSE_REVISION=<n>` line, and `n` equals the `revision` constant the compose file carries (the compose file arrives in T8; until then the test reads `COMPOSE_REVISION` from `apps/updates/gate.py` and T8 extends it).
- [ ] Implement; `mypy --strict`; push; green.
- [ ] Commit: `feat(updates): pure start gate for compose installs`.

### T3: `InstalledVersion`

**Files:** `backend/apps/updates/models.py`, `services.py`, `migrations/00xx_installedversion.py`, `backend/tests/test_updates_installed_version.py`, `backend/tests/test_rls_roles.py` (extend, if the role matrix lives there).

**Interfaces:**
- `InstalledVersion(id uuid, version CharField(20), created_at, updated_at)`: at most one row (a unique constraint on a constant `singleton` boolean default true).
- `services.installed_version() -> Version | None`: the row's version, else the newest `ServerUpdate.to_version`, else `None`.
- `services.set_installed_version(version: Version) -> None` (`transaction.atomic`): upsert; refuses a lower version than the stored one (`ReleaseError("downgrade")`) unless called from `record_restore(version)`, which the restore command uses after a restore of an older backup (check `restore_database`/`verify_restore` for the right hook before wiring).
- Grants: `brievie_app` SELECT, INSERT, UPDATE; no DELETE. `brievie_backup` reads (it has `pg_read_all_data`).

- [ ] Failing tests (real PostgreSQL in CI, as the other model tests): empty gives `None`; fallback to the latest `ServerUpdate`; upsert; a lower version is refused; two rows impossible; the app role cannot DELETE (tested as that role, like the audit table).
- [ ] Migration; `make migrations` check job green (`Migrations match the models`).
- [ ] Commit: `feat(updates): record the version the database belongs to`.

### T4: `init_secrets`: the vault and the per-role volumes

**Files:** `backend/apps/installer/` (new app, registered in `INSTALLED_APPS`), `init_secrets.py`, `management/commands/init_secrets.py`, `backend/tests/test_installer_init_secrets.py`, `docs/SPEC.md` line.

**Interfaces:**
- `init_secrets.run(*, vault: Path, out: Mapping[str, Path], backup_config: Path, documents: Path, pgdata: Path, environ: Mapping[str, str], owners: Mapping[str, tuple[int, int]]) -> Report`: all paths are parameters (tests pass `tmp_path`; the command passes the container paths). It never reads the real `os.environ` itself and runs without Django models or a database (`requires_system_checks = []`).
- Files (S 4.4): `postgres_password`, `owner_password`, `app_password`, `backup_db_password` (24 random bytes, hex), `secret_key` (48 random bytes, base64), `master_key` (32 bytes, hex), `backup_password` (`crypto.derive_backup_password(master_key)` unless `BRIEVIE_BACKUP_PASSWORD(_FILE)` is set), `installation_id` (16 bytes, hex). Vault files `0400` owned by uid 10001 (`init` runs as that user and reads them; the keeper and api never mount the vault), directory `0700`, written through a temporary file and `rename`, never through a link (`apps.backup.privatefile` exists: reuse it).
- Per-role volumes: `out["postgres"]` gets `postgres_password` (owner uid 999); `out["api"]` gets `secret_key`, `app_password`, `master_key`; `out["keeper"]` gets `secret_key`, `app_password`, `backup_db_password`, `backup_password`. Owners 10001. A file is rewritten only when its content differs; files that no role needs any more are removed from the role volumes (never from the vault).
- **Refusals** (exit 1, Dutch sentence, nothing written): the vault is empty and data exists (`PG_VERSION` anywhere under `pgdata`, any regular file under `documents`, or `installation-id` in `backup-config`); `installation_id` in the vault differs from the one in `backup-config`; an environment value for a secret differs from the vault's (import mode; never silently a different key); a vault file is a link or has the wrong mode.
- **Import mode:** when `BRIEVIE_MASTER_KEY`, `SECRET_KEY`, `POSTGRES_PASSWORD`, `BRIEVIE_OWNER_PASSWORD`, `BRIEVIE_APP_PASSWORD`, `BRIEVIE_BACKUP_DB_PASSWORD` are in `environ` and the vault is empty, write them (validating: master key 64 hex, passwords hex or letters and digits only, secret key ≥ 50). `BRIEVIE_INSTALLATION_ID` too, and write it to `backup-config` only when that has none.
- Output: counts and Dutch sentences; **never** a value. A test captures stdout, stderr and the logging records and asserts that no secret substring occurs.

- [ ] Failing tests (all under `tmp_path`): first run makes every file with the right mode and owner map (owners are passed in, so the test needs no root); second run changes nothing (mtimes equal); the keeper directory has no `master_key`; the api directory has no `owner_password` or `postgres_password`; the derived back-up password equals `derive_backup_password`; each refusal above; import with matching values is a no-op, with a differing value refuses; a hostile symbolic link in place of a vault file refuses; a crash between two files (raise from a patched writer) leaves no half-written file and a second run completes.
- [ ] Implement; the command wires the real paths and `os.environ`.
- [ ] Push; green; commit: `feat(installer): make and place the secrets of a compose install`.

### T5: `prepare_server`, part 1: names, roles, database

**Files:** `backend/apps/installer/database.py`, `prepare.py`, `management/commands/prepare_server.py`, `backend/tests/test_installer_database.py`, `backend/tests/test_installer_hostname.py`.

**Interfaces:**
- `database.ensure_roles_and_database(superuser: Connection, passwords: Passwords) -> None`: exactly the effect of `deploy/postgres-init/01-roles.sh`, `database.psql` and `backup-role.psql` together, idempotent: `CREATE ROLE ... WHERE NOT EXISTS` then `ALTER ROLE ... PASSWORD` (so a changed vault value reaches the database), `CREATE DATABASE brievie OWNER brievie_owner` when missing, the `REVOKE`/`GRANT`/`ALTER DEFAULT PRIVILEGES` lines. Passwords go through psycopg parameters (`sql.Literal` is not allowed for passwords: use `ALTER ROLE ... PASSWORD %s` via a server-side `format` call or the `\gexec` pattern; the test checks that no statement text in `pg_stat_statements`-style logging holds the password).
- `prepare.check_hostname(name: str) -> HostnameCheck`: `ok`, `placeholder` (refuse), `loopback` (warn), `invalid` (refuse; letters, digits, dots, dashes only, as `init-env.sh`).
- Single source of truth: the SQL files stay where they are for the wrapper and the dev stack; a test reads both and asserts that the Python statements and the SQL files grant the same set of privileges (compare `\dp`-style output after running each against a scratch database in CI).

- [ ] Failing tests against the CI PostgreSQL: empty server gets the three roles and the database with the same attributes as `01-roles.sh` produces (compare `pg_roles` rows and `pg_database` owner); second run changes nothing; an existing wrapper database passes without a statement that alters data; roles' attributes are exactly `NOSUPERUSER NOBYPASSRLS` for owner/app and `BYPASSRLS` only for backup; `brievie_app` cannot create tables.
- [ ] Hostname tests: the example name refuses with the Dutch sentence, `localhost` warns, `my nas` refuses.
- [ ] Implement; commit: `feat(installer): create roles and database from the vault`.

### T6: `prepare_server`, part 2: gate, freeze, emergency back-up, migrate, record

**Files:** `backend/apps/installer/prepare.py`, `freeze.py` (new), `backend/tests/test_installer_prepare.py`, `backend/tests/test_installer_freeze.py`, `backend/tests/test_installer_prepare_failures.py`.

**Interfaces:**
- `prepare.run(...)` does S 4.5 steps 3 to 7 in order; steps 1 and 2 are T5. It builds `gate.decide_start` inputs from the live database (`unknown_migrations` as the wrapper's `unknown_migrations()` does, `database_major` from `server_version_num`, free space from `services.preflight()`, `installed_version()`).
- `freeze.freeze(superuser) -> None`: `ALTER DATABASE brievie SET default_transaction_read_only = on`, then `pg_terminate_backend` for every session of `brievie_app` (so the old api and worker reconnect read-only). `freeze.thaw(superuser)`: `ALTER DATABASE ... RESET default_transaction_read_only`. `migrate` runs as owner with `PGOPTIONS=-c default_transaction_read_only=off` for its own session only.
- Emergency back-up: same code path as `update_backup` of the wrapper (`manage.py update_backup --label pre-update-<version>`), called in-process or as a subprocess from `init`; destination `BRIEVIE_BACKUP_DIR`/`BRIEVIE_BACKUP_REPOSITORY` when set, else the `update-backup` volume with `backup_init --update-fallback`. Skipped for `fresh` and `same`.
- Failure paths (each a test): refusal by the gate changes nothing (no freeze, no row); back-up failure thaws and exits 1 ("De back-up vóór het bijwerken is mislukt. Er is niets veranderd."); migration failure before any migration applied thaws; failure after at least one migration applied stays frozen and prints the way back (the tag to return to, `restore pre-update-<version>`); a second run after a half-finished migration resumes (Django migrations are idempotent) and finishes only if the back-up of the first run exists (`label` check), else refuses.
- Output: Dutch sentences only; the setup token is not printed here.

- [ ] Failing tests with the real PostgreSQL in CI and a fake restic where the real one is not needed (the existing back-up tests show the pattern): fresh server; same version; update with a back-up; downgrade refused; jump too large refused with `go_first`; freeze makes an `INSERT` as `brievie_app` fail with `read only transaction` and a new session after `pg_terminate_backend` is read-only; thaw restores; the `ALTER DATABASE` setting is gone after every success path; failure matrix above.
- [ ] Add a test that a refusal prints no secret and no connection string.
- [ ] Implement; push; green; commit: `feat(installer): gate, freeze, back up, migrate and record at start`.

### T7: The proxy image and the release

**Files:** `deploy/Dockerfile.proxy` (new), `.github/workflows/release.yml`, `.github/workflows/ci.yml` (`image` job), `.github/scripts/release-check.py`, `backend/tests/test_release_check.py` (extend), `backend/tests/test_deploy_proxy.py` (new).

**Interfaces:**
- `deploy/Dockerfile.proxy`: `FROM caddy:2.11@sha256:<digest>` (pinned by digest like restic in `deploy/Dockerfile`), `COPY deploy/Caddyfile /etc/caddy/Caddyfile`, `COPY deploy/caddy-publish-root.sh /etc/caddy/publish-root.sh`, `CMD ["/bin/sh", "/etc/caddy/publish-root.sh"]`, `LABEL org.opencontainers.image.version`. Same user model as the compose service today (root; the script chowns the authority copy to 10001).
- `release.yml`: the `scan` and `publish` jobs build and scan `brievie-proxy` for both architectures next to the backend image, tag its digest with the version, attest it. A new step attaches `deploy/compose/docker-compose.yml` to the GitHub release as `docker-compose.yml`, and a second asset `docker-compose.pinned.yml` with every `image:` rewritten to `@sha256:<digest>` of what was just published.
- `release-check.py` additionally fails when the default in `${BRIEVIE_VERSION:-X.Y.Z}` of the compose file is not the tag's version, the two image lines disagree, or `BRIEVIE_COMPOSE_REVISION` in the compose file differs from the Dockerfile's `ENV`.

- [ ] Failing tests: `release-check.py` passes a consistent tree and fails on each of the three drifts (use the script's existing test harness); a text test pins the Caddyfile copy line and the digest form of the base image.
- [ ] CI `image` job lints and builds `Dockerfile.proxy` (hadolint as for the backend) and runs a built-in smoke test: `docker run --rm brievie/proxy:ci caddy validate --config /etc/caddy/Caddyfile` with the placeholder variables set.
- [ ] `workflow_dispatch` run of the release workflow proves checks, build and scan without publishing (as the existing comment says); read that run.
- [ ] Commit: `build(release): publish the proxy image and the compose file`.

### T8: The compose file, its example `.env`, the CI override and the parity test

**Files:** `deploy/compose/docker-compose.yml` (new), `deploy/compose/.env.example` (new), `deploy/compose/ci.override.yml` (new), `backend/tests/test_compose_file.py` (new), `backend/tests/test_compose_parity.py` (new).

**Interfaces:** the file of S 4.8, completed: all services of S 4.2, the networks of S 4.3, `x-backend` and `x-settings` anchors, log rotation, `cap_drop`/`no-new-privileges` on api, worker, keeper, the three `<--` regions, `admin` under `profiles: ["admin"]` with `command: ["python","manage.py","help"]`, and the EA tunnel profile as EA defines it. `.env.example` lists only the variables of S 4.9 with comments. `ci.override.yml` sets `image:` of the backend services and the proxy to `brievie/backend:ci` and `brievie/proxy:ci` and nothing else.

- [ ] Failing tests parse the YAML with PyYAML (already a dependency of the test suite? if not, use the standard library `tomllib`-free approach: `docker compose config` output compared in CI; the unit test then only covers text rules): no line contains a value of the form of a secret (hex of 48+ characters, base64 of 60+), no `container_name`, no `latest` or `edge` tag, no `ports:` on `postgres`, `api`, `worker`, `keeper`, every `image:` uses `${BRIEVIE_VERSION:-<x>}` with one default, `postgres` is only on `db`, `db` is `internal: true`, the keeper mounts `secrets-keeper` and not `secrets` or `secrets-api`, the api mounts neither the vault nor `secrets-keeper`.
- [ ] `test_compose_parity`: for every service in both files, the same service names (the compose-only file has `init-secrets` and `init` instead of `migrate`, listed explicitly), the same set of environment keys after mapping each `X_FILE` to `X`, the same published ports, the same `depends_on` conditions, the same `healthcheck.test` for the api, the same Caddy environment. The Caddyfile in `Dockerfile.proxy` is `deploy/Caddyfile` (checked by T7's test). A failing example is committed as a test fixture to prove the test can fail.
- [ ] `compose-oldest` target version: choose the oldest Compose that supports every feature used (ankers and merge keys are YAML; `depends_on.condition`, `profiles`, `network_mode: none`) and record the version in `docs/DECISIONS.md`.
- [ ] Commit: `feat(deploy): compose file that runs from the published images`.

### T9: The recovery sheet without a terminal

**Files:** `backend/apps/backup/api.py` (new), `backend/apps/backup/schemas.py`, `backend/apps/backup/sheet.py` (a seam for the api container, which has the master key but no destination mounts), `backend/apps/audit/codes.py`, `backend/tests/test_backup_sheet_api.py`, `web/src/routes/recovery-sheet.tsx`, `web/src/routes/setup-summary.tsx`, `web/src/routes/recovery-sheet.test.tsx`, both catalogues, `docs/api/openapi.json`, `web/src/api/schema.d.ts`.

**Interfaces:**
- `POST /api/v1/server/recovery-sheet` `operation_id` `createRecoverySheet`, `auth=session_auth`, server admins only (`_server_admin` pattern), requires `reauthenticate` within 15 minutes (the same check `createPairingCode` uses), `Cache-Control: no-store`, `Content-Type: text/html; charset=utf-8`, `Content-Disposition: attachment; filename="brievie-herstelblad.html"`. It answers with `sheet.render_sheet(sheet.sheet_data())`. A GET would be cached or logged by proxies: POST only.
- Errors: 401 `reauthentication_needed`, 403 `forbidden`; both Dutch.
- `sheet.sheet_data()` today reads `settings.BRIEVIE_*` and `_destination()`; check that it works in the api container (it has the master key and `BRIEVIE_HOSTNAME`; the destination fields come from the database or are empty there) and add a test with the api's environment.
- Audit: `recovery_sheet_viewed`, no values, the actor is the admin.
- Web: page "Herstelblad" in the server section (server admins only): one explanation in B1 Dutch, a button "Herstelblad downloaden", after reauthentication, with the same warning text as the file's. `setup-summary.tsx`: the first-setup summary replaces its line about `brievie-herstelblad.html` and `./brievie recovery-sheet` by the button (D3).
- No log line names the sheet's content; the endpoint does not echo the key in an error.

- [ ] Failing tests: 401 without a session, 401 with a device token, 403 for a non-admin, 401 `reauthentication_needed` after 16 minutes, 200 with the sheet text containing the grouped master key and the right headers, one audit row, no key in the captured log records.
- [ ] Web tests (queries by role): the button downloads, an expired reauthentication asks for the password, a non-admin does not see the entry.
- [ ] `make openapi`, `pnpm gen:api`, `pnpm extract`, translate, commit all generated files.
- [ ] Commit: `feat(backup): recovery sheet from the web interface`.

### T10: Nightly back-up, first-time set-up and a status panel

> Built only when Stijn says yes to D2; otherwise this task shrinks to the documented `run` commands in T13.

**Files:** `backend/apps/backup/jobs.py`, `backend/apps/backup/api.py`, `backend/apps/backup/services.py`, `backend/apps/installer/prepare.py` (auto-init), `backend/tests/test_backup_schedule.py`, `backend/tests/test_backup_status_api.py`, `backend/tests/test_installer_auto_init.py`, `web/src/routes/backup.tsx` and test, catalogues, OpenAPI files.

**Interfaces:**
- `@app.periodic(cron="30 2 * * *") async def nightly_backup(...)`: on the `backup` queue; does nothing when no destination is configured or the last run is under 20 hours old; otherwise defers the same job `run_backup` that `cmd_backup` defers. The queueing lock of `retry_stalled_backup_jobs` is reused so two keepers never run two. The cron hour is `BRIEVIE_BACKUP_HOUR` (0 to 23, default 2).
- `GET /api/v1/server/backup-status` (`getBackupStatus`) and `POST /api/v1/server/backup-runs` (`createBackupRun`): `session_auth`, server admins; the first returns last run (time, ok, size), destination kind (`folder`, `restic`, `none`), reachable (bool), marker present, last check; the second enqueues one run and returns 202 `{runId}` (409 `backup_running` while one is). No path, key, credential or repository URL in any response (a test greps the JSON for the configured values).
- `BRIEVIE_BACKUP_AUTO_INIT=true`: `prepare_server` runs `backup_init` once when `BRIEVIE_BACKUP_DIR` is a mounted folder that is empty, has no marker, and is not on the data disk (the same best-effort check as `--init`); never for a folder that already has a marker; never with `--allow-same-disk`.
- Web: panel "Back-up" in the server section: status line, "Nu back-uppen" button, a plain sentence when nothing is set up with the three lines to put in the compose file. Text in B1.

- [ ] Failing tests: the periodic task skips without a destination, skips within 20 hours, defers once otherwise; status JSON has no secrets; `createBackupRun` is 409 while running; auto-init only in the safe case (three refusals tested); device token gets 401.
- [ ] `make openapi`, `pnpm gen:api`, `pnpm extract`; commit generated files.
- [ ] Commit: `feat(backup): nightly back-up, status panel and first-time set-up`.

### T11: Moving from a wrapper install

**Files:** `backend/tests/test_installer_adopt.py`, `.github/workflows/ci.yml` (job `adopt`), `docs/SELFHOST.md` (T13 text).

**Interfaces:** nothing new in code: the import mode (T4) and the adopt rule (T2/T6) already exist; this task proves the whole path.

- [ ] Unit tests (under `tmp_path`): a `.env`-shaped environment imports; the vault equals the `.env` values; `backup-config/installation-id` equals `BRIEVIE_INSTALLATION_ID`; `prepare_server` with no `InstalledVersion` and data requires `BRIEVIE_ADOPT_VERSION` equal to the image and writes the row.
- [ ] CI job `adopt`: start the wrapper stack from the checkout (`init-env.sh`, `docker compose ... up --build`), create a user and a document through the API, record the Caddy CA fingerprint, `stop`, then `docker compose --env-file deploy/.env -f deploy/compose/docker-compose.yml -f deploy/compose/ci.override.yml up -d --wait`, then assert: the API answers, the same CA fingerprint, the user can sign in, the document is readable (the master key was imported, not regenerated), `init` exited 0, the vault files exist, the installation code is **not** printed again (the server is already set up). Same `RUNNER_ENVIRONMENT` guard as `update`.
- [ ] Commit: `test(installer): moving from the wrapper keeps keys, data and the CA`.

### T12: CI jobs for the compose file

**Files:** `.github/workflows/ci.yml`, `.github/scripts/update-releases.py` (extend), `.github/scripts/compose-secrets-check.sh` (new), `.github/scripts/restore-check.sh` (extend).

**Interfaces:** jobs of S 4.13.
- `compose-stack`: build both images to the `ci` tags, `$COMPOSE up -d --wait --wait-timeout 600` with `-f deploy/compose/docker-compose.yml -f deploy/compose/ci.override.yml` and **no** `deploy/.env`; set `BRIEVIE_HOSTNAME=brievie.test` and the ports through the job's environment. Steps: the API answers; version equals `pyproject`; the web interface is served; worker and keeper running; the installation code printed exactly once; `compose-secrets-check.sh`: every file in the vault has mode `400` (`stat -c %a`), the keeper's volume has no `master_key`, the api's volume has no `owner_password`, and the contents of every secret file do not occur in `$COMPOSE config`, in `docker inspect` of every container, in any `logs` output, or in the `ps` command line of any process in any container (`ps` through `docker top`); a restart of the stack keeps every file's checksum (nothing regenerated).
- `compose-oldest`: `docker/setup-compose-action` pinned to the version chosen in T8; `config` and `up -d --wait` from the same override; fails the job on any compose warning about an unknown key.
- `update` (extend): a second matrix leg `via=compose`: install release A with the compose file, update to B by changing `BRIEVIE_VERSION` (the throwaway releases of `update-releases.py` build `brievie/backend:<x>` and `brievie/proxy:<x>` locally and the override maps tags); assert the emergency back-up row (`pre-update-B`), `record_update`, the pages, the paired device and the CA; then B to A (a downgrade) must exit non-zero in `init`, **the B containers keep running** (the claim of S 4.6: if this fails, change S 4.6 and the page), data intact; then a jump past `min_upgrade_from` (a throwaway release C with `min_upgrade_from` above A) is refused the same way; then a failing migration (a throwaway release D whose migration raises) leaves the database frozen with the way back printed, and `restore pre-update-B` followed by version B brings the data back.
- `restore` (extend): the same job with a compose-only installation: back-up to the folder, `down --volumes` except the folder, `up -d`, `admin restore`, pages and CA back.

- [ ] Every job runs only on GitHub-hosted runners (the existing guard) and ends with `down --volumes`.
- [ ] Failing-first: land each job with the deliberately broken fixture it must catch (a secret echoed into a log; a keeper volume with the master key), watch the job fail, then remove the fixture.
- [ ] Commit per job: `ci: test the compose-only install`, `ci: update and restore through the compose file`.

### T13: Docs and decisions

**Files:** `docs/SELFHOST.md`, `README.md`, `docs/DECISIONS.md`, `CHANGELOG.md`, `docs/SPEC.md`, `docs/PRIVACY.md`, `SECURITY.md` if it names the install, `CLAUDE.md` (two lines: the compose file and `_FILE`).

- [ ] `SELFHOST.md`: new section "Installing with only a compose file" (what S 2 and 4 say, in the existing voice, "Not verified yet" list with Synology, Unraid, Portainer, Dockge, Podman, Windows, Raspberry Pi), changes to sections 1, 5, 11, 21 ("Images from the registry" no longer says "comes later"), and the table of S 4.7 as "Without a terminal".
- [ ] `DECISIONS.md` (one line each): the proxy image (D1) and the alternative rejected; `_FILE`; the vault and role volumes (D4); the freeze (D5); `BRIEVIE_COMPOSE_REVISION` outside `release.json`; the oldest supported Compose; wrapper and compose-only side by side and phase 2; D6.
- [ ] `PRIVACY.md`: where the master key lives now (vault volume on the same disk) and what a theft of the disk or a backup exposes (unchanged from `deploy/.env`).
- [ ] `CHANGELOG.md` entry under the next version.
- [ ] Commit: `docs: install with only a compose file`.

---

## Site tasks (`stijnvandepol/Brievie_site`, a separate repository and branch)

Static HTML (`nl/` and `en/`), no build step except `tools/releases/generate.py`. Start only when T7 has published a release with the compose file (the page's block is stamped from that release) and keep every untested platform labelled "Niet getest" / "Untested".

### S1: Stamp the compose file into the page

**Files:** `tools/releases/generate.py`, `tools/releases/test_generate.py`, `tools/releases/fixtures/releases.json`, `nl/install/index.html`, `en/install/index.html`.

- [ ] Failing test first: the generator fetches the newest release's `docker-compose.yml` asset (fixture), places it between two markers in both pages, and fails the build when the asset is missing, when it holds a line that looks like a secret, or when its default version is not the release's. The checked-in pages hold the last stamped text so a rebuild without network keeps working.
- [ ] Commit: `feat(install): stamp the compose file of the newest release into the page`.

### S2: Restructure the install page (nl, then en)

**Files:** `nl/install/index.html`, `en/install/index.html`, `docs/install-page-notes.md`, `home.css` or `styles.css` for the tables (no new dependency; existing button style for the copy button; `site.js` already handles copy buttons for `install.sh`).

Structure (the order of S 5 in the spec): 1 requirements; 2 Docker Compose with the stamped block, a copy button and three numbered steps that each end in something visible; 3 tables "Variabelen", "Poorten", "Volumes" with a collapsed "Alle variabelen"; 4 one sentence on why there is no `docker run`; 5 per platform (Synology Container Manager, Portainer, Dockge, Unraid, Linux, Windows and macOS with Docker Desktop, Podman as "niet ondersteund"), each with prerequisites, steps with what you see, good to know, and a Getest/Niet getest label; 6 first run; 7 update; 8 back-up and restore; 9 handy commands; 10 troubleshooting one problem per heading; 11 moving from `install.sh`/the wrapper and uninstalling; 12 reaching Brievie from outside (EA's sections, same variables).

- [ ] The old `#synology-titel`, `#docker-titel`, `#linux-titel`, `#windows-titel`, `#mac-titel`, `#installeren` and `#handmatig` anchors keep working (redirect list or duplicated `id`s); a link-check script (the one the site already runs, or a ten-line Python check added to `tools/`) fails on a dead fragment.
- [ ] `install.sh` stays as the second route ("met een git-checkout en `./brievie`") and is described as such; its text changes only where the page now calls it the second route.
- [ ] `docs/install-page-notes.md`: add a section "Compose as the main route (2026-11-23)" that records the two example pages' patterns (spec chapter 2), says that the ruling "Synology needs ready-made images that do not exist yet" is superseded by the published images, and keeps the "never SSH on a Synology" ruling (Container Manager is not SSH).
- [ ] Synology section: ports 80 and 443 belong to DSM, choose others, and the port becomes part of the address in the QR code; back-up folder as a shared folder with uid 10001; scheduled task for `restore`: all "Niet getest".
- [ ] Both languages through the existing `lang.js` pattern; copy reviewed in B1 Dutch.
- [ ] Commit: `docs(install): Docker Compose as the main way to install`.

### S3: Page checks

- [ ] The site's existing HTML checks (security headers, CSP: no inline script; the copy button uses `site.js`), width 360 px: the compose block scrolls inside its box and does not widen the page; tables become stacked rows on a phone.
- [ ] A test (Python, in `tools/`) that every `docker compose` command shown on the page also appears in `docs/SELFHOST.md` of the host (fetch at build time from the tag) so the two never differ.
- [ ] Commit: `test(install): the page and SELFHOST agree on the commands`.

### S4: Releases page and README

- [ ] The releases page links the release's `docker-compose.yml` and `docker-compose.pinned.yml` assets and shows the `gh attestation verify` line; the root README's install line points to the Docker Compose section first.
- [ ] Commit: `docs: link the compose assets from the releases page`.

---

## Order and coordination

1. T1, T2, T3 in parallel (independent), then T4, T5, T6 in that order. T7 and T8 can start once T4 to T6 interfaces are fixed; T9 and T10 are independent of them (T10 after D2). T11 and T12 after T8. T13 last. Site tasks after the first release carrying the compose file.
2. Verify, for every task, with a push and a read of the named CI job, never locally with Docker.
3. After the last task, run the whole of CI once with `down --volumes` guards, then do one manual try on a real Synology before the page loses its "Niet getest" labels (D6).
