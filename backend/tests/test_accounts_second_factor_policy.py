"""Who must use two-step sign-in (spec S 5, plan Task 5)."""

from datetime import UTC, datetime, timedelta

import pytest
from pytest_django import Settings

from apps.accounts.models import SecondFactor, User
from apps.accounts.second_factor_policy import (
    has_factor,
    in_grace,
    note_required_since,
    requirement_for,
)
from tests.factories import UserFactory

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)


@pytest.fixture
def admin() -> User:
    return UserFactory.create(is_server_admin=True)


@pytest.fixture
def member() -> User:
    return UserFactory.create()


def confirm_factor(user: User) -> None:
    SecondFactor.objects.create(user=user, secret_sealed=b"x", confirmed_at=NOW)


def test_none_never_requires(settings: Settings, admin: User, member: User) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "none"

    for user in (admin, member):
        requirement = requirement_for(user, now=NOW)
        assert not requirement.required
        assert requirement.grace_until is None
        assert not in_grace(requirement, now=NOW)


def test_server_admins_requires_admins_only(settings: Settings, admin: User, member: User) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "server_admins"

    assert requirement_for(admin, now=NOW).required
    assert requirement_for(admin, now=NOW).by_server
    assert not requirement_for(member, now=NOW).required


def test_everyone_requires_all(settings: Settings, admin: User, member: User) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"

    assert requirement_for(admin, now=NOW).required
    assert requirement_for(member, now=NOW).required
    assert requirement_for(member, now=NOW).by_workspaces == ()


def test_required_since_is_set_once_and_kept(settings: Settings, member: User) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"

    note_required_since(member, now=NOW)
    note_required_since(member, now=NOW + timedelta(days=2))

    assert member.second_factor_required_since == NOW
    assert User.objects.get(pk=member.pk).second_factor_required_since == NOW


def test_a_stale_copy_does_not_restart_the_grace(settings: Settings, member: User) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    note_required_since(member, now=NOW)
    stale = User.objects.get(pk=member.pk)
    stale.second_factor_required_since = None

    note_required_since(stale, now=NOW + timedelta(days=3))

    assert stale.second_factor_required_since == NOW


def test_required_since_is_kept_when_the_rule_goes(settings: Settings, member: User) -> None:
    # Security review of PR #52, point 2: a rule missing for a while (a restore onto a new
    # machine without it) must not give everybody a new grace period when it is back.
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    note_required_since(member, now=NOW)
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "server_admins"

    note_required_since(member, now=NOW + timedelta(days=3))
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    note_required_since(member, now=NOW + timedelta(days=9))

    assert User.objects.get(pk=member.pk).second_factor_required_since == NOW
    assert not in_grace(
        requirement_for(member, now=NOW + timedelta(days=9)), now=NOW + timedelta(days=9)
    )


def test_grace_ends_after_the_configured_days(settings: Settings, member: User) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    settings.BRIEVIE_SECOND_FACTOR_GRACE_DAYS = 7
    note_required_since(member, now=NOW)

    requirement = requirement_for(member, now=NOW)

    assert requirement.grace_until == NOW + timedelta(days=7)
    assert in_grace(requirement, now=NOW + timedelta(days=6, hours=23))
    assert not in_grace(requirement, now=NOW + timedelta(days=7))


def test_grace_zero_asks_for_setup_at_once(settings: Settings, member: User) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    settings.BRIEVIE_SECOND_FACTOR_GRACE_DAYS = 0
    note_required_since(member, now=NOW)

    assert not in_grace(requirement_for(member, now=NOW), now=NOW)


def test_a_user_who_has_a_factor_is_never_in_grace(settings: Settings, member: User) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    note_required_since(member, now=NOW)
    confirm_factor(member)

    requirement = requirement_for(member, now=NOW)

    assert requirement.required
    assert requirement.grace_until is None
    assert not in_grace(requirement, now=NOW)


def test_an_unconfirmed_factor_is_no_factor(member: User) -> None:
    SecondFactor.objects.create(user=member, secret_sealed=b"x")

    assert not has_factor(member)
    SecondFactor.objects.filter(user=member).update(confirmed_at=NOW)
    assert has_factor(member)
