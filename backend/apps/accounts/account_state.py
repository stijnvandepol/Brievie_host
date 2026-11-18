"""Whether an account is switched on and whether it is a server admin (D 6.3, D 21).

Every path that takes server-admin power away goes through `lock_active_server_admins`,
so the server always keeps at least one active server admin.

(A module of its own: `apps.devices.services` and `apps.accounts.resets` import
`apps.accounts.services`, so that module cannot import them back.)
"""

from django.db import transaction
from django.http import HttpRequest
from django.utils.translation import gettext as _

from apps.accounts.models import AccountNotice, User, normalise_email
from apps.accounts.notices import notify
from apps.accounts.resets import lock_out_account, revoke_resets_made_by
from apps.accounts.services import lock_active_server_admins
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.devices.models import PairingCode


def last_server_admin_error() -> ApiError:
    return ApiError(
        409,
        "last_admin",
        _("Er is altijd minstens één serverbeheerder nodig. Dit account kan daarom niet weg."),
    )


def _is_last_active_server_admin(user: User) -> bool:
    """Takes the server-admin lock (inside the caller's transaction)."""
    admins = lock_active_server_admins()
    return user.is_active and not any(admin.id != user.id for admin in admins)


def _set_flag(user: User, *, grant: bool, actor: AuditActor) -> None:
    user.is_server_admin = grant
    user.save(update_fields=["is_server_admin", "updated_at"])
    record(
        AuditCode.SERVER_ADMIN_GRANTED if grant else AuditCode.SERVER_ADMIN_REVOKED,
        actor=actor,
        target=AuditTarget(kind=TargetKind.USER, id=user.id, name=user.name),
    )


def deactivate_account(
    user: User, *, actor: AuditActor, current: HttpRequest | None = None
) -> None:
    """Switch the account off, for when its last membership goes.

    It stops being a server admin (an invitation that brings it back must not bring the
    power back), every web session ends (`current`'s own one by a flush, so the response
    stays clean), every device and unused pairing code stops, and every open reset link
    for it or made by it stops. 409 `last_admin` for the last active server admin.
    """
    with transaction.atomic():
        if user.is_server_admin:
            if _is_last_active_server_admin(user):
                raise last_server_admin_error()
            _set_flag(user, grant=False, actor=actor)
        user.is_active = False
        user.save(update_fields=["is_active", "updated_at"])
        lock_out_account(user, actor, current=current, reason="deactivated")
        PairingCode.objects.filter(user=user, used_at__isnull=True).delete()
        revoke_resets_made_by(user.id)


def set_server_admin(*, email: str, grant: bool, actor: AuditActor) -> User:
    """Make the account with this address a server admin (`grant`) or take that away,
    from the server's command line (D 21); the member gets a notice.

    `ValueError("unknown_account")` for an address without an account,
    `ValueError("account_inactive")` for granting to a switched-off account,
    `ValueError("last_server_admin")` for removing the last active server admin. An
    account already in the asked state is left as it is, without a log row.

    Lock order as everywhere: the active server admins, then the account's own row.
    """
    with transaction.atomic():
        # Removing needs the server-admin lock, always before the account's own row.
        admins = [] if grant else lock_active_server_admins()
        user = (
            User.objects.select_for_update(no_key=True).filter(email=normalise_email(email)).first()
        )
        if user is None:
            raise ValueError("unknown_account")
        if grant and not user.is_active:
            raise ValueError("account_inactive")
        if user.is_server_admin == grant:
            return user
        if not grant and user.is_active and all(admin.id == user.id for admin in admins):
            raise ValueError("last_server_admin")
        _set_flag(user, grant=grant, actor=actor)
        if grant:
            notify(user, AccountNotice.Code.SERVER_ADMIN_GRANTED, actor_name="")
        else:
            revoke_resets_made_by(user.id)
            notify(user, AccountNotice.Code.SERVER_ADMIN_REVOKED, actor_name="")
    return user
