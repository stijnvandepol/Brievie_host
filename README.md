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
curl http://127.0.0.1:8000/api/v1/health
```

A one-shot `migrate` container applies migrations as the `brievie_owner` role and
exits; the `api` container starts after it and serves requests as the `brievie_app`
role, without the owner credentials. The cookies are `Secure` by default; for local
development over plain http set `SESSION_COOKIE_SECURE=false` and
`CSRF_COOKIE_SECURE=false` in `deploy/.env`.
