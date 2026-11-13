# Brievie host

Brievie keeps, explains and tracks the mail of a household or a small business. This
repository is the server that runs at home or at the office; the iOS app lives in
`Brievie_ios`. See `docs/SPEC.md` for the full product description.

## Requirements

- Docker with Compose v2+
- [uv](https://docs.astral.sh/uv/) for local development (it installs Python 3.13)

## Run locally

```sh
make dev          # PostgreSQL 18 on 127.0.0.1:5432, creates deploy/.env
make migrate      # apply migrations
make test lint typecheck
```

`deploy/.env` holds local passwords. With `DEBUG=false` (the default) the server refuses to
start while `SECRET_KEY` is the placeholder or shorter than 50 characters; the database
passwords only produce a warning (`manage.py check`), because an existing database keeps
the password it was created with. Replace every `change-me` value before you run Brievie
anywhere but on your own machine (see below).

## Run the whole stack

1. Create your settings file:

   ```sh
   cp deploy/.env.example deploy/.env
   ```

2. Replace the `change-me` values in `deploy/.env` with real secrets. One command per
   value; paste each output after the `=` of its line:

   ```sh
   openssl rand -base64 48   # SECRET_KEY (at least 50 characters)
   openssl rand -hex 24      # POSTGRES_PASSWORD
   openssl rand -hex 24      # BRIEVIE_OWNER_PASSWORD
   openssl rand -hex 24      # BRIEVIE_APP_PASSWORD
   ```

   Also copy the two database passwords into `DATABASE_URL` and `DATABASE_OWNER_URL`
   (only used when you run the backend outside Docker), and set `BRIEVIE_HOSTNAME`
   (next section). The database passwords are set when the database is created: changing
   them later in `deploy/.env` does not change a database that already exists.

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
choose "App koppelen", make a code and scan the QR code with the app. Does scanning fail?
Use "Kopieer koppelgegevens" and paste the text in the app.

**The private certificate.** Caddy makes its own certificate authority (CA) and signs the
server certificate with it, because a home server has no public name that a public CA could
vouch for. Your browser will warn about it: that is expected. The QR code carries the
fingerprint of that CA, and the app only talks to a server whose certificate chain ends in
exactly that CA. You do not install anything on the phone, and nobody else can pose as your
server without the CA's private key (which stays in the Caddy volume).

## Web interface

`https://localhost` serves the web app (setup, login, workspaces, pairing the iOS app).
It lives in `web/` and is built into the API image; there is no Node in the running
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
