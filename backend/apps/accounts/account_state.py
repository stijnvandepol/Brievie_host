"""Whether an account is switched on and whether it is a server admin (D 6.3, D 21).

Every path that takes server-admin power away goes through `lock_active_server_admins`,
so the server always keeps at least one active server admin.

(A module of its own: `apps.devices.services` and `apps.accounts.resets` import
`apps.accounts.services`, so that module cannot import them back.)
"""

from django.db import transaction
from django.http import HttpRequest
from django.utils.translation import gettext as _

from apps.accounts.models import User
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


def _take_server_admin_away(user: User, *, actor: AuditActor) -> None:
    """Clear the flag under the server-admin lock; 409 `last_admin` for the last active
    one. Runs inside the caller's transaction."""
    admins = lock_active_server_admins()
    if user.is_active and not any(admin.id != user.id for admin in admins):
        raise last_server_admin_error()
    user.is_server_admin = False
    user.save(update_fields=["is_server_admin", "updated_at"])
    record(
        AuditCode.SERVER_ADMIN_REVOKED,
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
            _take_server_admin_away(user, actor=actor)
        user.is_active = False
        user.save(update_fields=["is_active", "updated_at"])
        lock_out_account(user, actor, current=current, reason="deactivated")
        PairingCode.objects.filter(user=user, used_at__isnull=True).delete()
        revoke_resets_made_by(user.id)
