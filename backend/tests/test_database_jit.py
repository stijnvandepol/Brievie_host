"""No JIT compilation on the app's connections (DECISIONS 2026-11-20, visibility measurement)."""

from django.conf import settings
from django.db import connection


def test_the_settings_turn_jit_off_at_connection_start() -> None:
    options = settings.DATABASES["default"]["OPTIONS"]
    assert isinstance(options, dict)
    assert "-c jit=off" in str(options["options"])


def test_the_test_connection_runs_without_jit() -> None:
    # The test run uses the same settings as the app (only the role differs), so this
    # is what a request and the visibility measurement get.
    with connection.cursor() as c:
        c.execute("SHOW jit")
        row = c.fetchone()
    assert row == ("off",)
