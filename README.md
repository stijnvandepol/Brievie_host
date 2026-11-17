# Brievie host

Brievie keeps, explains and tracks the mail of a household or a small business. This
repository is the server that runs at home or at the office; the iOS app lives in
`Brievie_ios`. See `docs/SPEC.md` for the full product description.

## Requirements

- Docker with Compose v2+
- [uv](https://docs.astral.sh/uv/) for local development (it installs Python 3.13)

## Run locally

```sh
make dev          # creates deploy/.env with random secrets, starts PostgreSQL 18 on 127.0.0.1:5432
make migrate      # apply migrations
make test lint typecheck
```

`make dev` runs `deploy/init-env.sh`, which creates `deploy/.env` from
`deploy/.env.example` **only if it does not exist yet**, with freshly generated random
values for `SECRET_KEY` and the database passwords (file mode 600, nothing is printed). It
never changes an existing `deploy/.env`. The example file itself holds placeholders and is
refused by the server: with `DEBUG=false` (the default) it will not start while
`SECRET_KEY` is a placeholder or shorter than 50 characters, and `manage.py check` warns
about placeholder database passwords.

The database passwords are fixed when PostgreSQL first creates its volume. So
`deploy/.env` must exist **before the first** `docker compose up` (or `make dev`). If the
volume was created with other passwords (for example from an older `.env`), either put
those passwords back in `deploy/.env`, change them in PostgreSQL (`ALTER ROLE ... PASSWORD`),
or delete the volume (`docker compose ... down -v`, this deletes all data) and start again.

## Run the whole stack

1. Create your settings file (skips if `deploy/.env` exists; secrets are generated):

   ```sh
   sh deploy/init-env.sh
   ```

2. Set `BRIEVIE_HOSTNAME` in `deploy/.env` (see "iPhone koppelen" below).

3. Start everything:

   ```sh
   docker compose --project-directory deploy -f deploy/docker-compose.yml up -d --build
   ```

Caddy is the only service with public ports (443, and 80 for the redirect). It serves
`https://$BRIEVIE_HOSTNAME` (default `localhost`) with a certificate from its own
private CA. To call the API with curl, copy the root certificate out first:

```sh
docker compose --project-directory deploy -f deploy/docker-compose.yml \
  cp caddy:/data/caddy/pki/authorities/local/root.crt root.crt
curl --cacert root.crt https://localhost/api/v1/health
```

A one-shot `migrate` container applies migrations as the `brievie_owner` role and
exits; the `api` container starts after it and serves requests as the `brievie_app`
role, without the owner credentials.

## Documents and AI (phase 1b)

The app uploads a scan (page images plus the text it recognised on the phone). The
`worker` container processes it: it masks BSNs, optionally asks an AI service what the
letter says, checks every answer against the letter text, and offers the result for
review in the app. Nothing becomes a task until you confirm it there.

A new workspace has AI **off**: you fill in the review by hand. To let an AI service
read your letters (only the text, with BSNs masked; never the page images), an
administrator opens **AI** in the web interface, chooses the service, pastes the API
key and agrees to the notice that says what leaves the server. The same from a
terminal:

```sh
# The workspace id is in the address bar of the web interface, or in GET /api/v1/workspaces.
docker compose --project-directory deploy -f deploy/docker-compose.yml exec api \
  python manage.py configure_ai --workspace <workspace-id> --mode external
```

It first tells you, in Dutch, what leaves your server with every letter and to which
service and country, and asks you to type `akkoord`: you decide this for everyone whose
post is in the workspace. Your answer is recorded (who, when, which service), and every
member sees in the web interface that AI is on. In a script, pass `--accept-external-ai`
instead of typing. Another model at the same address does not ask again; another
address does. `--mode off` switches it off again and records that too.

It then asks for the API key (or reads `BRIEVIE_AI_API_KEY`) and defaults to Mistral
(`https://api.mistral.ai/v1`, model `mistral-small-latest`). Choose another supplier with
`--supplier`:

```sh
... configure_ai --workspace <workspace-id> --mode external --supplier claude
... configure_ai --workspace <workspace-id> --mode external --supplier openai --model <name>
```

`claude` defaults to model `claude-opus-5-5` (`--model claude-haiku-5-5` is cheaper);
`openai` has no default model, so `--model` is required. Any other OpenAI-compatible
service works with `--base-url ... --model ...`. `--mode local --base-url http://ollama:11434/v1
--model <name>` uses a model on your own network, `--mode off` switches AI off.

`BRIEVIE_MASTER_KEY` in `deploy/.env` encrypts the documents. `sh deploy/init-env.sh`
creates it (also in an existing `.env`). **Keep a copy in a safe place**: without it the
stored documents cannot be read. See [docs/PRIVACY.md](docs/PRIVACY.md).

## Ordering post (phase 1c)

Post is on the name of a person, carries labels, and can be ordered by rules. A new
workspace gets a start set of labels and rules (Thuis or Zakelijk). A label or person a
rule or the AI proposes is a suggestion you confirm; switch a rule to automatic and its
changes are made for you, shown in the document's history and undoable. The web interface
has the inbox, persons, labels, the tab Automatisering (rule builder with a test) and a
task list. With AI `external` or `local` the names of your labels and persons go along
with the letter text, see [docs/PRIVACY.md](docs/PRIVACY.md).

## iPhone koppelen

The app reaches the server over your network, so `BRIEVIE_HOSTNAME` in `deploy/.env` must
be a name or IP address **the phone** can reach, not `localhost` (on the phone that is the
phone itself). Examples:

- the fixed IP address of the computer that runs Brievie: `BRIEVIE_HOSTNAME=192.168.1.20`
- a name on your network: `BRIEVIE_HOSTNAME=brievie.local` (or the name of your Mac or NAS)

That one value is enough: `ALLOWED_HOSTS` and the addresses in the pairing QR code
(`BRIEVIE_PUBLIC_URLS`) follow from it. Set them yourself only when you need more than
one address; the server refuses to start if a public address is not in `ALLOWED_HOSTS`.
Give the computer a fixed address in your router, so the address in the QR code stays
valid. After changing `BRIEVIE_HOSTNAME`, run `docker compose ... up -d` again.

The phone must be on the same network (Wi-Fi) as the server. Then open the web interface,
choose "Apparaten" in the menu, make a code ("Koppelcode maken") and scan the QR code
with the app. Does scanning fail? Use "Kopieer koppelgegevens" and paste the text in the
app.

**The private certificate.** Caddy makes its own certificate authority (CA) and signs the
server certificate with it, because a home server has no public name that a public CA could
vouch for. Your browser will warn about it: that is expected. The QR code carries the
fingerprint of that CA, and the app only talks to a server whose certificate chain ends in
exactly that CA. You do not install anything on the phone, and nobody else can pose as your
server without the CA's private key (which stays in the Caddy volume).

## Web interface

`https://localhost` serves the web app: setup, login, workspaces, inbox, post, persons,
labels, Automatisering and pairing the iOS app. It follows the Fluent 2 design language
(the calm, dense look of Microsoft 365 admin and Outlook on the web) with Brievie's own
name, mark and navy app bar: a navigation pane on the left that folds to icons (the menu
button; below 1200 px it is icons by default, on a phone it opens as a drawer), a page
header with a command bar, and tables for lists. Colours, type and spacing are tokens at
the top of `web/src/styles.css`; screenshots are in `docs/ui/fluent/`. It lives in `web/` and is built into the API image; there is no Node in the running
stack. For development you need Node 24+ and pnpm: `make web-install`, then
`cd web && pnpm dev` (proxies `/api` to the running stack; `BRIEVIE_DEV_API` changes the
target). `make web-test`, `make web-lint` and `make web-build` are the checks. After
`make openapi` run `make web-api`; after changing visible text run `make web-i18n`.

## First setup: the installation code

`POST /api/v1/setup` creates the first administrator and needs an installation code,
because whoever sets up the server first becomes its administrator. Either set
`BRIEVIE_SETUP_TOKEN` in `deploy/.env`, or leave it empty: the `api` container then
generates a code at its first start and prints it once in its log:

```sh
docker compose --project-directory deploy -f deploy/docker-compose.yml logs api | grep Installatiecode
```

Lost it? `docker compose ... exec api python manage.py prepare_setup_token --new`
prints a new one (the old one stops working). After setup the code is deleted.

## Back-up

Brievie can back up the database, the document files and the server's certificate
authority to an external disk, a NAS share, a restic REST server or S3-compatible
storage, encrypted. The master key is never in a back-up: keep the recovery sheet
(`./brievie recovery-sheet`). Set-up, restore and advice for organisations are in
[docs/SELFHOST.md](docs/SELFHOST.md). A nightly schedule comes in a later version; for
now run `./brievie backup` yourself.

## Licence

Brievie host is free software: you can redistribute it and/or modify it under the terms of
the GNU Affero General Public License as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version. It comes without any
warranty. The full text is in `LICENSE`.

If you run a modified version as a service for others, the AGPL requires you to offer
those users the source of your version.
