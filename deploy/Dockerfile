# syntax=docker/dockerfile:1
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./
RUN DATABASE_URL=postgres://build@localhost/build \
    DATABASE_OWNER_URL=postgres://build@localhost/build \
    SECRET_KEY=collectstatic-only \
    python manage.py collectstatic --noinput

RUN useradd --system --create-home --uid 10001 brievie
USER brievie

EXPOSE 8000
# Serves as the app role. Migrations run in the separate one-shot "migrate" service.
CMD ["gunicorn", "brievie.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
