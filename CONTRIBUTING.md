# Contributing to Brievie host

Thank you for looking. Brievie keeps people's letters (tax, health, debts), so changes
are reviewed with that in mind: small, tested, and explained.

- **A security problem?** Do not open an issue or pull request. Follow
  [SECURITY.md](SECURITY.md).
- **A bug or an idea?** Open an issue first for anything larger than a small fix, so we
  can agree on the approach before you spend time on it.

## What to read first

- `docs/SPEC.md`: the product (in Dutch). Read sections 11, 12, 14, 18 and 19 before
  changing the architecture.
- `docs/DECISIONS.md`: what was chosen and why. A change that goes against a decision
  says so and adds a new one.
- `docs/GLOSSARY.md`: the Dutch and English names of the same things.
- `docs/PRIVACY.md`: what the server promises. Code that breaks a promise there is a bug.

## Set-up

You need Docker with Compose v2, [uv](https://docs.astral.sh/uv/) (it installs Python
3.13), and for the web interface Node 24 or newer with pnpm (the version is in
`web/package.json`).

```sh
make dev           # creates deploy/.env with random secrets, starts PostgreSQL 18 on 127.0.0.1
make migrate       # applies migrations as the owner role
make web-install   # installs the web interface's packages
cd web && pnpm dev # the web interface, proxied to a running stack on https://localhost
```

`make dev` never changes an existing `deploy/.env`. To run the whole server the way a
user does, use `./brievie start` (see the README).

## Commands

| Command | What it does |
|---|---|
| `make dev` | Start PostgreSQL for development and tests |
| `make down` | Stop it (the data volume stays) |
| `make migrate` | Apply migrations as the owner role |
| `make test` | Backend tests (pytest, as the non-superuser owner role; needs `make dev`) |
| `make lint` | `ruff check` and `ruff format --check` |
| `make format` | Fix what ruff can fix, and format |
| `make typecheck` | `mypy --strict` |
| `make openapi` | Write `docs/api/openapi.json`, the contract with the iOS app |
| `make web-test` | Web tests (Vitest) |
| `make web-lint` | Biome and `tsc --noEmit` |
| `make web-build` | Type check and production build |
| `make web-api` | Regenerate `web/src/api/schema.d.ts` after `make openapi` |
| `make web-i18n` | Update the Dutch text catalogue after changing visible text |

## The rules in short

The full list is in `CLAUDE.md`; these are the ones a review checks first.

- Code, comments, commits, table and field names in English. Text a user sees is Dutch
  at B1 level: in the backend through `gettext`, in `web/` through Lingui (`t` macros,
  then `make web-i18n`).
- Every table has a UUID `id`, `created_at` and `updated_at`. Every workspace content
  table subclasses `WorkspaceModel` and has row-level security enabled **and forced**.
- `mypy --strict` and `ruff check` clean. No `Any`, no bare `except`.
- The API lives under `/api/v1` and only grows: a field added to an existing schema has a
  default, nullable fields are primitives, every endpoint has typed schemas and an
  explicit camelCase `operation_id`. Every error body is
  `{"code": "<snake_case>", "message": "<Dutch sentence>"}`.
- Endpoints are thin: validate, call one function in the app's `services.py`, return.
  Every mutating service function runs in `transaction.atomic`.
- No document content, tokens, passwords or BSNs in logs. Text is BSN-masked before it is
  stored, logged or sent to an AI service; document files are sealed before they touch
  disk. Text goes to an external AI service only with a recorded consent.
- No new dependency without a line in `docs/DECISIONS.md`.
- Tests first for logic.

## Before you open a pull request

```sh
make lint typecheck test
cd web && pnpm lint && pnpm typecheck && pnpm test && pnpm build
```

After an API change also run `make openapi` and `make web-api` and commit both files;
after changing visible text run `make web-i18n`. CI fails when either is out of date.

## How a change is reviewed

1. Open a pull request against `main` and fill in the template: what changed, how you
   checked it, and what you did **not** check.
2. CI must pass. It runs the backend and web checks, the dependency audits, the secret
   scan, the workflow linter, the image scan, the full stack, the back-up and restore
   proof and a ZAP scan; CodeQL runs alongside. See the README for one line per job.
3. The code owner (`.github/CODEOWNERS`) reviews it. The review asks: does it keep the
   promises in `docs/PRIVACY.md`, does it follow the rules above, is it tested, and is a
   decision recorded where one was made.
4. Questions are answered in the pull request; changes are pushed as new commits.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), in English, in the
imperative:

```
feat: add a deadline to a task
fix(web): the only admin is not offered leaving
docs: what a restore brings back
test: a member's whole life over HTTP
ci: pin every action to a commit
chore: allow a made-up test value in gitleaks
```

Common types: `feat`, `fix`, `docs`, `test`, `refactor`, `ci`, `chore`. A scope such as
`(web)` is optional. Explain the why in the body when the subject cannot.

## Licence

Brievie host is licensed under the GNU Affero General Public License v3.0 or later. A
contribution you submit is licensed under the same terms (GitHub's terms of service
already say so for contributions to a public repository).
