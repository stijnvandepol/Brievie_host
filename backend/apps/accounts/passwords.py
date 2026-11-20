"""Django's password rules with their sentences in Dutch.

Django's Dutch catalogue lags behind Django itself: the minimum length message changed
its msgid in Django 6 and reached users in English, and the similarity message says
"de email". These subclasses keep Django's checks and give each one its own Dutch
sentence through gettext, so no rule depends on that catalogue. Configured in
`AUTH_PASSWORD_VALIDATORS`; tests/test_accounts_passwords.py fails for any other.
"""

from django.contrib.auth import password_validation
from django.utils.translation import gettext as _


class MinimumLengthValidator(password_validation.MinimumLengthValidator):
    def get_error_message(self) -> str:
        return _("Dit wachtwoord is te kort. Gebruik minstens %(min)d tekens.") % {
            "min": self.min_length
        }

    def get_help_text(self) -> str:
        return _("Gebruik minstens %(min)d tekens.") % {"min": self.min_length}


class UserAttributeSimilarityValidator(password_validation.UserAttributeSimilarityValidator):
    # The user's own fields (Django's defaults name fields this user model lacks).
    def __init__(
        self, user_attributes: tuple[str, ...] = ("name", "email"), max_similarity: float = 0.7
    ) -> None:
        super().__init__(user_attributes=user_attributes, max_similarity=max_similarity)

    def get_error_message(self) -> str:
        return _("Dit wachtwoord lijkt te veel op je naam of e-mailadres.")

    def get_help_text(self) -> str:
        return _("Het wachtwoord mag niet te veel op je naam of e-mailadres lijken.")


class CommonPasswordValidator(password_validation.CommonPasswordValidator):
    def get_error_message(self) -> str:
        return _("Dit wachtwoord is te algemeen. Kies een ander.")

    def get_help_text(self) -> str:
        return _("Kies geen wachtwoord dat veel mensen gebruiken.")


class NumericPasswordValidator(password_validation.NumericPasswordValidator):
    def get_error_message(self) -> str:
        return _("Dit wachtwoord bevat alleen cijfers. Gebruik ook letters.")

    def get_help_text(self) -> str:
        return _("Gebruik niet alleen cijfers.")
