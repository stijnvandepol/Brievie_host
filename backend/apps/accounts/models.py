from typing import ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel


def normalise_email(email: str) -> str:
    return email.strip().lower()


class UserManager(BaseUserManager["User"]):
    def create_user(
        self, email: str, password: str | None = None, *, name: str, **extra: bool | str
    ) -> "User":
        user = self.model(email=normalise_email(email), name=name, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def get_by_natural_key(self, username: str | None) -> "User":
        # Login looks users up here; addresses are stored in lower case.
        return super().get_by_natural_key(normalise_email(username or ""))


class User(AbstractBaseUser, PermissionsMixin, BaseModel):
    class ExplanationLanguage(models.TextChoices):
        # Each language in its own name, so a reader finds theirs; not translated.
        DUTCH = "nl", "Nederlands"
        ENGLISH = "en", "English"

    # Stored in lower case (create_user and clean() normalise; a check constraint
    # rejects anything else), so the unique index ignores case.
    email = models.EmailField(max_length=254, unique=True)
    name = models.CharField(max_length=150)
    language = models.CharField(max_length=10, default="nl")
    explanation_language = models.CharField(
        max_length=2, choices=ExplanationLanguage.choices, default=ExplanationLanguage.DUTCH
    )
    is_active = models.BooleanField(default=True)
    # Runs the server itself (members, backups, audit log), apart from any workspace role.
    is_server_admin = models.BooleanField(default=False)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = ["name"]

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(email=Lower("email")), name="accounts_user_email_lower_case"
            ),
        ]

    def __str__(self) -> str:
        return self.email

    def clean(self) -> None:
        super().clean()
        self.email = normalise_email(self.email)


class SetupToken(BaseModel):
    """Hash of the generated first-run setup code. At most one row; gone after setup."""

    token_hash = models.CharField(max_length=64)
    # Always True; the unique constraint keeps the table at one row.
    singleton = models.BooleanField(default=True, editable=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["singleton"], name="accounts_setuptoken_singleton"),
            models.CheckConstraint(
                condition=models.Q(singleton=True), name="accounts_setuptoken_singleton_true"
            ),
        ]

    def __str__(self) -> str:
        return "Setup token"


class UserSession(BaseModel):
    """One signed-in web session (D 7.2): Django's session row plus what the user sees.

    A session without this row is signed out by `UserSessionMiddleware`. The key is
    stored plain, as Django's own session table holds it. `created_at` is the start.
    """

    class Method(models.TextChoices):
        PASSWORD = "password", _("Wachtwoord")
        SSO = "sso", _("Inloggen via organisatie")

    class Via(models.TextChoices):
        PASSWORD = "password", _("Inloggen")
        SETUP = "setup", _("Installatie")
        INVITATION = "invitation", _("Uitnodiging")
        RESET = "reset", _("Wachtwoord opnieuw ingesteld")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="web_sessions")
    session_key = models.CharField(max_length=40, unique=True)
    method = models.CharField(max_length=10, choices=Method.choices)
    created_via = models.CharField(max_length=20, choices=Via.choices, default=Via.PASSWORD)
    last_used_at = models.DateTimeField()
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    # A short description such as "Firefox op Windows", never the raw user agent.
    browser = models.CharField(max_length=100, blank=True)

    def __str__(self) -> str:
        return f"Session of {self.user_id}"


class AccountNotice(BaseModel):
    """Something another person did to this account, shown once in the web app (D 6.3)."""

    class Code(models.TextChoices):
        PASSWORD_RESET_CREATED = "password_reset_created", _("Herstellink gemaakt")
        PASSWORD_RESET_COMPLETED = "password_reset_completed", _("Wachtwoord opnieuw ingesteld")
        PASSWORD_RESET_BY_COMMAND = (
            "password_reset_by_command",
            _("Wachtwoord opnieuw ingesteld op de server"),
        )
        SESSIONS_ENDED = "sessions_ended", _("Sessies beëindigd")
        DEVICE_REVOKED = "device_revoked", _("Apparaat ingetrokken")
        SECOND_FACTOR_CLEARED = "second_factor_cleared", _("Tweestapsverificatie gewist")
        # Part B sends these four; nothing in part A does.
        EMERGENCY_ACCESS_SWITCHED_ON = "emergency_access_switched_on", _("Noodtoegang aangezet")
        EMERGENCY_ACCESS_SWITCHED_OFF = "emergency_access_switched_off", _("Noodtoegang uitgezet")
        EMERGENCY_ACCESS_USED = "emergency_access_used", _("Noodtoegang gebruikt")
        PRIVATE_WITHOUT_READERS = "private_without_readers", _("Privépost zonder lezers")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notices")
    code = models.CharField(max_length=40, choices=Code.choices)
    # The name of who did it, as it was then.
    actor_name = models.CharField(max_length=150, blank=True)
    values = models.JSONField(default=dict, blank=True)
    seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "seen_at"], name="accounts_notice_unseen"),
        ]

    def __str__(self) -> str:
        return f"{self.code} for {self.user_id}"


class PasswordReset(BaseModel):
    """A one-time link with which a member chooses a new password (D 6.3).

    Made by an admin who may act on the account (`apps.accounts.authority`); valid 24
    hours. Only the SHA-256 of the token is stored: the token is in the link's fragment.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_resets")
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # The name of who made it, as it was then (for the notice after the reset).
    created_by_name = models.CharField(max_length=150)
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Password reset for {self.user_id}"
