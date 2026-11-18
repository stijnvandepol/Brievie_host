import hashlib
import hmac
import secrets
from collections.abc import Sequence

from axes.handlers.proxy import AxesProxyHandler
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connection, transaction
from django.http import HttpRequest
from django.utils.translation import gettext as _

from apps.accounts.models import SetupToken, User, normalise_email
from apps.common.errors import ApiError
from apps.workspaces.models import Membership
from apps.workspaces.services import create_workspace

# pg_advisory_xact_lock key that serialises POST /setup and the generation of the
# setup token ("brievie" in ASCII).
SETUP_LOCK_KEY = 0x62726965766965
MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"


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
        email = normalise_email(email)
        validate_email(email)
        user = User(email=email, name=_required(name, _("Vul je naam in.")), is_server_admin=True)
        validate_password(password, user)
        user.set_password(password)
        user.save()
        workspace = create_workspace(
            name=_required(workspace_name, _("Geef de werkruimte een naam.")),
            kind=workspace_kind,
            admin=user,
            extra_labels=extra_labels,
        )
        SetupToken.objects.all().delete()
        django_login(request, user, backend=MODEL_BACKEND)
    return Membership.objects.select_related("workspace").get(user=user, workspace=workspace)


def lock_active_server_admins() -> list[User]:
    """Lock the active server admins, always in id order so two callers cannot deadlock.

    Every check of "is there another server admin?" takes this lock first, so two
    requests that each remove one of the last two admins run one after the other.
    Must run inside a transaction.
    """
    return list(
        User.objects.select_for_update().filter(is_server_admin=True, is_active=True).order_by("id")
    )


def other_active_server_admin_exists(user: User) -> bool:
    """Whether an active server admin other than `user` exists (takes the lock above)."""
    return any(admin.id != user.id for admin in lock_active_server_admins())


def login(request: HttpRequest, *, email: str, password: str) -> User:
    """Log in with email and password, with django-axes' lockout per email + address.

    The password check runs outside `transaction.atomic`: a failure must keep the
    attempt that axes recorded, and the 401 response commits the request transaction.
    """
    credentials = {"username": email}
    if AxesProxyHandler.is_locked(request, credentials):
        cooloff = int(settings.AXES_COOLOFF_TIME.total_seconds())
        raise ApiError(
            429,
            "too_many_attempts",
            _("Te veel pogingen. Probeer het later opnieuw."),
            headers={"Retry-After": str(cooloff)},
        )
    user = authenticate(request, username=email, password=password)
    if not isinstance(user, User):
        raise ApiError(401, "invalid_credentials", _("Het e-mailadres of wachtwoord klopt niet."))
    with transaction.atomic():
        django_login(request, user)
    return user


def logout(request: HttpRequest) -> None:
    with transaction.atomic():
        django_logout(request)


def axes_username(request: HttpRequest, credentials: dict[str, object] | None) -> str:
    """Key axes' records on the normalised email, so case variants share one lockout."""
    username = credentials.get("username") if credentials else None
    return normalise_email(username) if isinstance(username, str) else ""
