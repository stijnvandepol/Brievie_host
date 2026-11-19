"""What an admin does to somebody else's sessions (D 6.3): the authority comes from
`apps.accounts.authority`, the member gets a notice and the logs record it."""

from uuid import UUID

from django.db import transaction
from django.http import HttpRequest

from apps.accounts.authority import account_authority
from apps.accounts.models import AccountNotice
from apps.accounts.notices import notify
from apps.accounts.services import current_user
from apps.accounts.sessions import end_sessions
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditTarget, AuditValue, actor_from_request, record


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
