"""Reset links (D 6.3): an admin makes one for a member, who chooses a new password.

The token is shown once, in the link's fragment; only its hash is stored. A link works
once, for 24 hours, and only while its maker still may act on the account: the rule of
`apps.accounts.authority` is checked when the link is made and again when it is used.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.authority import account_authority
from apps.accounts.models import AccountNotice, PasswordReset, User, UserSession, normalise_email
from apps.accounts.notices import notify
from apps.accounts.services import current_user
from apps.accounts.sessions import end_sessions, start_session
from apps.audit.codes import ActorKind, AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, AuditValue, actor_from_request, record
from apps.common.errors import ApiError
from apps.common.links import fragment_link
from apps.common.net import valid_client_ip
from apps.common.tokens import new_token, token_hash
from apps.devices.models import Device
from apps.devices.services import revoke_device

RESET_VALIDITY = timedelta(hours=24)
RESET_PATH = "/wachtwoord-herstellen"


@dataclass(frozen=True)
class IssuedReset:
    link: str
    expires_at: datetime


def _user_target(user: User) -> AuditTarget:
    return AuditTarget(kind=TargetKind.USER, id=user.id, name=user.name)


def create_password_reset(request: HttpRequest, *, target_id: UUID) -> IssuedReset:
    """Make a reset link for another person's account; earlier unused links stop working.

    The member gets a notice; the server log, and the workspace log when the account
    has one workspace, record it.
    """
    actor = current_user(request)
    with transaction.atomic():
        authority = account_authority(actor, target_id)
        target = authority.target
        if not target.is_active:
            raise ApiError(
                409, "account_inactive", _("Dit account staat uit. Zet het eerst weer aan.")
            )
        now = timezone.now()
        PasswordReset.objects.filter(user=target, used_at=None, revoked_at=None).update(
            revoked_at=now
        )
        token, digest = new_token()
        reset = PasswordReset.objects.create(
            user=target,
            token_hash=digest,
            expires_at=now + RESET_VALIDITY,
            created_by=actor,
            created_by_name=actor.name,
        )
        notify(target, AccountNotice.Code.PASSWORD_RESET_CREATED, actor_name=actor.name)
        audit_actor = actor_from_request(request)
        record(AuditCode.PASSWORD_RESET_CREATED, actor=audit_actor, target=_user_target(target))
        if authority.workspace_id is not None:
            record(
                AuditCode.PASSWORD_RESET_CREATED,
                actor=audit_actor,
                target=_user_target(target),
                workspace_id=authority.workspace_id,
            )
    return IssuedReset(link=fragment_link(request, RESET_PATH, token), expires_at=reset.expires_at)


def _dead_link() -> ApiError:
    # One sentence for unknown, used, expired and revoked links: a link reveals nothing.
    return ApiError(
        400, "reset_link_invalid", _("Deze herstellink werkt niet meer. Vraag een nieuwe.")
    )


def _maker_may_still_act(reset: PasswordReset) -> bool:
    """Whether the link's maker still holds the authority (the account may have joined a
    second workspace, or the maker lost the admin role, since the link was made)."""
    if reset.created_by is None:
        return False
    try:
        account_authority(reset.created_by, reset.user_id)
    except ApiError:
        return False
    return True


def complete_password_reset(request: HttpRequest, *, token: str, password: str) -> User:
    """Set the new password with a reset link and sign this browser in.

    The link is locked, so two attempts at once give one success. A password the rules
    refuse answers 422 and leaves the link usable. Every other session, every paired
    device and every other open link of the account ends; the member gets a notice that
    this new session cannot dismiss.
    """
    with transaction.atomic():
        reset = (
            PasswordReset.objects.select_for_update(of=("self",))
            .select_related("user")
            .filter(token_hash=token_hash(token))
            .first()
        )
        now = timezone.now()
        if (
            reset is None
            or reset.used_at is not None
            or reset.revoked_at is not None
            or reset.expires_at <= now
            or not reset.user.is_active
            or not _maker_may_still_act(reset)
        ):
            raise _dead_link()
        user = reset.user
        validate_password(password, user)
        user.set_password(password)
        user.save(update_fields=["password", "updated_at"])
        reset.used_at = now
        reset.save(update_fields=["used_at", "updated_at"])
        # Whoever held the link did this; nobody proved that was the member (it may be
        # the admin), so the rows name nobody: the address, and the maker's id.
        actor = AuditActor(
            kind=ActorKind.USER, user_id=None, name="", client_ip=valid_client_ip(request)
        )
        values: dict[str, AuditValue] = {"via": "reset"}
        if reset.created_by_id is not None:
            values["reset_by"] = str(reset.created_by_id)
        record(AuditCode.PASSWORD_CHANGED, actor=actor, target=_user_target(user), values=values)
        _lock_out_everything_else(user, actor, current=request, reason="reset")
        notify(user, AccountNotice.Code.PASSWORD_RESET_COMPLETED, actor_name=reset.created_by_name)
        start_session(request, user, via=UserSession.Via.RESET)
    return user


def _lock_out_everything_else(
    user: User, actor: AuditActor, *, current: HttpRequest | None, reason: str
) -> None:
    """After a reset (by an admin's link or on the server), which is what one does after a
    suspected break-in: end every session, revoke every paired device and every other
    open reset link of the account. A user's own password change keeps the devices.
    """
    target = _user_target(user)
    ended = end_sessions(user, current=current)
    if ended:
        record(
            AuditCode.SESSION_ENDED,
            actor=actor,
            target=target,
            values={"count": ended, "reason": reason},
        )
    # Locked first, so a device revoked meanwhile cannot make `revoke_device` answer 404.
    devices = list(
        Device.objects.select_for_update().filter(user=user, revoked_at__isnull=True).order_by("id")
    )
    for device in devices:
        revoke_device(user, device.id)
        record(
            AuditCode.DEVICE_REVOKED,
            actor=actor,
            target=AuditTarget(kind=TargetKind.DEVICE, id=device.id, name=device.name),
        )
    PasswordReset.objects.filter(user=user, used_at=None, revoked_at=None).update(
        revoked_at=timezone.now()
    )


def revoke_resets_made_by(user_id: UUID) -> int:
    """Stop every open reset link `user_id` made; for when they lose the admin role or
    leave. Returns how many."""
    with transaction.atomic():
        return PasswordReset.objects.filter(
            created_by_id=user_id, used_at=None, revoked_at=None
        ).update(revoked_at=timezone.now())


def reset_password_by_command(*, email: str, password: str) -> User:
    """Set a password from the server's command line (a server admin who lost theirs):
    the rules apply, every session, device and open reset link ends, the member gets a
    notice, the log records the system as actor."""
    with transaction.atomic():
        user = User.objects.select_for_update().filter(email=normalise_email(email)).first()
        if user is None:
            raise User.DoesNotExist
        validate_password(password, user)
        user.set_password(password)
        user.save(update_fields=["password", "updated_at"])
        actor = AuditActor.system()
        record(
            AuditCode.PASSWORD_CHANGED,
            actor=actor,
            target=_user_target(user),
            values={"via": "command"},
        )
        _lock_out_everything_else(user, actor, current=None, reason="password_changed")
        notify(user, AccountNotice.Code.PASSWORD_RESET_BY_COMMAND, actor_name="")
    return user
