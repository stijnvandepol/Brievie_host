"""The caller's own second factor ("Beveiliging", two-step sign-in S 9): what the API's
`/me/second-factor` operations call.

Setting one up works from a web session, and from a pending sign-in at the step `setup`
(a rule asks for a factor and the grace period is over): then confirming it completes the
sign-in. Either way the account's other sessions end once the factor is on: they signed
in without it.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.http import HttpRequest
from django.http.request import split_domain_port
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User, UserSession
from apps.accounts.second_factor import (
    SetupSecret,
    begin_setup,
    confirm_setup,
    recovery_codes_left,
    remove_factor,
    renew_recovery_codes,
)
from apps.accounts.second_factor_policy import has_factor, in_grace, requirement_for
from apps.accounts.sessions import end_sessions, recently_authenticated
from apps.accounts.signin import complete_sign_in
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.common.net import valid_client_ip


@dataclass(frozen=True)
class SecondFactorStatus:
    enabled: bool
    required: bool
    # none, server or workspace: what asks it (the server's rule wins the name).
    required_by: str
    required_by_workspaces: list[str]
    # The last moment of the grace period (ISO 8601), while it runs.
    grace_until: str | None
    recovery_codes_left: int


def acting_user(request: HttpRequest) -> User:
    """The account of the web session, or of the pending set-up (`PendingSetupAuth`)."""
    user = request.auth  # type: ignore[attr-defined]  # set by Ninja's auth
    if not isinstance(user, User):
        raise TypeError("Endpoint requires an account.")
    return user


def _signed_in(request: HttpRequest) -> bool:
    return bool(request.user.is_authenticated)


def my_status(request: HttpRequest, *, now: datetime | None = None) -> SecondFactorStatus:
    user = acting_user(request)
    moment = now or timezone.now()
    requirement = requirement_for(user, now=moment)
    enabled = has_factor(user)
    if requirement.by_server:
        required_by = "server"
    elif requirement.by_workspaces:
        required_by = "workspace"
    else:
        required_by = "none"
    grace_until = (
        requirement.grace_until.isoformat()
        if requirement.grace_until is not None and in_grace(requirement, now=moment)
        else None
    )
    return SecondFactorStatus(
        enabled=enabled,
        required=requirement.required,
        required_by=required_by,
        required_by_workspaces=list(requirement.by_workspaces),
        grace_until=grace_until,
        recovery_codes_left=recovery_codes_left(user) if enabled else 0,
    )


def _host(request: HttpRequest) -> str:
    domain, _port = split_domain_port(request.get_host())
    return domain or "brievie"


def begin_my_setup(request: HttpRequest) -> SetupSecret:
    """A new secret to scan. From a web session only after a recent sign-in or password
    (403 `reauthentication_required`): a browser left open must not get a factor that
    then signs its owner out everywhere."""
    user = acting_user(request)
    if _signed_in(request) and not recently_authenticated(request):
        raise ApiError(403, "reauthentication_required", _("Vul eerst je wachtwoord opnieuw in."))
    return begin_setup(user, host=_host(request))


def confirm_my_setup(request: HttpRequest, *, code: str) -> list[str]:
    """Switch the factor on with a code from the app; answer the ten recovery codes. A
    pending set-up signs in; a session is marked as having passed the second step. Every
    other session of the account ends."""
    user = acting_user(request)
    client_ip = valid_client_ip(request)
    confirmed = confirm_setup(user, code, now=timezone.now(), client_ip=client_ip)
    if not _signed_in(request):
        complete_sign_in(request, confirmed.passed)
    with transaction.atomic():
        UserSession.objects.filter(session_key=request.session.session_key).update(
            second_factor=True
        )
        ended = end_sessions(user, except_key=request.session.session_key)
        if ended:
            record(
                AuditCode.SESSION_ENDED,
                actor=AuditActor.for_user(user, client_ip=client_ip),
                target=AuditTarget(kind=TargetKind.USER, id=user.id, name=user.name),
                values={"count": ended, "reason": "second_factor_added"},
            )
    return confirmed.recovery_codes


def renew_my_recovery_codes(request: HttpRequest, *, code: str) -> list[str]:
    return renew_recovery_codes(
        acting_user(request), code, now=timezone.now(), client_ip=valid_client_ip(request)
    )


def remove_my_factor(request: HttpRequest, *, password: str, code: str) -> None:
    remove_factor(request, acting_user(request), password=password, code=code, now=timezone.now())
