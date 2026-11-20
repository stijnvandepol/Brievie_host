import hashlib
import hmac
import secrets
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from axes.handlers.proxy import AxesProxyHandler
from django.conf import settings
from django.contrib.auth import authenticate, update_session_auth_hash
from django.contrib.auth import logout as django_logout
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.signals import user_login_failed
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connection, transaction
from django.http import HttpRequest
from django.utils.translation import gettext as _

from apps.accounts.models import SetupToken, User, UserSession, normalise_email
from apps.accounts.sessions import (
    end_current_session,
    end_sessions,
    mark_reauthenticated,
    rekey_session,
    start_session,
)
from apps.audit.codes import ActorKind, AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, actor_from_request, record
from apps.common.errors import ApiError
from apps.common.net import valid_client_ip
from apps.common.throttling import FailedAttemptThrottle
from apps.workspaces.models import Membership
from apps.workspaces.services import create_workspace

# pg_advisory_xact_lock key that serialises POST /setup and the generation of the
# setup token ("brievie" in ASCII).
SETUP_LOCK_KEY = 0x62726965766965

# Failed sign-ins per client address (every failure writes an audit row, so one address
# must not grow the table without bound). Successful ones are not counted: many people
# may sign in behind one office address. axes still locks per email and address.
LOGIN_FAILURES = FailedAttemptThrottle("login", "30/10m")

# Which password check an axes lockout comes from, for its audit row.
_password_check: ContextVar[str] = ContextVar("password_check", default="password")


@contextmanager
def _checking_password_for(via: str) -> Iterator[None]:
    token = _password_check.set(via)
    try:
        yield
    finally:
        _password_check.reset(token)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _lock_setup() -> None:
    with connection.cursor() as c:
        c.execute("SELECT pg_advisory_xact_lock(%s)", [SETUP_LOCK_KEY])


def prepare_setup_token(*, new: bool = False) -> str | None:
    """Generate the first-run setup code if one is needed; return it (to print once).

    Returns None when no code is generated: a user exists, `BRIEVIE_SETUP_TOKEN`
    is set, or a code exists already (unless `new`). Only the hash is stored. The
    advisory lock makes concurrent calls (several containers or workers) create
    one code.
    """
    with transaction.atomic():
        _lock_setup()
        if User.objects.exists() or settings.BRIEVIE_SETUP_TOKEN:
            SetupToken.objects.all().delete()
            return None
        if SetupToken.objects.exists() and not new:
            return None
        token = secrets.token_urlsafe(16)
        SetupToken.objects.all().delete()
        SetupToken.objects.create(token_hash=sha256_hex(token))
    return token


def _setup_token_is_valid(given: str) -> bool:
    given_hash = sha256_hex(given.strip())  # a phone keyboard adds spaces
    configured: str = settings.BRIEVIE_SETUP_TOKEN
    if configured:
        return hmac.compare_digest(given_hash, sha256_hex(configured))
    stored = SetupToken.objects.first()
    return stored is not None and hmac.compare_digest(given_hash, stored.token_hash)


def current_user(request: HttpRequest) -> User:
    """The authenticated caller; for endpoints that require auth."""
    user = request.user
    if not isinstance(user, User):
        raise TypeError("Endpoint requires an authenticated user.")
    return user


def needs_setup() -> bool:
    return not User.objects.exists()


def _required(value: str, message: str) -> str:
    value = value.strip()
    if not value:
        raise ValidationError(message, code="required")
    return value


def build_user(*, email: str, name: str, password: str, is_server_admin: bool = False) -> User:
    """A new, unsaved account checked by the rules of every new account: a valid address
    (stored in lower case), a name, and a password the validators accept (422 otherwise).
    Whether the address is free is the caller's check (and the unique index's)."""
    email = normalise_email(email)
    validate_email(email)
    user = User(
        email=email, name=_required(name, _("Vul je naam in.")), is_server_admin=is_server_admin
    )
    validate_password(password, user)
    user.set_password(password)
    return user


def setup_server(
    request: HttpRequest,
    *,
    setup_token: str,
    email: str,
    password: str,
    name: str,
    workspace_name: str,
    workspace_kind: str,
    extra_labels: Sequence[str] = (),
) -> Membership:
    """Create the first user, their workspace and admin membership, and log them in.

    Only allowed while no user exists, and only with the setup code (see
    `prepare_setup_token`), which is deleted afterwards. The advisory lock lasts
    until the request transaction ends, so a concurrent call waits and then sees
    the new user.
    """
    with transaction.atomic():
        _lock_setup()
        if User.objects.exists():
            raise ApiError(409, "already_set_up", _("Brievie is al ingesteld."))
        if not _setup_token_is_valid(setup_token):
            raise ApiError(
                403,
                "setup_token_invalid",
                _("De installatiecode klopt niet. Je vindt hem in het log van de server."),
            )
        user = build_user(email=email, name=name, password=password, is_server_admin=True)
        user.save()
        workspace = create_workspace(
            name=_required(workspace_name, _("Geef de werkruimte een naam.")),
            kind=workspace_kind,
            admin=user,
            extra_labels=extra_labels,
        )
        SetupToken.objects.all().delete()
        start_session(request, user, via=UserSession.Via.SETUP)
    return Membership.objects.select_related("workspace").get(user=user, workspace=workspace)


def lock_active_server_admins() -> list[User]:
    """Lock the active server admins, always in id order so two callers cannot deadlock.

    Every check of "is there another server admin?" takes this lock first, so two
    requests that each remove one of the last two admins run one after the other.
    `FOR NO KEY UPDATE`: two of these exclude each other, but an insert that only
    references a server admin (a sign-in, an upload, a notice) is not held up, and
    cannot deadlock with a removal. Must run inside a transaction.
    """
    return list(
        User.objects.select_for_update(no_key=True)
        .filter(is_server_admin=True, is_active=True)
        .order_by("id")
    )


def other_active_server_admin_exists(user: User) -> bool:
    """Whether an active server admin other than `user` exists (takes the lock above)."""
    return any(admin.id != user.id for admin in lock_active_server_admins())


def login(request: HttpRequest, *, email: str, password: str) -> User:
    """Log in with email and password, with django-axes' lockout per email + address.

    The password check runs outside `transaction.atomic`: a failure must keep the
    attempt that axes recorded and the `sign_in_failed` row, and the 401 response
    commits the request transaction.
    """
    _refuse_when_locked(request, email)
    user = authenticate(request, username=email, password=password)
    if not isinstance(user, User):
        LOGIN_FAILURES.record_failure(request)
        _record_sign_in_problem(AuditCode.SIGN_IN_FAILED, request, email)
        raise ApiError(401, "invalid_credentials", _("Het e-mailadres of wachtwoord klopt niet."))
    start_session(request, user, via=UserSession.Via.PASSWORD)
    return user


def verify_password(request: HttpRequest, *, email: str, password: str) -> User:
    """The account with this address and password, active or not, without signing in.

    For accepting an invitation with an existing account (D 6.2): the same lockout per
    email and address as signing in (429), a wrong password or an unknown address counts
    for axes, the address and the log exactly as at sign-in (401). A deactivated account
    passes here, so it comes back only with its own password; plain `login` keeps
    refusing it. Runs outside a transaction: the recorded failure must survive the 401.
    """
    _refuse_when_locked(request, email)
    account = User.objects.filter(email=normalise_email(email)).first()
    if account is None:
        # Hash anyway, so an unknown address takes as long as a wrong password.
        User().set_password(password)
    if account is None or not account.check_password(password):
        with _checking_password_for("invitation"):
            user_login_failed.send(
                sender=__name__, credentials={"username": email}, request=request
            )
        LOGIN_FAILURES.record_failure(request)
        _record_sign_in_problem(AuditCode.SIGN_IN_FAILED, request, email)
        raise ApiError(401, "invalid_credentials", _("Het e-mailadres of wachtwoord klopt niet."))
    return account


def _refuse_when_locked(request: HttpRequest, email: str) -> None:
    """429 while axes holds this email and address locked (after 5 wrong passwords)."""
    if AxesProxyHandler.is_locked(request, {"username": email}):
        cooloff = int(settings.AXES_COOLOFF_TIME.total_seconds())
        raise ApiError(
            429,
            "too_many_attempts",
            _("Te veel pogingen. Probeer het later opnieuw."),
            headers={"Retry-After": str(cooloff)},
        )


def _record_sign_in_problem(
    code: AuditCode,
    request: HttpRequest,
    email: str,
    values: dict[str, str] | None = None,
) -> None:
    """A failed or locked sign-in, about the account the email belongs to.

    For an unknown address the row names nobody: the typed email is never stored. In its
    own block, ended before the error answer is raised.
    """
    account = User.objects.filter(email=normalise_email(email)).first()
    address = valid_client_ip(request)
    with transaction.atomic():
        if account is None:
            actor = AuditActor(kind=ActorKind.USER, user_id=None, name="", client_ip=address)
            record(code, actor=actor, values=values)
            return
        record(
            code,
            actor=AuditActor.for_user(account, client_ip=address),
            target=AuditTarget(kind=TargetKind.USER, id=account.id, name=account.name),
            values=values,
        )


def on_user_locked_out(
    sender: object, request: HttpRequest | None = None, username: str = "", **kwargs: object
) -> None:
    """axes' `user_locked_out` receiver: one `sign_in_locked_out` row per lockout (the
    refused attempts after it are answered before axes is asked again), saying whether
    signing in or changing the password caused it."""
    if request is None:
        return
    values = {"via": _password_check.get()}
    _record_sign_in_problem(AuditCode.SIGN_IN_LOCKED_OUT, request, username, values)


def logout(request: HttpRequest) -> None:
    with transaction.atomic():
        UserSession.objects.filter(session_key=request.session.session_key).delete()
        django_logout(request)


def axes_username(request: HttpRequest, credentials: dict[str, object] | None) -> str:
    """Key axes' records on the normalised email, so case variants share one lockout."""
    username = credentials.get("username") if credentials else None
    return normalise_email(username) if isinstance(username, str) else ""


@dataclass(frozen=True)
class SessionView:
    id: UUID
    method: str
    started_at: datetime
    last_used_at: datetime
    client_ip: str
    browser: str
    current: bool


def list_my_sessions(request: HttpRequest) -> list[SessionView]:
    """The caller's web sessions, the most recently used first."""
    user = current_user(request)
    key = request.session.session_key
    rows = UserSession.objects.filter(user=user).order_by("-last_used_at", "id")
    return [
        SessionView(
            id=row.id,
            method=row.method,
            started_at=row.created_at,
            last_used_at=row.last_used_at,
            client_ip=row.client_ip or "",
            browser=row.browser,
            current=row.session_key == key,
        )
        for row in rows
    ]


def end_my_session(request: HttpRequest, *, session_id: UUID) -> None:
    """End one of the caller's own web sessions; the next request of that browser is 401.

    The current one is ended through `end_current_session` (a flush, so the response
    is a clean 204). Somebody else's session answers 404, as an unknown one does.
    """
    user = current_user(request)
    with transaction.atomic():
        row = UserSession.objects.select_for_update().filter(user=user, id=session_id).first()
        if row is None:
            raise ApiError(404, "session_not_found", _("Deze sessie bestaat niet meer."))
        record(
            AuditCode.SESSION_ENDED,
            actor=actor_from_request(request),
            target=AuditTarget(kind=TargetKind.SESSION, id=row.id),
            values={"count": 1, "reason": "self"},
        )
        if row.session_key == request.session.session_key:
            end_current_session(request)
            return
        Session.objects.filter(session_key=row.session_key).delete()
        row.delete()


def reauthenticate(request: HttpRequest, *, password: str) -> None:
    """Check the caller's password again and note it on this web session, for what needs
    a recent proof of who is at the browser (`sessions.recently_authenticated`).

    Checked through `authenticate` like `change_password`, so wrong passwords count for
    the same lockouts as signing in.
    """
    user = current_user(request)
    _refuse_when_locked(request, user.email)
    with _checking_password_for("reauthenticate"):
        checked = authenticate(request, username=user.email, password=password)
    if not isinstance(checked, User) or checked.pk != user.pk:
        raise ApiError(400, "wrong_password", _("Je wachtwoord klopt niet."))
    mark_reauthenticated(request)


def change_password(request: HttpRequest, *, current_password: str, new_password: str) -> None:
    """Change the caller's password; every other web session ends, this one stays.

    The current password is checked through `authenticate`, so django-axes counts and
    locks wrong ones per email and address exactly as at sign-in. That check runs outside
    the transaction below: the failure axes recorded survives the 400.
    """
    user = current_user(request)
    _refuse_when_locked(request, user.email)
    with _checking_password_for("password_change"):
        checked = authenticate(request, username=user.email, password=current_password)
    if not isinstance(checked, User) or checked.pk != user.pk:
        raise ApiError(400, "wrong_password", _("Je huidige wachtwoord klopt niet."))
    with transaction.atomic():
        validate_password(new_password, user)
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])
        old_key = request.session.session_key
        # Cycles the session key and keeps this session signed in.
        update_session_auth_hash(request, user)
        if old_key is not None:
            rekey_session(request, old_key)
        ended = end_sessions(user, except_key=request.session.session_key)
        actor = actor_from_request(request)
        target = AuditTarget(kind=TargetKind.USER, id=user.id, name=user.name)
        record(AuditCode.PASSWORD_CHANGED, actor=actor, target=target, values={"via": "self"})
        if ended:
            record(
                AuditCode.SESSION_ENDED,
                actor=actor,
                target=target,
                values={"count": ended, "reason": "password_changed"},
            )
