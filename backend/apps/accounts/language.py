"""Which language the server answers in (DECISIONS 2026-11-21: English).

Before sign-in, Django's `LocaleMiddleware` picks it from `Accept-Language` (Dutch when
the header names neither language). Once the caller is known, the account's own choice
wins: `AccountLanguageMiddleware` for a web session, `DeviceTokenAuth` for a device. An
account without a choice ("") keeps the header's language.
"""

from django.http import HttpRequest
from django.utils import translation

from apps.accounts.models import User

LANGUAGES = frozenset(User.Language.values)


def activate_account_language(request: HttpRequest, user: User) -> None:
    """Answer in the account's chosen language, when it chose one."""
    if user.language in LANGUAGES:
        translation.activate(user.language)
        request.LANGUAGE_CODE = user.language
