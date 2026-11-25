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
# Pinned by index digest (amd64 and arm64); Dependabot's docker updates raise it.
FROM python:3.13-slim@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50

# BRIEVIE_IN_CONTAINER: the destructive commands (restore, sweep --delete, back-up
# init) refuse to run anywhere else (safepaths.require_container).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BRIEVIE_IN_CONTAINER=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Back-up tools (the keeper and admin services run them; DECISIONS 2026-11-17 B1).
# restic: the static binary of the official image, pinned by version and digest.
COPY --from=restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510 \
    /usr/bin/restic /usr/local/bin/restic
# pg_dump/pg_restore from PGDG, the same major as the server (postgres:18 in compose;
# test_pg_client_major_matches_the_server). The signing key is pinned by checksum.
# The same layer installs Debian's pending security updates (the base image lags behind
# them between its rebuilds) and removes the base image's pip: nothing runs it in this
# image (the app's packages are in /opt/venv from the builder stage, whose python is
# first on PATH, hence the full path), and it ships vendored packages of its own
# (urllib3, msgpack, setuptools) with their own vulnerabilities.
# Tesseract with its Dutch data reads uploaded files without a text layer (intake,
# DECISIONS 2026-11-25); Debian's package, so the update above patches it too.
ADD --checksum=sha256:0144068502a1eddd2a0280ede10ef607d1ec592ce819940991203941564e8e76 --chmod=0644 \
    https://www.postgresql.org/media/keys/ACCC4CF8.asc /usr/share/keyrings/pgdg.asc
RUN . /etc/os-release \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends postgresql-client-18 tesseract-ocr tesseract-ocr-nld \
    && rm -rf /var/lib/apt/lists/* \
    && /usr/local/bin/python3 -m pip uninstall --yes pip

# backend/tests is left out by .dockerignore.
COPY backend/ ./
# The release data of this version (the tag's deploy/release.json, byte for byte). A compose
# install checks it at start (apps.updates.gate); the wrapper ignores it. The image sets no
# variable that marks a compose install: only the standalone compose file does, so a
# ./brievie install never has the web recovery sheet (tests/test_compose_flag.py).
COPY deploy/release.json /app/release.json
# The built web app: WhiteNoise serves its files, apps.common.web its index.html.
COPY --from=web /web/dist ./webdist
# Build-time only: the real SECRET_KEY comes from the environment at run time.
RUN DATABASE_URL=postgres://build@localhost/build \
    DATABASE_OWNER_URL=postgres://build@localhost/build \
    SECRET_KEY=collectstatic-only-not-a-real-key-collectstatic-only-not-a-real-key \
    python manage.py collectstatic --noinput

RUN useradd --system --create-home --uid 10001 brievie \
    # A volume takes its owner from this directory when it is first created. The
    # authority copy directory exists even while its volume is empty: the back-up always
    # lists it, and restic fails on a missing source path. /backup only ever holds the
    # fallback volume of an unset BRIEVIE_BACKUP_DIR.
    && mkdir -p /data/documents /data/backup-config /data/authority /backup \
    && chown brievie:brievie /data/documents /data/backup-config /data/authority /backup
USER brievie

EXPOSE 8000
# Serves as the app role. Migrations run in the separate one-shot "migrate" service.
# Threaded workers: a slow upload (Caddy streams the body) holds one thread, not a whole
# worker, and is not killed by the worker timeout.
# Before gunicorn starts, prepare_setup_token prints the first-run installation code
# once (only while the server is not set up and BRIEVIE_SETUP_TOKEN is unset).
CMD ["sh", "-c", "python manage.py check_master_key && python manage.py prepare_setup_token && exec gunicorn brievie.wsgi:application --bind 0.0.0.0:8000 --workers 2 --worker-class gthread --threads 4 --timeout 120"]
