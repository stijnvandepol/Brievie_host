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

    def create_superuser(self, email: str, password: str, *, name: str) -> "User":
        return self.create_user(email, password, name=name, is_staff=True, is_superuser=True)


class User(AbstractBaseUser, PermissionsMixin, BaseModel):
    class ExplanationLanguage(models.TextChoices):
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
    is_staff = models.BooleanField(default=False)

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
