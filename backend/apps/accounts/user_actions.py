"""What an admin does to somebody else's sessions and second factor (D 6.3): the
authority comes from `apps.accounts.authority`, the member gets a notice and the logs
record it."""

from uuid import UUID

from django.db import transaction
from django.http import HttpRequest
from django.utils.translation import gettext as _

from apps.accounts.authority import AccountAuthority, account_authority
from apps.accounts.models import AccountNotice, User
from apps.accounts.notices import notify
from apps.accounts.second_factor import clear_factor
from apps.accounts.second_factor_policy import has_factor
from apps.accounts.services import check_own_password, current_user, lock_active_server_admins
from apps.accounts.sessions import end_sessions, session_passed_second_factor
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditTarget, AuditValue, actor_from_request, record
from apps.common.errors import ApiError
from apps.workspaces.locks import lock_memberships
from apps.workspaces.models import Workspace


def end_user_sessions(request: HttpRequest, *, target_id: UUID) -> None:
    """End every web session of another person's account.

    `current=request` keeps the caller's own session clean if it were ever among them.
    Nothing is recorded or noticed when the account had no session.
    """
    actor = current_user(request)
    with transaction.atomic():
        authority = account_authority(actor, target_id)
        target = authority.target
        ended = end_sessions(target, current=request)
        if not ended:
            return
        notify(target, AccountNotice.Code.SESSIONS_ENDED, actor_name=actor.name)
        audit_actor = actor_from_request(request)
        audit_target = AuditTarget(kind=TargetKind.USER, id=target.id, name=target.name)
        values: dict[str, AuditValue] = {"count": ended, "reason": "admin"}
        record(AuditCode.SESSION_ENDED, actor=audit_actor, target=audit_target, values=values)
        if authority.workspace_id is not None:
            record(
                AuditCode.SESSION_ENDED,
                actor=audit_actor,
                target=audit_target,
                workspace_id=authority.workspace_id,
                values=values,
            )


def _locked_authority(actor: User, target_id: UUID) -> AccountAuthority:
    """`account_authority`, decided on rows held until the transaction ends, in the lock
    order of `apps.workspaces.members`: the memberships of the account's workspaces (id
    order), the active server admins, then the account's own row. A demotion, a second
    membership or a lost server-admin role at the same moment waits, or is seen."""
    workspaces = Workspace.objects.filter(memberships__user_id=target_id).order_by("id")
    for workspace in workspaces:
        lock_memberships(workspace)
    lock_active_server_admins()
    fresh = User.objects.filter(pk=actor.pk).first()
    if fresh is None:
        raise ApiError(404, "user_not_found", _("We kunnen dit account niet vinden."))
    User.objects.select_for_update(no_key=True).filter(pk=target_id).first()
    return account_authority(fresh, target_id)


def clear_user_second_factor(
    request: HttpRequest, *, target_id: UUID, password: str, revoke_devices: bool
) -> int:
    """Clear another person's lost second factor (S 7): the admin's own password again
    (counted like a sign-in), from a session that passed the second step when the admin
    has one; the authority rule of account actions; every session of the account ends,
    its devices are unpaired when asked, the logs record it and the member gets a notice.
    Answers how many devices were unpaired."""
    actor = current_user(request)
    if has_factor(actor) and not session_passed_second_factor(request):
        raise ApiError(
            403,
            "second_factor_session_required",
            _("Log opnieuw in met je code om dit te doen."),
        )
    check_own_password(request, actor, password, via="second_factor_clear")
    with transaction.atomic():
        authority = _locked_authority(actor, target_id)
        return clear_factor(
            authority.target,
            revoke_devices=revoke_devices,
            via="admin",
            actor=actor_from_request(request),
            actor_name=actor.name,
            current=request,
            workspace_id=authority.workspace_id,
        )
