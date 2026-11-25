"""gunicorn's settings module (`gunicorn --config python:brievie.gunicorn_config`).

The master is made not dumpable before it forks its workers (`on_starting`); the
workers inherit it. The command line in `deploy/Dockerfile` holds the other settings."""

from apps.common.hardening import forbid_inspection


def on_starting(server: object) -> None:
    forbid_inspection()
