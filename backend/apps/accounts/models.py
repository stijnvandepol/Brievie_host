from typing import ClassVar

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models.functions import Lower

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
