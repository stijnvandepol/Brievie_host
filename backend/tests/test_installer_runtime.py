"""The real wiring of `prepare_server` that needs no server: files, settings, the command."""

import pytest
from django.core.management import call_command

from apps.backup.envcopy import _leaks
from apps.installer import runtime
from apps.updates.release import parse_release


def test_the_settings_copy_holds_no_secret_and_passes_the_leak_guard() -> None:
    environ = {
        "BRIEVIE_HOSTNAME": "mijnnas.local",
        "BRIEVIE_HTTPS_PORT": "8443",
        "BRIEVIE_MASTER_KEY": "ab" * 32,
        "DATABASE_PASSWORD": "hunter2-hunter2",
        "SECRET_KEY": "k" * 60,
        "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY": "s3-secret-value",
        "BRIEVIE_REQUIRE_SECOND_FACTOR": "everyone",
        "BRIEVIE_SECOND_FACTOR_GRACE_DAYS": "3",
    }
    text = runtime.settings_copy_text(environ)
    assert "BRIEVIE_HOSTNAME=mijnnas.local" in text
    # A restore on a new machine keeps the rule of two-step verification (review of #52).
    assert "BRIEVIE_REQUIRE_SECOND_FACTOR=everyone" in text
    assert "BRIEVIE_SECOND_FACTOR_GRACE_DAYS=3" in text
    assert "BRIEVIE_HTTPS_PORT=8443" in text
    for secret in ("ab" * 32, "hunter2", "k" * 60, "s3-secret-value"):
        assert secret not in text
    assert not any(_leaks(line) for line in text.splitlines())


def test_the_release_file_of_the_checkout_parses() -> None:
    release = parse_release(runtime.release_file().read_text("utf-8"))
    assert release.postgres_major >= 18


def test_the_command_refuses_to_run_as_another_role_than_the_owner(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The test run uses the owner's credentials but not the settings' owner alias itself.
    with pytest.raises(SystemExit) as caught:
        call_command("prepare_server")
    assert caught.value.code == 1
    assert "BRIEVIE_DB_ROLE=owner" in capsys.readouterr().err
