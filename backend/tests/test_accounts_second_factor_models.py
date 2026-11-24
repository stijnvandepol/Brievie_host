"""The second-factor tables and the server-wide rule (two-step sign-in, plan Task 2)."""

import runpy
from pathlib import Path

import environ
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction

import brievie.settings
from apps.accounts.models import RecoveryCode, SecondFactor, User, UserSession
from tests.factories import UserFactory

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = str(Path(brievie.settings.__file__))


def fresh_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> dict[str, object]:
    for name in ("BRIEVIE_REQUIRE_SECOND_FACTOR", "BRIEVIE_SECOND_FACTOR_GRACE_DAYS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    return runpy.run_path(SETTINGS_PATH)


def test_a_new_factor_starts_unconfirmed_with_clean_counters() -> None:
    factor = SecondFactor.objects.create(user=UserFactory.create(), secret_sealed=b"x")

    factor.refresh_from_db()
    assert factor.confirmed_at is None
    assert factor.last_used_step == 0
    assert factor.failed_count == 0
    assert factor.first_failed_at is None
    assert factor.locked_until is None
    assert factor.lockouts == 0
    assert factor.pending_secret_sealed is None
    assert factor.pending_since is None


def test_one_factor_per_user() -> None:
    user = UserFactory.create()
    SecondFactor.objects.create(user=user, secret_sealed=b"x")

    with pytest.raises(IntegrityError), transaction.atomic():
        SecondFactor.objects.create(user=user, secret_sealed=b"y")


def test_recovery_code_hash_is_unique() -> None:
    user = UserFactory.create()
    RecoveryCode.objects.create(user=user, code_hash="a" * 64)

    with pytest.raises(IntegrityError), transaction.atomic():
        RecoveryCode.objects.create(user=UserFactory.create(), code_hash="a" * 64)


def test_deleting_a_user_removes_the_factor_and_the_codes() -> None:
    user = UserFactory.create()
    SecondFactor.objects.create(user=user, secret_sealed=b"x")
    RecoveryCode.objects.create(user=user, code_hash="b" * 64)

    user.delete()

    assert not SecondFactor.objects.exists()
    assert not RecoveryCode.objects.exists()


def test_new_columns_default() -> None:
    user = UserFactory.create()

    assert User.objects.get(pk=user.pk).second_factor_required_since is None
    session = UserSession.objects.create(
        user=user, session_key="k" * 32, method="password", last_used_at=user.created_at
    )
    assert UserSession.objects.get(pk=session.pk).second_factor is False


def test_the_rule_defaults_to_none_and_seven_days(monkeypatch: pytest.MonkeyPatch) -> None:
    values = fresh_settings(monkeypatch)

    assert values["BRIEVIE_REQUIRE_SECOND_FACTOR"] == "none"
    assert values["BRIEVIE_SECOND_FACTOR_GRACE_DAYS"] == 7


@pytest.mark.parametrize("rule", ["none", "server_admins", "everyone"])
def test_the_rule_takes_its_three_values(monkeypatch: pytest.MonkeyPatch, rule: str) -> None:
    values = fresh_settings(monkeypatch, BRIEVIE_REQUIRE_SECOND_FACTOR=rule)

    assert values["BRIEVIE_REQUIRE_SECOND_FACTOR"] == rule


@pytest.mark.parametrize("rule", ["admins", "Everyone", "true", ""])
def test_another_rule_is_refused(monkeypatch: pytest.MonkeyPatch, rule: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="BRIEVIE_REQUIRE_SECOND_FACTOR"):
        fresh_settings(monkeypatch, BRIEVIE_REQUIRE_SECOND_FACTOR=rule)


@pytest.mark.parametrize("days", ["0", "30"])
def test_grace_of_0_to_30_days(monkeypatch: pytest.MonkeyPatch, days: str) -> None:
    values = fresh_settings(monkeypatch, BRIEVIE_SECOND_FACTOR_GRACE_DAYS=days)

    assert values["BRIEVIE_SECOND_FACTOR_GRACE_DAYS"] == int(days)


@pytest.mark.parametrize("days", ["-1", "31"])
def test_grace_outside_0_to_30_is_refused(monkeypatch: pytest.MonkeyPatch, days: str) -> None:
    with pytest.raises(ImproperlyConfigured, match="BRIEVIE_SECOND_FACTOR_GRACE_DAYS"):
        fresh_settings(monkeypatch, BRIEVIE_SECOND_FACTOR_GRACE_DAYS=days)


def test_env_example_documents_both_variables_commented_out() -> None:
    text = (ROOT / "deploy" / ".env.example").read_text()

    assert "# BRIEVIE_REQUIRE_SECOND_FACTOR=none" in text
    assert "# BRIEVIE_SECOND_FACTOR_GRACE_DAYS=7" in text


def test_compose_passes_both_variables_to_api_and_worker() -> None:
    from tests.test_deploy_files import service

    for name in ("api", "worker"):
        text = service(name)
        assert "BRIEVIE_REQUIRE_SECOND_FACTOR: ${BRIEVIE_REQUIRE_SECOND_FACTOR:-none}" in text
        assert "BRIEVIE_SECOND_FACTOR_GRACE_DAYS: ${BRIEVIE_SECOND_FACTOR_GRACE_DAYS:-7}" in text
