"""Who has a second factor, and who must (two-step sign-in, S 5).

A second factor belongs to the account, which belongs to the whole server. A rule asks it
of an account: the server-wide `BRIEVIE_REQUIRE_SECOND_FACTOR` (`server_admins` or
`everyone`), and later a workspace's own switch (`_workspace_requirements`, plan Task 15).
The strictest wins: any rule that asks it makes it required.

From the moment a rule first asks it of an account (`second_factor_required_since`) the
account has `BRIEVIE_SECOND_FACTOR_GRACE_DAYS` to add one; signing in during that time
works with a notice, after it only through setting one up. An account that has a factor
is never "in grace".
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction

from apps.accounts.models import SecondFactor, User


@dataclass(frozen=True)
class Requirement:
    required: bool
    by_server: bool
    # The names of the workspaces that ask it (none until plan Task 15).
    by_workspaces: tuple[str, ...]
    # The end of the grace period; None when not required or the account has a factor.
    grace_until: datetime | None


def has_factor(user: User) -> bool:
    """Whether the account has a confirmed second factor (an unfinished set-up is none)."""
    return SecondFactor.objects.filter(user_id=user.id, confirmed_at__isnull=False).exists()


def _server_requires(user: User) -> bool:
    rule: str = settings.BRIEVIE_REQUIRE_SECOND_FACTOR
    return rule == "everyone" or (rule == "server_admins" and user.is_server_admin)


def _workspace_requirements(user: User) -> tuple[str, ...]:
    """The workspaces of `user` that require a second factor: none yet (plan Task 15
    adds the per-workspace switch)."""
    return ()


def _grace() -> timedelta:
    return timedelta(days=settings.BRIEVIE_SECOND_FACTOR_GRACE_DAYS)


def requirement_for(user: User, *, now: datetime) -> Requirement:
    by_server = _server_requires(user)
    by_workspaces = _workspace_requirements(user)
    required = by_server or bool(by_workspaces)
    grace_until = None
    if required and not has_factor(user):
        since = user.second_factor_required_since or now
        grace_until = since + _grace()
    return Requirement(
        required=required,
        by_server=by_server,
        by_workspaces=by_workspaces,
        grace_until=grace_until,
    )


def in_grace(requirement: Requirement, *, now: datetime) -> bool:
    """Required, no factor yet, and the grace period still runs."""
    return (
        requirement.required
        and requirement.grace_until is not None
        and now < requirement.grace_until
    )


def note_required_since(user: User, *, now: datetime) -> None:
    """Start the grace period the first time a rule asks a factor of `user` (one atomic
    UPDATE, so two requests at once start it once), and forget it when no rule asks it
    any more. Kept while the account has a factor: a factor cleared later gives no new
    grace."""
    required = _server_requires(user) or bool(_workspace_requirements(user))
    with transaction.atomic():
        if required:
            if user.second_factor_required_since is None:
                User.objects.filter(pk=user.pk, second_factor_required_since__isnull=True).update(
                    second_factor_required_since=now
                )
                user.second_factor_required_since = (
                    User.objects.filter(pk=user.pk)
                    .values_list("second_factor_required_since", flat=True)
                    .first()
                )
        elif user.second_factor_required_since is not None:
            User.objects.filter(pk=user.pk).update(second_factor_required_since=None)
            user.second_factor_required_since = None
