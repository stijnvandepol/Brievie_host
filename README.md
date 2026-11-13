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

`deploy/.env` holds local passwords; change every `change-me` value before you run
Brievie anywhere but on your own machine.

## Run the whole stack

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
