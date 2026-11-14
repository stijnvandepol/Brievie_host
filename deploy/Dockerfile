# syntax=docker/dockerfile:1

# Build stage only: Node never ends up in the image that runs.
FROM node:26.13-slim AS web
# Node 25+ no longer ships corepack; the pnpm version is the one in web/package.json.
RUN npm install --global pnpm@10.30.3
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

# Build stage: uv and the resolved virtualenv. Only /opt/venv is copied on, so the
# running image has neither the uv binary nor its caches.
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# backend/tests is left out by .dockerignore.
COPY backend/ ./
# The built web app: WhiteNoise serves its files, apps.common.web its index.html.
COPY --from=web /web/dist ./webdist
# Build-time only: the real SECRET_KEY comes from the environment at run time.
RUN DATABASE_URL=postgres://build@localhost/build \
    DATABASE_OWNER_URL=postgres://build@localhost/build \
    SECRET_KEY=collectstatic-only-not-a-real-key-collectstatic-only-not-a-real-key \
    python manage.py collectstatic --noinput

RUN useradd --system --create-home --uid 10001 brievie \
    # The documents volume takes its owner from this directory when it is first created.
    && mkdir -p /data/documents && chown brievie:brievie /data/documents
USER brievie

EXPOSE 8000
# Serves as the app role. Migrations run in the separate one-shot "migrate" service.
# Before gunicorn starts, prepare_setup_token prints the first-run installation code
# once (only while the server is not set up and BRIEVIE_SETUP_TOKEN is unset).
CMD ["sh", "-c", "python manage.py check_master_key && python manage.py prepare_setup_token && exec gunicorn brievie.wsgi:application --bind 0.0.0.0:8000 --workers 2"]
