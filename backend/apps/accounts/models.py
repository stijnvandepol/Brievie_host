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
    class Language(models.TextChoices):
        # Each language in its own name, so a reader finds theirs; not translated.
        DUTCH = "nl", "Nederlands"
        ENGLISH = "en", "English"

    class ExplanationLanguage(models.TextChoices):
        # Each language in its own name, so a reader finds theirs; not translated.
        DUTCH = "nl", "Nederlands"
        ENGLISH = "en", "English"

    # Stored in lower case (create_user and clean() normalise; a check constraint
    # rejects anything else), so the unique index ignores case.
    email = models.EmailField(max_length=254, unique=True)
    name = models.CharField(max_length=150)
    # The language of the interface and of the server's messages, chosen by the user;
    # "" until they choose: then the browser's or device's Accept-Language decides. The
    # column also has a database default '' (migration 0012) for raw inserts.
    language = models.CharField(max_length=2, choices=Language.choices, blank=True, default="")
    explanation_language = models.CharField(
        max_length=2, choices=ExplanationLanguage.choices, default=ExplanationLanguage.DUTCH
    )
    is_active = models.BooleanField(default=True)
    # Runs the server itself (members, backups, audit log), apart from any workspace role.
    is_server_admin = models.BooleanField(default=False)
    # Since when a rule asks this account for a second factor (two-step sign-in, S 5):
    # the grace period counts from here. Null while no rule asks it.
    second_factor_required_since = models.DateTimeField(null=True, blank=True)

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
    # When the password was last typed again in this session (`reauthenticate`); with
    # `created_at` it says how recently the person at this browser proved who they are.
    reauthenticated_at = models.DateTimeField(null=True, blank=True)
    # True when this sign-in passed the second step (a code or a recovery code) or came
    # through the organisation's sign-in (SSO), which does its own.
    second_factor = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"Session of {self.user_id}"


class SecondFactor(BaseModel):
    """An account's authenticator app (two-step sign-in, S 2). A server table, like
    `UserSession`: accounts are not workspace content.

    The secret is sealed under a key derived from the master key, bound to the account
    (`apps.common.crypto.second_factor_aad`). Unconfirmed (`confirmed_at` null) while the
    set-up runs; only a confirmed factor asks for a code at sign-in.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="second_factor")
    secret_sealed = models.BinaryField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # The last step whose code was used: a code is accepted only for a later step.
    last_used_step = models.BigIntegerField(default=0)
    # Wrong codes since `first_failed_at` (per account, whatever the address).
    failed_count = models.IntegerField(default=0)
    first_failed_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    # How often the account was locked in a row: the lock doubles each time.
    lockouts = models.IntegerField(default=0)
    # A new phone being set up ("Nieuwe telefoon"): the old secret works until the new
    # one is confirmed.
    pending_secret_sealed = models.BinaryField(null=True, blank=True)
    pending_since = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Second factor of {self.user_id}"


class RecoveryCode(BaseModel):
    """One recovery code of an account, as a keyed hash (two-step sign-in, S 2); usable
    once."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=64, unique=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Recovery code of {self.user_id}"


class AccountNotice(BaseModel):
    """Something another person did to this account, or a new device on it, shown once in
    the web app (D 6.3)."""

    class Code(models.TextChoices):
        PASSWORD_RESET_CREATED = "password_reset_created", _("Herstellink gemaakt")
        PASSWORD_RESET_COMPLETED = "password_reset_completed", _("Wachtwoord opnieuw ingesteld")
        PASSWORD_RESET_BY_COMMAND = (
            "password_reset_by_command",
            _("Wachtwoord opnieuw ingesteld op de server"),
        )
        SESSIONS_ENDED = "sessions_ended", _("Sessies beëindigd")
        DEVICE_REVOKED = "device_revoked", _("Apparaat ingetrokken")
        DEVICE_PAIRED = "device_paired", _("Apparaat gekoppeld")
        SECOND_FACTOR_CLEARED = "second_factor_cleared", _("Tweestapsverificatie gewist")
        SECOND_FACTOR_CLEARED_BY_COMMAND = (
            "second_factor_cleared_by_command",
            _("Tweestapsverificatie gewist op de server"),
        )
        # Somebody had the password but not the code, until the second step locked.
        SECOND_FACTOR_LOCKED = "second_factor_locked", _("Inlogpoging zonder code")
        SECOND_FACTOR_REQUIRED = "second_factor_required", _("Tweestapsverificatie verplicht")
        SECOND_FACTOR_RECOVERY_USED = "second_factor_recovery_used", _("Herstelcode gebruikt")
        SERVER_ADMIN_GRANTED = "server_admin_granted", _("Serverbeheerder gemaakt")
        SERVER_ADMIN_REVOKED = "server_admin_revoked", _("Geen serverbeheerder meer")
        # Part B sends these four; nothing in part A does.
        EMERGENCY_ACCESS_SWITCHED_ON = "emergency_access_switched_on", _("Noodtoegang aangezet")
        EMERGENCY_ACCESS_SWITCHED_OFF = "emergency_access_switched_off", _("Noodtoegang uitgezet")
        EMERGENCY_ACCESS_USED = "emergency_access_used", _("Noodtoegang gebruikt")
        PRIVATE_WITHOUT_READERS = "private_without_readers", _("Privépost zonder lezers")
        # The same, for an admin who may not see the letter: it is not named.
        PRIVATE_WITHOUT_READERS_UNNAMED = (
            "private_without_readers_unnamed",
            _("Privépost zonder lezers, brief niet genoemd"),
        )
        # Who sees what changed (S 4.4, 4.14): the member sees more, or less, post.
        SIGHT_WIDENED = "sight_widened", _("Je ziet meer post")
        SIGHT_NARROWED = "sight_narrowed", _("Je ziet minder post")
        # Admins now read all post that is not private: every member is told (R37).
        ADMINS_READ_ALL_ON = "admins_read_all_on", _("Beheerders lezen alle post")
        # An admin gave themselves sight (S 4.7): the department's members, or the other
        # admins, are told.
        SELF_GRANT = "self_grant", _("Iemand gaf zichzelf toegang")

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notices")
    code = models.CharField(max_length=40, choices=Code.choices)
    # The name of who did it, as it was then.
    actor_name = models.CharField(max_length=150, blank=True)
    values = models.JSONField(default=dict, blank=True)
    seen_at = models.DateTimeField(null=True, blank=True)
    # The workspace the notice is about (navigation spec S 8): shown only there. Null for
    # a notice about the account itself (and for notices from before this field).
    workspace = models.ForeignKey(
        "workspaces.Workspace", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )

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


class SignInFailures(BaseModel):
    """Wrong passwords per account, whatever address they came from
    (`apps.accounts.signin_limits`). Keyed on a hash of the normalised email, so an
    address without an account is counted alike and no email is stored."""

    account_key = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField()
    last_failure_at = models.DateTimeField(db_index=True)

    def __str__(self) -> str:
        return f"{self.failures} failures"
